import json

import pytest

from harness import foundry_shim as shim


ENDPOINTS = {
    "pii-mask": {"url": "https://a.example/demo/v1/chat/completions", "api_key": "key-a"},
    "denied-topic": {"url": "https://b.example/demo/v1/chat/completions", "api_key": "key-b"},
}


class TestSanitizeRequest:
    def test_drops_fields_the_endpoint_rejects(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
            "presence_penalty": 1,
            "stream_options": {"include_usage": True},
        }
        clean, streaming = shim.sanitize_request(body)
        assert streaming is True
        # max_tokens is always supplied; see TestOutputBudget.
        assert clean["messages"] == [{"role": "user", "content": "hi"}]
        assert set(clean) == {"messages", "max_tokens"}

    def test_keeps_supported_fields(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 64,
            "temperature": 0.2,
            "top_p": 0.9,
            "stop": ["x"],
            "response_format": {"type": "text"},
        }
        clean, streaming = shim.sanitize_request(body)
        assert streaming is False
        assert clean == body

    def test_drops_model_because_each_endpoint_pins_its_own(self):
        clean, _ = shim.sanitize_request(
            {"messages": [{"role": "user", "content": "hi"}], "model": "pii-mask"}
        )
        assert "model" not in clean

    def test_flattens_non_text_content_parts(self):
        # Harnesses send structured content; the endpoint only accepts text parts.
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "data:..."}},
                    ],
                }
            ]
        }
        clean, _ = shim.sanitize_request(body)
        assert clean["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "look"}]}
        ]

    def test_drops_messages_left_with_no_usable_content(self):
        body = {
            "messages": [
                {"role": "user", "content": "keep"},
                {"role": "assistant", "content": [{"type": "image_url", "image_url": {}}]},
            ]
        }
        clean, _ = shim.sanitize_request(body)
        assert clean["messages"] == [{"role": "user", "content": "keep"}]


class TestCompletionEnvelope:
    def test_passes_through_openai_shaped_payload(self):
        payload = {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}]
        }
        completion = shim.completion_envelope(payload, "pii-mask")
        assert completion["choices"][0]["message"]["content"] == "hello"
        assert completion["model"] == "pii-mask"
        assert completion["object"] == "chat.completion"
        assert completion["id"].startswith("chatcmpl-")

    def test_wraps_generation_shaped_payload(self):
        completion = shim.completion_envelope({"generation": "hello"}, "pii-mask")
        assert completion["choices"][0]["message"] == {
            "role": "assistant",
            "content": "hello",
        }

    def test_wraps_output_text_shaped_payload(self):
        completion = shim.completion_envelope({"outputText": "hello"}, "pii-mask")
        assert completion["choices"][0]["message"]["content"] == "hello"

    def test_unknown_payload_becomes_visible_text_not_an_exception(self):
        completion = shim.completion_envelope({"surprise": 1}, "pii-mask")
        assert "surprise" in completion["choices"][0]["message"]["content"]

    def test_finish_reason_defaults_to_stop(self):
        completion = shim.completion_envelope({"generation": "hi"}, "pii-mask")
        assert completion["choices"][0]["finish_reason"] == "stop"

    def test_preserves_guardrail_finish_reason(self):
        payload = {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "masked"},
                    "finish_reason": "guardrail",
                }
            ]
        }
        completion = shim.completion_envelope(payload, "pii-mask")
        assert completion["choices"][0]["finish_reason"] == "guardrail"


class TestBlockedCompletion:
    def test_renders_guardrail_message_as_assistant_turn(self):
        body = {"blocked": True, "message": "Blocked by the endpoint guardrail.", "request_id": "r1"}
        completion = shim.blocked_completion(body, "denied-topic")
        choice = completion["choices"][0]
        assert choice["finish_reason"] == "content_filter"
        assert "Blocked by the endpoint guardrail." in choice["message"]["content"]

    def test_falls_back_when_the_payload_has_no_message(self):
        completion = shim.blocked_completion({"blocked": True}, "denied-topic")
        assert completion["choices"][0]["message"]["content"]


