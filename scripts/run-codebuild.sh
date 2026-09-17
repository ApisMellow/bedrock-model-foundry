#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
case "$action" in
  import|cleanup) ;;
  *)
    echo "usage: $0 <import|cleanup>" >&2
    exit 2
    ;;
esac

: "${CODEBUILD_PROJECT:?CODEBUILD_PROJECT is required}"
region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
if [[ -z "$region" ]]; then
  echo "AWS_REGION or AWS_DEFAULT_REGION is required" >&2
  exit 2
fi

overrides=("name=ACTION,value=$action,type=PLAINTEXT")
for name in \
  HF_MODEL_ID MODEL_REVISION MODEL_DEPLOYMENT_ID S3_BUCKET S3_PREFIX SSM_PARAMETER_NAME \
  IMPORT_ROLE_ARN IMPORTED_MODEL_NAME LIFECYCLE_SCRIPT_KEY; do
  if [[ -n "${!name:-}" ]]; then
    overrides+=("name=$name,value=${!name},type=PLAINTEXT")
  fi
done

build_id="$(aws codebuild start-build \
  --project-name "$CODEBUILD_PROJECT" \
  --region "$region" \
  --environment-variables-override "${overrides[@]}" \
  --query 'build.id' \
  --output text)"
echo "Started CodeBuild lifecycle action '$action': $build_id"

for attempt in $(seq 1 720); do
  status="$(aws codebuild batch-get-builds --ids "$build_id" --region "$region" --query 'builds[0].buildStatus' --output text)"
  case "$status" in
    SUCCEEDED)
      echo "CodeBuild lifecycle action '$action' succeeded."
      exit 0
      ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      echo "CodeBuild lifecycle action '$action' ended with status $status." >&2
      exit 1
      ;;
    IN_PROGRESS) sleep 15 ;;
    *)
      echo "Unexpected CodeBuild status: $status" >&2
      exit 1
      ;;
  esac
done

echo "Timed out waiting for CodeBuild lifecycle action '$action'." >&2
exit 1
