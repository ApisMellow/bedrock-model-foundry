import io
import json
from types import SimpleNamespace

import pytest

from src.endpoint import app


def event(body):
    return {"body": json.dumps(body), "requestContext": {"requestId": "req-1"}}


class FakeRuntime:
    def __init__(self, *, input_action="NONE", output_action="NONE", ready=True):
        self.input_action = input_action
        self.output_action = output_action
        self.ready = ready
        self.guardrail_calls = []
        self.invoke_calls = []

    def apply_guardrail(self, **kwargs):
        self.guardrail_calls.append(kwargs)
        action = self.input_action if kwargs["source"] == "INPUT" else self.output_action
        response = {"action": action}
        if action == "GUARDRAIL_INTERVENED":
            response["outputs"] = [{"text": "Blocked by the endpoint guardrail."}]
        return response

    def invoke_model(self, **kwargs):
        self.invoke_calls.append(kwargs)
        if not self.ready:
            error = RuntimeError("model restoring")
            error.response = {"Error": {"Code": "ModelNotReadyException"}}
            raise error
        payload = {"choices": [{"message": {"role": "assistant", "content": "Hello there"}}]}
        return {"body": io.BytesIO(json.dumps(payload).encode())}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("MODEL_ARN", "arn:aws:bedrock:us-east-1:123:imported-model/demo")
    monkeypatch.setenv("GUARDRAIL_ID", "guardrail-id")
    monkeypatch.setenv("GUARDRAIL_VERSION", "1")


def test_invalid_json_returns_400():
    response = app.lambda_handler({"body": "{"}, SimpleNamespace(aws_request_id="ctx"))
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "invalid_request"


def test_messages_are_required():
    response = app.lambda_handler(event({"max_tokens": 10}), SimpleNamespace(aws_request_id="ctx"))
    assert response["statusCode"] == 400


def test_blocked_input_never_invokes_model(monkeypatch):
    runtime = FakeRuntime(input_action="GUARDRAIL_INTERVENED")
    monkeypatch.setattr(app, "runtime_client", lambda: runtime)

    response = app.lambda_handler(event({"messages": [{"role": "user", "content": "blocked"}]}), None)

    assert response["statusCode"] == 400
    assert runtime.invoke_calls == []
    assert json.loads(response["body"])["blocked"] is True


def test_success_scans_input_and_output(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(app, "runtime_client", lambda: runtime)

    response = app.lambda_handler(event({
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 32,
    }), None)

    assert response["statusCode"] == 200
    assert [call["source"] for call in runtime.guardrail_calls] == ["INPUT", "OUTPUT"]
    invoked = json.loads(runtime.invoke_calls[0]["body"])
    assert invoked["messages"][0]["content"] == "hello"
    assert json.loads(response["body"])["choices"][0]["message"]["content"] == "Hello there"


def test_blocked_output_replaces_model_response(monkeypatch):
    runtime = FakeRuntime(output_action="GUARDRAIL_INTERVENED")
    monkeypatch.setattr(app, "runtime_client", lambda: runtime)

    response = app.lambda_handler(event({"messages": [{"role": "user", "content": "hello"}]}), None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["blocked"] is True
    assert body["choices"][0]["message"]["content"] == "Blocked by the endpoint guardrail."


def test_model_not_ready_returns_retryable_503(monkeypatch):
    runtime = FakeRuntime(ready=False)
    monkeypatch.setattr(app, "runtime_client", lambda: runtime)

    response = app.lambda_handler(event({"messages": [{"role": "user", "content": "hello"}]}), None)

    assert response["statusCode"] == 503
    assert response["headers"]["Retry-After"] == "20"
    assert json.loads(response["body"])["error"]["code"] == "model_not_ready"


def test_anonymized_input_is_sent_to_model(monkeypatch):
    runtime = FakeRuntime(input_action="GUARDRAIL_INTERVENED")
    monkeypatch.setenv("GUARDRAIL_BEHAVIOR", "anonymize")
    monkeypatch.setattr(app, "runtime_client", lambda: runtime)

    response = app.lambda_handler(event({
        "messages": [{"role": "user", "content": "Email me at person@example.com"}]
    }), None)

    assert response["statusCode"] == 200
    invoked = json.loads(runtime.invoke_calls[0]["body"])
    assert invoked["messages"] == [{
        "role": "user",
        "content": "Blocked by the endpoint guardrail.",
    }]


def test_anonymized_output_returns_sanitized_text(monkeypatch):
    runtime = FakeRuntime(output_action="GUARDRAIL_INTERVENED")
    monkeypatch.setenv("GUARDRAIL_BEHAVIOR", "anonymize")
    monkeypatch.setattr(app, "runtime_client", lambda: runtime)

    response = app.lambda_handler(event({"messages": [{"role": "user", "content": "hello"}]}), None)

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["blocked"] is False
    assert body["choices"][0]["message"]["content"] == "Blocked by the endpoint guardrail."
