#!/usr/bin/env bash
# Deploy the demo and leave it ready to talk to.
#
# Beyond terraform apply this absorbs the two waits that make a fresh deploy
# look broken: API Gateway keys take minutes to propagate, and a newly imported
# model is cold until something invokes it.
set -euo pipefail

cd "$(dirname "$0")/.."
python="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  python=".venv/bin/python"
fi

echo "==> terraform init"
terraform -chdir=terraform init -input=false >/dev/null

echo "==> terraform apply (the model import takes roughly ten minutes)"
terraform -chdir=terraform apply -input=false -auto-approve

echo "==> harness configuration"
"$python" scripts/generate-harness-config.py

url="$(terraform -chdir=terraform output -json endpoint_urls | "$python" -c 'import json,sys; print(json.load(sys.stdin)["pii-mask"])')"
key="$(terraform -chdir=terraform output -json endpoint_api_keys | "$python" -c 'import json,sys; print(json.load(sys.stdin)["pii-mask"])')"

probe() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 120 -X POST "$url" \
    -H 'content-type: application/json' -H "x-api-key: $key" \
    -d '{"messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
}

echo "==> waiting for the API key to propagate"
for _ in $(seq 1 30); do
  code="$(probe || true)"
  [[ "$code" == "403" ]] || break
  sleep 10
done
if [[ "$code" == "403" ]]; then
  echo "    still 403 after five minutes; check the usage plan and key association" >&2
  exit 1
fi
echo "    key accepted"

echo "==> warming the model (a cold import restores in about a minute)"
for _ in $(seq 1 20); do
  code="$(probe || true)"
  [[ "$code" == "503" ]] || break
  sleep 15
done
if [[ "$code" != "200" ]]; then
  echo "    endpoint returned HTTP $code rather than a completion" >&2
  exit 1
fi
echo "    model is warm"

echo
terraform -chdir=terraform output -raw demo_summary
echo
echo "the same text is in demo-endpoints.txt"
echo
echo "start the harness:"
echo "  $python harness/foundry_shim.py --keep-warm"
echo "  opencode          # from this directory"
