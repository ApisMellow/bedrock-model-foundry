#!/usr/bin/env python3
"""Write local harness configuration from the deployed Terraform outputs.

Produces two files:

  harness/foundry-endpoints.json  endpoint URLs and API keys, read by the shim
  opencode.json                   project-level opencode provider, no secrets

The API keys stay in the first file, which Git ignores. The harness config
holds only a localhost URL, so it is safe to show on a projector.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
GUARDRAIL_LABELS = {
    "pii-mask": "anonymizes email, name, and phone",
    "denied-topic": "blocks credential sharing",
}


def terraform_output_raw(name: str) -> str:
    completed = subprocess.run(
        ["terraform", "-chdir=terraform", "output", "-raw", name],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return completed.stdout


def terraform_output(name: str):
    completed = subprocess.run(
        ["terraform", "-chdir=terraform", "output", "-json", name],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def endpoint_config(urls, keys):
    missing = sorted(set(urls) - set(keys))
    if missing:
        raise SystemExit(f"no API key for endpoint(s): {', '.join(missing)}")
    return {
        "endpoints": {
            name: {"url": url, "api_key": keys[name]} for name, url in sorted(urls.items())
        }
    }


def opencode_config(names, base_url):
    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "foundry": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Bedrock Model Foundry",
                "options": {"baseURL": base_url, "apiKey": "unused-shim-holds-the-key"},
                "models": {
                    name: {
                        "name": f"{name} ({GUARDRAIL_LABELS.get(name, 'guarded endpoint')})",
                        # The imported demo model does not do function calling,
                        # and the endpoint rejects a tools field outright.
                        "tool_call": False,
                        # Without an output limit the harness asks for the whole
                        # context window, and prompt plus output then exceeds it.
                        "limit": {"context": 32768, "output": 4096},
                    }
                    for name in sorted(names)
                },
            }
        },
        # Default to the endpoint that answers rather than the one that blocks.
        "model": f"foundry/{'pii-mask' if 'pii-mask' in names else sorted(names)[0]}",
    }


def write_summary(path: Path, summary: str) -> None:
    """Persist Terraform's own summary text for a second window during a demo."""
    path.write_text(summary if summary.endswith("\n") else summary + "\n")
    print(f"wrote {path.name if path.parent == ROOT else path}")


def write_json(path: Path, payload, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    if private:
        path.chmod(0o600)
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    urls = terraform_output("endpoint_urls")
    keys = terraform_output("endpoint_api_keys")

    write_json(ROOT / "harness" / "foundry-endpoints.json", endpoint_config(urls, keys), private=True)
    write_json(
        ROOT / "opencode.json",
        opencode_config(urls, f"http://{args.host}:{args.port}/v1"),
    )

    write_summary(ROOT / "demo-endpoints.txt", terraform_output_raw("demo_summary"))

    print("\nnext:")
    print(f"  python3 harness/foundry_shim.py --port {args.port}")
    print("  opencode            # from this directory")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"FAIL: terraform output failed: {error.stderr}", file=sys.stderr)
        raise SystemExit(1)
