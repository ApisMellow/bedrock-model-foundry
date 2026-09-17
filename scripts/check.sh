#!/usr/bin/env bash
set -euo pipefail

python3 -m compileall -q src scripts
python3 -m pytest -q

for script in scripts/*.sh; do
  bash -n "$script"
done

terraform -chdir=terraform fmt -recursive -check

if [[ -d terraform/.terraform ]]; then
  terraform -chdir=terraform validate
else
  echo "SKIP terraform validate: run 'terraform -chdir=terraform init -backend=false' first."
fi

python3 -c 'import xml.etree.ElementTree as ET; ET.parse("docs/architecture/bedrock-model-foundry.svg")'
echo "All available offline checks passed."
