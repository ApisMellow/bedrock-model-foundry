import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "generate_harness_config",
    Path(__file__).parents[1] / "scripts" / "generate-harness-config.py",
)
gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gen)


URLS = {
    "pii-mask": "https://a.example/demo/v1/chat/completions",
    "denied-topic": "https://b.example/demo/v1/chat/completions",
}
KEYS = {"pii-mask": "key-a", "denied-topic": "key-b"}


class TestEndpointConfig:
    def test_pairs_each_url_with_its_key(self):
        config = gen.endpoint_config(URLS, KEYS)
        assert config["endpoints"]["pii-mask"] == {"url": URLS["pii-mask"], "api_key": "key-a"}

    def test_refuses_an_endpoint_with_no_key(self):
        with pytest.raises(SystemExit) as error:
            gen.endpoint_config(URLS, {"pii-mask": "key-a"})
        assert "denied-topic" in str(error.value)


class TestOpencodeConfig:
    def test_points_at_the_shim_and_holds_no_secret(self):
        config = gen.opencode_config(URLS, "http://127.0.0.1:8787/v1")
        options = config["provider"]["foundry"]["options"]
        assert options["baseURL"] == "http://127.0.0.1:8787/v1"
        assert "key-a" not in json.dumps(config)

    def test_publishes_every_endpoint_as_a_model(self):
        models = gen.opencode_config(URLS, "http://x/v1")["provider"]["foundry"]["models"]
        assert set(models) == set(URLS)

    def test_caps_output_and_disables_tool_calling(self):
        # Without these the harness asks for the whole context window as output,
        # and the endpoint rejects a tools field outright.
        models = gen.opencode_config(URLS, "http://x/v1")["provider"]["foundry"]["models"]
        for model in models.values():
            assert model["tool_call"] is False
            assert model["limit"]["output"] == 4096

    def test_defaults_to_the_endpoint_that_answers(self):
        config = gen.opencode_config(URLS, "http://x/v1")
        assert config["model"] == "foundry/pii-mask"

    def test_falls_back_to_the_first_endpoint_when_pii_mask_is_absent(self):
        config = gen.opencode_config({"zeta": "u", "alpha": "u"}, "http://x/v1")
        assert config["model"] == "foundry/alpha"


class TestSummaryFile:
    def test_writes_the_terraform_summary_verbatim(self, tmp_path):
        target = tmp_path / "demo-endpoints.txt"
        gen.write_summary(target, "Bedrock Model Foundry - us-east-1\n\nendpoints\n  pii-mask  https://a")
        written = target.read_text()
        assert written.startswith("Bedrock Model Foundry")
        assert written.endswith("\n")

    def test_does_not_double_the_trailing_newline(self, tmp_path):
        target = tmp_path / "demo-endpoints.txt"
        gen.write_summary(target, "one line\n")
        assert target.read_text() == "one line\n"