class TestSseFraming:
    def test_emits_role_delta_then_content_then_done(self):
        completion = shim.completion_envelope({"generation": "hello"}, "pii-mask")
        frames = shim.sse_frames(completion)

        assert frames[-1] == "data: [DONE]\n\n"
        payloads = [json.loads(frame[len("data: "):]) for frame in frames[:-1]]
        assert payloads[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
        assert payloads[1]["choices"][0]["delta"] == {"content": "hello"}
        assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
        assert all(payload["object"] == "chat.completion.chunk" for payload in payloads)

    def test_chunks_share_the_completion_id(self):
        completion = shim.completion_envelope({"generation": "hello"}, "pii-mask")
        payloads = [
            json.loads(frame[len("data: "):])
            for frame in shim.sse_frames(completion)[:-1]
        ]
        assert len({payload["id"] for payload in payloads}) == 1
        assert payloads[0]["id"] == completion["id"]


class TestModelRouting:
    def test_resolves_each_endpoint_by_model_name(self):
        assert shim.resolve_model("denied-topic", ENDPOINTS)["api_key"] == "key-b"

    def test_unknown_model_raises_with_the_available_names(self):
        with pytest.raises(shim.UnknownModel) as error:
            shim.resolve_model("gpt-4", ENDPOINTS)
        assert "pii-mask" in str(error.value)

    def test_model_list_is_openai_shaped(self):
        listing = shim.model_list(ENDPOINTS)
        assert listing["object"] == "list"
        assert {entry["id"] for entry in listing["data"]} == set(ENDPOINTS)
        assert all(entry["object"] == "model" for entry in listing["data"])


class TestUpstreamRequest:
    def test_sends_the_api_key_header_and_never_a_bearer_token(self):
        request = shim.build_upstream_request(
            ENDPOINTS["pii-mask"], {"messages": [{"role": "user", "content": "hi"}]}
        )
        assert request.get_header("X-api-key") == "key-a"
        assert request.get_header("Authorization") is None
        assert request.full_url == ENDPOINTS["pii-mask"]["url"]
        assert json.loads(request.data) == {"messages": [{"role": "user", "content": "hi"}]}


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._raw = json.dumps(payload).encode()
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTPError(shim.urllib.error.HTTPError):
    def __init__(self, payload, code, headers=None):
        self._raw = json.dumps(payload).encode()
        super().__init__("https://x", code, "err", headers or {}, None)

    def read(self):
        return self._raw


NOT_READY = {"error": {"code": "model_not_ready", "message": "restoring"}}


class TestColdModelRetry:
    def _call(self, responses, **kwargs):
        sent = iter(responses)
        slept = []
        clock = iter(range(0, 10000, 10))

        def opener(request, timeout=None):
            response = next(sent)
            if isinstance(response, Exception):
                raise response
            return response

        status, payload = shim.call_endpoint(
            ENDPOINTS["pii-mask"],
            {"messages": []},
            "pii-mask",
            opener=opener,
            sleep=slept.append,
            monotonic=lambda: next(clock),
            **kwargs,
        )
        return status, payload, slept

    def test_retries_until_the_model_answers(self):
        status, payload, slept = self._call([
            FakeHTTPError(NOT_READY, 503, {"Retry-After": "5"}),
            FakeHTTPError(NOT_READY, 503, {"Retry-After": "5"}),
            FakeResponse({"generation": "ready"}),
        ])
        assert status == 200
        assert payload["choices"][0]["message"]["content"] == "ready"
        assert slept == [5.0, 5.0]

    def test_gives_up_inside_the_budget_and_returns_the_503(self):
        status, payload, _ = self._call(
            [FakeHTTPError(NOT_READY, 503, {"Retry-After": "5"})] * 50,
            max_wait=20,
        )
        assert status == 503
        assert payload["error"]["code"] == "model_not_ready"

    def test_does_not_retry_other_errors(self):
        status, payload, slept = self._call([FakeHTTPError({"error": {"code": "boom"}}, 502)])
        assert status == 502
        assert slept == []

    def test_blocked_request_is_not_retried_and_becomes_a_message(self):
        status, payload, slept = self._call([
            FakeHTTPError({"blocked": True, "message": "nope"}, 400),
        ])
        assert status == 200
        assert payload["choices"][0]["finish_reason"] == "content_filter"
        assert slept == []


class TestOutputBudget:
    def test_clamps_an_oversized_output_request(self):
        # A harness may ask for the model's whole context as output; the model
        # rejects the call when prompt plus requested output exceeds it.
        clean, _ = shim.sanitize_request(
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 32000},
            max_output_tokens=4096,
        )
        assert clean["max_tokens"] == 4096

    def test_leaves_a_modest_request_alone(self):
        clean, _ = shim.sanitize_request(
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 256},
            max_output_tokens=4096,
        )
        assert clean["max_tokens"] == 256

    def test_supplies_a_budget_when_the_caller_omits_one(self):
        clean, _ = shim.sanitize_request(
            {"messages": [{"role": "user", "content": "hi"}]}, max_output_tokens=4096
        )
        assert clean["max_tokens"] == 4096

    def test_ignores_a_non_integer_budget(self):
        clean, _ = shim.sanitize_request(
            {"messages": [{"role": "user", "content": "hi"}], "max_tokens": "lots"},
            max_output_tokens=4096,
        )
        assert clean["max_tokens"] == 4096
