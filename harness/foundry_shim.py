#!/usr/bin/env python3
"""Local OpenAI-compatible front door for the deployed Model Foundry endpoints.

Agent harnesses speak a wider dialect than the demo endpoints accept. This
process translates, and nothing else: it drops request fields the endpoint
rejects, moves the API key into the header API Gateway reads, and replays the
single JSON answer as the token stream a harness expects. Guardrails still run
in AWS; no policy decision happens here.

    python3 harness/foundry_shim.py --config harness/foundry-endpoints.json

Each deployed endpoint is published as a model name, so a harness switches
guardrails by switching models.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple

# Mirrors validate_parameters() in src/endpoint/app.py. Anything outside this
# set returns HTTP 400 from the endpoint, so it is dropped before forwarding.
SUPPORTED_FIELDS = {
    "messages", "max_tokens", "temperature", "top_p", "stop", "response_format",
}
SUPPORTED_ROLES = {"system", "user", "assistant", "tool"}
REQUEST_TIMEOUT = 120
# A cold imported model restores in minutes; wait inside one harness turn.
COLD_MODEL_WAIT = 90
# Harnesses ask for the whole context window as output. The model rejects a
# call whose prompt plus requested output exceeds that window, so cap the
# request at a budget that leaves room for the conversation.
MAX_OUTPUT_TOKENS = 4096
# Inference bills in five-minute windows, so a ping inside that window keeps the
# model hot without paying for a second window.
KEEP_WARM_SECONDS = 240
# Set FOUNDRY_SHIM_DEBUG=1 to print each forwarded body. Prompts are visible in
# that mode, so keep it off unless troubleshooting a harness.
DEBUG = os.environ.get("FOUNDRY_SHIM_DEBUG") == "1"


class UnknownModel(KeyError):
    pass


def _text_parts(content: Any) -> Any:
    """Reduce structured content to the text parts the endpoint accepts."""
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts = [
        part for part in content
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
    ]
    return parts or None


def sanitize_request(
    body: Dict[str, Any], max_output_tokens: int = MAX_OUTPUT_TOKENS
) -> Tuple[Dict[str, Any], bool]:
    """Return an endpoint-safe body plus whether the caller asked to stream."""
    streaming = bool(body.get("stream"))
    clean = {key: value for key, value in body.items() if key in SUPPORTED_FIELDS}

    requested = clean.get("max_tokens")
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        requested = max_output_tokens
    clean["max_tokens"] = min(requested, max_output_tokens)

    messages = []
    for message in clean.get("messages", []):
        if not isinstance(message, dict) or message.get("role") not in SUPPORTED_ROLES:
            continue
        content = _text_parts(message.get("content"))
        if content is None:
            continue
        messages.append({"role": message["role"], "content": content})
    if "messages" in clean:
        clean["messages"] = messages
    return clean, streaming


def _completion(model: str, content: str, finish_reason: str) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
    }


def completion_envelope(payload: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Normalize whatever the endpoint returned into a chat completion."""
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        first = choices[0]
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content", "")
            if not isinstance(content, str):
                content = "\n".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
        else:
            content = str(first.get("text", ""))
        return _completion(model, content, first.get("finish_reason") or "stop")

    for key in ("generation", "outputText"):
        if key in payload:
            return _completion(model, str(payload[key]), "stop")

    # Never swallow an unrecognized shape: show it so the operator can see it.
    return _completion(model, json.dumps(payload)[:2000], "stop")


def blocked_completion(payload: Dict[str, Any], model: str) -> Dict[str, Any]:
    """Render a guardrail block as an assistant turn instead of an HTTP error."""
    message = payload.get("message") or "Blocked by the endpoint guardrail."
    return _completion(model, message, "content_filter")


