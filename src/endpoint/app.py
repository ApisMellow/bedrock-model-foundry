"""API Gateway Lambda proxy for guarded imported-model inference."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple


_client = None
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def runtime_client():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION"),
            config=Config(retries={"mode": "standard", "total_max_attempts": 2}),
        )
    return _client


def _response(status: int, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
    response_headers = {"Content-Type": "application/json"}
    if headers:
        response_headers.update(headers)
    return {
        "statusCode": status,
        "headers": response_headers,
        "body": json.dumps(payload),
    }


def _log(request_id: str, status: int, stage: str, code: str = "") -> None:
    logger.info(json.dumps({
        "request_id": request_id,
        "status": status,
        "stage": stage,
        "error_code": code,
    }))


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", {})
    return response.get("Error", {}).get("Code", "")


def extract_messages(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system", "user", "assistant", "tool"
        }:
            raise ValueError("each message must contain a supported role")
        content = message.get("content")
        if isinstance(content, str) and content:
            continue
        if not isinstance(content, list) or not content:
            raise ValueError("message content must be text or an array of text parts")
        if any(
            not isinstance(part, dict)
            or part.get("type") != "text"
            or not isinstance(part.get("text"), str)
            or not part.get("text")
            for part in content
        ):
            raise ValueError("only text content parts are supported")
    return messages


def validate_parameters(body: Dict[str, Any]) -> None:
    supported = {
        "messages", "model", "max_tokens", "temperature", "top_p", "stop",
        "response_format",
    }
    unsupported = sorted(set(body) - supported)
    if unsupported:
        raise ValueError("unsupported request fields: " + ", ".join(unsupported))

    if "model" in body and (not isinstance(body["model"], str) or not body["model"]):
        raise ValueError("model must be a non-empty string when provided")

    if "max_tokens" in body:
        value = body["max_tokens"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 32768:
            raise ValueError("max_tokens must be an integer from 1 through 32768")

    for name, lower, upper in (("temperature", 0, 2), ("top_p", 0, 1)):
        if name not in body:
            continue
        value = body[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number")
        if name == "top_p" and not lower < value <= upper:
            raise ValueError("top_p must be greater than 0 and at most 1")
        if name == "temperature" and not lower <= value <= upper:
            raise ValueError("temperature must be from 0 through 2")

    if "stop" in body:
        value = body["stop"]
        valid = isinstance(value, str) and bool(value)
        if isinstance(value, list):
            valid = 1 <= len(value) <= 4 and all(
                isinstance(item, str) and bool(item) for item in value
            )
        if not valid:
            raise ValueError("stop must be a string or one to four non-empty strings")

    if "response_format" in body:
        value = body["response_format"]
        if (
            not isinstance(value, dict)
            or value.get("type") not in {"text", "json_object", "json_schema"}
        ):
            raise ValueError("response_format must specify text, json_object, or json_schema")
        if value.get("type") == "json_schema" and not isinstance(value.get("json_schema"), dict):
            raise ValueError("json_schema response_format requires a json_schema object")


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(part["text"] for part in content)


def messages_text(messages: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{message['role']}: {_content_text(message['content'])}" for message in messages
    )


def extract_response_text(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if choices:
        first = choices[0]
        if isinstance(first.get("message"), dict):
            return _content_text(first["message"].get("content", ""))
        if "text" in first:
            return str(first["text"])
    for key in ("generation", "outputText"):
        if key in payload:
            return str(payload[key])
    raise ValueError("model response did not contain generated text")


def _guardrail(client: Any, source: str, texts: Any) -> Dict[str, Any]:
    if isinstance(texts, str):
        texts = [texts]
    return client.apply_guardrail(
        guardrailIdentifier=os.environ["GUARDRAIL_ID"],
        guardrailVersion=os.environ["GUARDRAIL_VERSION"],
        source=source,
        content=[{"text": {"text": text}} for text in texts],
    )


def _intervention_text(result: Dict[str, Any]) -> str:
    texts = [
        output["text"]
        for output in result.get("outputs", [])
        if isinstance(output, dict) and isinstance(output.get("text"), str)
    ]
    return "\n".join(texts) if texts else "Blocked by the endpoint guardrail."


def _guard_messages(
    client: Any, messages: List[Dict[str, Any]], behavior: str
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    guarded = []
    targets = []
    texts = []
    for message_index, message in enumerate(messages):
        updated = dict(message)
        content = message["content"]
        if isinstance(content, str):
            targets.append((message_index, None))
            texts.append(content)
        else:
            updated["content"] = [dict(part) for part in content]
            for part_index, part in enumerate(content):
                targets.append((message_index, part_index))
                texts.append(part["text"])
        guarded.append(updated)

    result = _guardrail(client, "INPUT", texts)
    if result.get("action") != "GUARDRAIL_INTERVENED":
        return guarded, None
    if behavior != "anonymize":
        return messages, _intervention_text(result)

    outputs = [
        output.get("text") for output in result.get("outputs", [])
        if isinstance(output, dict) and isinstance(output.get("text"), str)
    ]
    if len(outputs) != len(targets):
        raise ValueError("guardrail masking output could not be mapped to request content")
    for (message_index, part_index), output in zip(targets, outputs):
        if part_index is None:
            guarded[message_index]["content"] = output
        else:
            guarded[message_index]["content"][part_index]["text"] = output
    return guarded, None


def _upstream_error(request_id: str, error: Exception, stage: str):
    code = _error_code(error) or type(error).__name__
    _log(request_id, 502, stage, code)
    return _response(502, {
        "error": {
            "code": "upstream_service_error",
            "message": "An AWS service could not process the request.",
        },
        "request_id": request_id,
    })


def lambda_handler(event, context):
    request_id = (
        event.get("requestContext", {}).get("requestId")
        or getattr(context, "aws_request_id", None)
        or "unknown"
    )
    try:
        raw_body = event.get("body") or "{}"
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        messages = extract_messages(body)
        validate_parameters(body)
    except (json.JSONDecodeError, ValueError) as error:
        _log(request_id, 400, "validation", "invalid_request")
        return _response(400, {
            "error": {"code": "invalid_request", "message": str(error)},
            "request_id": request_id,
        })

    try:
        client = runtime_client()
        guardrail_behavior = os.environ.get("GUARDRAIL_BEHAVIOR", "block")
        guarded_messages, blocked_message = _guard_messages(
            client, messages, guardrail_behavior
        )
    except Exception as error:
        return _upstream_error(request_id, error, "input_guardrail")

    if blocked_message:
        _log(request_id, 400, "input_guardrail", "guardrail_intervened")
        return _response(400, {
            "blocked": True,
            "message": blocked_message,
            "request_id": request_id,
        })

    body = dict(body)
    body["messages"] = guarded_messages
    allowed_fields = {
        "messages", "max_tokens", "temperature", "top_p", "stop",
        "response_format",
    }
    invocation_body = {key: value for key, value in body.items() if key in allowed_fields}

    try:
        model_response = client.invoke_model(
            modelId=os.environ["MODEL_ARN"],
            contentType="application/json",
            accept="application/json",
            body=json.dumps(invocation_body),
        )
        payload = json.loads(model_response["body"].read())
        generated_text = extract_response_text(payload)
    except Exception as error:
        if _error_code(error) == "ModelNotReadyException":
            _log(request_id, 503, "inference", "ModelNotReadyException")
            return _response(
                503,
                {
                    "error": {
                        "code": "model_not_ready",
                        "message": "The imported model is restoring; retry this request.",
                    },
                    "request_id": request_id,
                },
                {"Retry-After": "20"},
            )
        return _upstream_error(request_id, error, "inference")

    try:
        output_result = _guardrail(client, "OUTPUT", generated_text)
    except Exception as error:
        return _upstream_error(request_id, error, "output_guardrail")

    if output_result.get("action") == "GUARDRAIL_INTERVENED":
        _log(request_id, 200, "output_guardrail", "guardrail_intervened")
        return _response(200, {
            "blocked": guardrail_behavior != "anonymize",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": _intervention_text(output_result),
                },
                "finish_reason": "guardrail",
            }],
            "request_id": request_id,
        })

    payload["request_id"] = request_id
    _log(request_id, 200, "complete")
    return _response(200, payload)
