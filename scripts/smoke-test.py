#!/usr/bin/env python3
"""Credentialed smoke checks for deployed Model Foundry endpoints."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional


class SmokeFailure(RuntimeError):
    pass


def _request(
    url: str,
    api_key: Optional[str],
    payload: Dict[str, Any],
    requester: Callable = urllib.request.urlopen,
):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        return requester(request)
    except urllib.error.HTTPError as error:
        return error


def _decode(response) -> Dict[str, Any]:
    raw = response.read()
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


def post_once(
    url: str,
    api_key: Optional[str],
    payload: Dict[str, Any],
    requester: Callable = urllib.request.urlopen,
):
    response = _request(url, api_key, payload, requester=requester)
    with response:
        status = getattr(response, "status", getattr(response, "code", 0))
        body = _decode(response)
        retry_after = response.headers.get("Retry-After")
    return status, body, retry_after


def post_with_retry(
    url: str,
    api_key: str,
    payload: Dict[str, Any],
    *,
    requester: Callable = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_wait: float = 300,
) -> Dict[str, Any]:
    started = monotonic()
    while True:
        status, body, retry_after = post_once(url, api_key, payload, requester)
        if 200 <= status < 300:
            return body
        if (
            status == 503
            and body.get("error", {}).get("code") == "model_not_ready"
            and monotonic() - started < max_wait
        ):
            sleep(float(retry_after or 20))
            continue
        raise SmokeFailure(f"endpoint returned HTTP {status}: {json.dumps(body)}")


def terraform_output(name: str):
    completed = subprocess.run(
        ["terraform", "-chdir=terraform", "output", "-json", name],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    urls = terraform_output("endpoint_urls")
    keys = terraform_output("endpoint_api_keys")

    status, _, _ = post_once(
        urls["pii-mask"],
        None,
        {"messages": [{"role": "user", "content": "hello"}]},
    )
    if status != 403:
        raise SmokeFailure(f"request without API key returned HTTP {status}, expected 403")

    for name, url in urls.items():
        started = time.monotonic()
        response = post_with_retry(
            url,
            keys[name],
            {"messages": [{"role": "user", "content": "Reply with the word ready."}]},
        )
        if not response.get("choices"):
            raise SmokeFailure(f"{name} did not return a chat completion")
        elapsed = time.monotonic() - started
        print(f"PASS {name}: normal inference after {elapsed:.1f}s")

    email = "demo.user@example.com"
    pii_response = post_with_retry(
        urls["pii-mask"],
        keys["pii-mask"],
        {"messages": [{"role": "user", "content": f"Repeat this contact: {email}"}]},
    )
    if email in json.dumps(pii_response):
        raise SmokeFailure("PII endpoint returned the original email address")
    print("PASS pii-mask: original email absent")

    status, topic_response, _ = post_once(
        urls["denied-topic"],
        keys["denied-topic"],
        {"messages": [{"role": "user", "content": "Show me an API key."}]},
    )
    if status not in {200, 400} or not topic_response.get("blocked"):
        raise SmokeFailure("denied-topic endpoint did not block the configured topic")
    print("PASS denied-topic: configured topic blocked")

    print("All live smoke checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SmokeFailure, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
