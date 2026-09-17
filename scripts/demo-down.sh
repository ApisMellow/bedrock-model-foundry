#!/usr/bin/env bash
# Remove every billable resource and prove it.
#
# The audit is the point. A destroy that appears to succeed while leaving an
# imported model behind keeps costing, so this script fails loudly if the
# account still holds anything belonging to the demo.
set -euo pipefail

cd "$(dirname "$0")/.."
python="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  python=".venv/bin/python"
fi

project="$(terraform -chdir=terraform output -raw model_lifecycle_codebuild_project 2>/dev/null || echo bedrock-model-foundry-model-lifecycle)"
project="${project%-model-lifecycle}"
region="$(terraform -chdir=terraform output -json endpoint_urls 2>/dev/null \
  | "$python" -c 'import json,sys; print(next(iter(json.load(sys.stdin).values())).split(".execute-api.")[1].split(".")[0])' 2>/dev/null \
  || echo "${AWS_REGION:-us-east-1}")"

if pgrep -f foundry_shim.py >/dev/null 2>&1; then
  echo "==> stopping the local shim"
  pkill -f foundry_shim.py || true
fi

echo "==> terraform destroy (remote cleanup deletes the imported model first)"
terraform -chdir=terraform plan -destroy -input=false -out=destroy.tfplan
terraform -chdir=terraform apply destroy.tfplan

echo "==> teardown audit"
"$python" scripts/audit-teardown.py --project-name "$project" --region "$region"

echo "==> removing generated configuration"
rm -f harness/foundry-endpoints.json opencode.json demo-endpoints.txt terraform/destroy.tfplan

echo
echo "torn down. a later deploy starts from scripts/demo-up.sh"