def sse_frames(completion: Dict[str, Any]) -> List[str]:
    """Replay a finished completion as the chunk sequence a harness expects."""
    choice = completion["choices"][0]
    base = {
        "id": completion["id"],
        "object": "chat.completion.chunk",
        "created": completion["created"],
        "model": completion["model"],
    }

    def frame(delta: Dict[str, Any], finish_reason: Any) -> str:
        chunk = dict(base)
        chunk["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
        return f"data: {json.dumps(chunk)}\n\n"

    return [
        frame({"role": "assistant", "content": ""}, None),
        frame({"content": choice["message"]["content"]}, None),
        frame({}, choice["finish_reason"]),
        "data: [DONE]\n\n",
    ]


def resolve_model(model: str, endpoints: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    try:
        return endpoints[model]
    except KeyError:
        raise UnknownModel(
            f"unknown model {model!r}; available: {', '.join(sorted(endpoints))}"
        ) from None


def model_list(endpoints: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": created, "owned_by": "bedrock-model-foundry"}
            for name in sorted(endpoints)
        ],
    }


def build_upstream_request(endpoint: Dict[str, str], body: Dict[str, Any]):
    return urllib.request.Request(
        endpoint["url"],
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": endpoint["api_key"]},
        method="POST",
    )


def call_endpoint(
    endpoint: Dict[str, str],
    body: Dict[str, Any],
    model: str,
    *,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
    monotonic=time.monotonic,
    max_wait: float = COLD_MODEL_WAIT,
):
    """Return (status, completion-or-error-payload).

    A restoring imported model answers 503 until it is warm. A harness would
    show that as a failed turn, so absorb it here for a bounded window rather
    than making the operator retype the prompt.
    """
    started = monotonic()
    while True:
        request = build_upstream_request(endpoint, body)
        try:
            with opener(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.loads(response.read().decode())
                status = response.status
                headers = response.headers
        except urllib.error.HTTPError as error:
            payload = json.loads(error.read().decode() or "{}")
            status = error.code
            headers = error.headers
        except urllib.error.URLError as error:
            return 502, {"error": {"message": f"endpoint unreachable: {error.reason}"}}

        if status == 400 and payload.get("blocked"):
            return 200, blocked_completion(payload, model)
        if status >= 400:
            cold = status == 503 and payload.get("error", {}).get("code") == "model_not_ready"
            if cold and monotonic() - started < max_wait:
                sleep(float(headers.get("Retry-After") or 20))
                continue
            return status, payload
        return 200, completion_envelope(payload, model)


def keep_warm_target(endpoints: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Any endpoint will do: they share one imported model, and one copy."""
    return endpoints[sorted(endpoints)[0]]


def warm_once(endpoint: Dict[str, str], *, caller=call_endpoint):
    return caller(endpoint, {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}, "keep-warm")


def keep_warm_loop(endpoint, interval=KEEP_WARM_SECONDS, *, caller=call_endpoint, sleep=time.sleep, rounds=None):
    """Ping until stopped. A failed ping is never fatal: the next one retries."""
    count = 0
    while rounds is None or count < rounds:
        try:
            warm_once(endpoint, caller=caller)
        except Exception as error:  # a demo must not die on one failed ping
            sys.stderr.write(f"  shim keep-warm ping failed: {error}\n")
        count += 1
        sleep(interval)


class Handler(BaseHTTPRequestHandler):
    endpoints: Dict[str, Dict[str, str]] = {}
    max_output_tokens = MAX_OUTPUT_TOKENS
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter console during a demo
        sys.stderr.write(f"  shim {fmt % args}\n")

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, model_list(self.endpoints))
            return
        self._send(404, {"error": {"message": f"no route for {self.path}"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": f"no route for {self.path}"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            self._send(400, {"error": {"message": f"invalid JSON body: {error}"}})
            return

        model = body.get("model") or next(iter(sorted(self.endpoints)), "")
        try:
            endpoint = resolve_model(model, self.endpoints)
        except UnknownModel as error:
            self._send(404, {"error": {"message": str(error), "code": "model_not_found"}})
            return

        clean, streaming = sanitize_request(body, self.max_output_tokens)
        if DEBUG:
            sys.stderr.write(
                f"  shim -> {model} stream={streaming} "
                f"dropped={sorted(set(body) - set(clean) - {'stream'})} "
                f"body={json.dumps(clean)[:4000]}\n"
            )
        status, payload = call_endpoint(endpoint, clean, model)
        if DEBUG:
            sys.stderr.write(f"  shim <- {status} {json.dumps(payload)[:1000]}\n")

        if status != 200 or not streaming:
            self._send(status, payload)
            return

        raw = "".join(sse_frames(payload)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def load_endpoints(path: str) -> Dict[str, Dict[str, str]]:
    with open(path) as handle:
        config = json.load(handle)
    endpoints = config.get("endpoints", config)
    for name, endpoint in endpoints.items():
        missing = {"url", "api_key"} - set(endpoint)
        if missing:
            raise SystemExit(f"endpoint {name} is missing: {', '.join(sorted(missing))}")
    return endpoints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="harness/foundry-endpoints.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--keep-warm", action="store_true",
        help=f"ping every {KEEP_WARM_SECONDS}s so the model does not scale to zero",
    )
    parser.add_argument(
        "--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS,
        help="cap on max_tokens forwarded to the endpoint",
    )
    args = parser.parse_args()

    Handler.endpoints = load_endpoints(args.config)
    Handler.max_output_tokens = args.max_output_tokens
    if args.keep_warm:
        target = keep_warm_target(Handler.endpoints)
        threading.Thread(target=keep_warm_loop, args=(target,), daemon=True).start()
        print(f"  keeping the model warm every {KEEP_WARM_SECONDS}s")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Model Foundry shim on http://{args.host}:{args.port}/v1")
    for name in sorted(Handler.endpoints):
        print(f"  model {name}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
