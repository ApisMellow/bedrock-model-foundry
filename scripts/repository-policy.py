#!/usr/bin/env python3
"""First-push repository policy checks that require no AWS credentials."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
SELF = Path(__file__).resolve()
EXCLUDED_IDENTIFIER = bytes((104, 101, 114, 109, 101, 115)).decode()
SECRET_PATTERNS = {
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private key": re.compile("-" * 5 + r"BEGIN [A-Z ]*PRIVATE KEY" + "-" * 5),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
}
MODEL_FILE_SUFFIXES = {".safetensors", ".gguf", ".onnx"}


def tracked_candidates():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.resolve() == SELF:
            continue
        if any(part in {".git", ".terraform", ".pytest_cache", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        if path.suffix in {".zip", ".pyc"}:
            continue
        yield path


def main() -> int:
    failures = []
    for path in tracked_candidates():
        if path.suffix.lower() in MODEL_FILE_SUFFIXES:
            failures.append(f"local model artifact: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(errors="ignore")
        if EXCLUDED_IDENTIFIER in text.lower():
            failures.append(f"excluded identifier: {path.relative_to(ROOT)}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label}: {path.relative_to(ROOT)}")
    if failures:
        raise SystemExit("repository policy failed:\n  - " + "\n  - ".join(failures))
    print("Repository policy checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
