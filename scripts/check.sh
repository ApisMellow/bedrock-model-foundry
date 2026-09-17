#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Prefer the project virtualenv. The test dependencies are rarely installed
# into the system interpreter, and a bare run should not fail on that.
python="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  python=".venv/bin/python"
fi

if ! "$python" -c 'import pytest' 2>/dev/null; then
  echo "pytest is missing from $python." >&2
  echo "Create the environment first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt" >&2
  exit 1
fi

"$python" -m compileall -q src scripts harness
"$python" -m pytest -q
"$python" scripts/repository-policy.py

for script in scripts/*.sh; do
  bash -n "$script"
done

terraform -chdir=terraform fmt -recursive -check

if [[ -d terraform/.terraform ]]; then
  terraform -chdir=terraform validate
else
  echo "SKIP terraform validate: run 'terraform -chdir=terraform init -backend=false' first."
fi

"$python" -c 'import xml.etree.ElementTree as ET; ET.parse("docs/architecture/bedrock-model-foundry.svg")'
echo "All available offline checks passed."
