import importlib.util
import json
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "smoke_test", Path(__file__).parents[1] / "scripts" / "smoke-test.py"
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class Response:
    def __init__(self, status, payload, retry_after=None):
        self.status = status
        self.payload = payload
        self.headers = {}
        if retry_after is not None:
            self.headers["Retry-After"] = str(retry_after)

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_retryable_model_restore_eventually_returns_response():
    responses = iter([
        Response(503, {"error": {"code": "model_not_ready"}}, retry_after=1),
        Response(200, {"choices": [{"message": {"content": "ready"}}]}),
    ])
    sleeps = []

    result = smoke.post_with_retry(
        "https://example.invalid",
        "secret",
        {"messages": [{"role": "user", "content": "hello"}]},
        requester=lambda request: next(responses),
        sleep=sleeps.append,
        monotonic=iter([0, 0, 1]).__next__,
        max_wait=10,
    )

    assert result["choices"][0]["message"]["content"] == "ready"
    assert sleeps == [1]


def test_non_retryable_response_fails_immediately():
    responses = iter([Response(403, {"message": "Forbidden"})])

    try:
        smoke.post_with_retry(
            "https://example.invalid",
            "wrong",
            {"messages": [{"role": "user", "content": "hello"}]},
            requester=lambda request: next(responses),
            sleep=lambda _: None,
            monotonic=iter([0, 0]).__next__,
            max_wait=10,
        )
    except smoke.SmokeFailure as error:
        assert "403" in str(error)
    else:
        raise AssertionError("403 must fail the smoke check")
