"""API Gateway Lambda proxy for guarded imported-model inference."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List


_client = None


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


def _response(status: int, payload: Dict[str, Any], headers: Dict[str, str] | None = None):
    response_headers = {"Content-Type": "application/json"}
    if headers:
        response_headers.update(headers)
    return {
        "statusCode": status,
        "headers": response_headers,
        "body": json.dumps(payload),
    }


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
        if "content" not in message:
            raise ValueError("each message must contain content")
    return messages


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
        return "\n".join(text_parts)
    return str(content)


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


def _guardrail(client: Any, source: str, text: str) -> Dict[str, Any]:
    return client.apply_guardrail(
        guardrailIdentifier=os.environ["GUARDRAIL_ID"],
        guardrailVersion=os.environ["GUARDRAIL_VERSION"],
        source=source,
        content=[{"text": {"text": text}}],
    )


def _intervention_text(result: Dict[str, Any]) -> str:
    outputs = result.get("outputs", [])
    if outputs and isinstance(outputs[0], dict):
        return outputs[0].get("text", "Blocked by the endpoint guardrail.")
    return "Blocked by the endpoint guardrail."


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
    except (json.JSONDecodeError, ValueError) as error:
        return _response(400, {
            "error": {"code": "invalid_request", "message": str(error)},
            "request_id": request_id,
        })

    client = runtime_client()
    guardrail_behavior = os.environ.get("GUARDRAIL_BEHAVIOR", "block")
    input_result = _guardrail(client, "INPUT", messages_text(messages))
    if input_result.get("action") == "GUARDRAIL_INTERVENED":
        if guardrail_behavior == "anonymize":
            body = dict(body)
            body["messages"] = [{
                "role": "user",
                "content": _intervention_text(input_result),
            }]
        else:
            return _response(400, {
                "blocked": True,
                "message": _intervention_text(input_result),
                "request_id": request_id,
            })

    allowed_fields = {
        "messages", "max_tokens", "temperature", "top_p", "stop",
        "response_format", "structured_outputs", "tools", "tool_choice",
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
        raise

    output_result = _guardrail(client, "OUTPUT", generated_text)
    if output_result.get("action") == "GUARDRAIL_INTERVENED":
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
    return _response(200, payload)
