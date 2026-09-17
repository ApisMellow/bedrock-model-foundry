# Bedrock Model Foundry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a disposable Terraform demonstration that imports an ungated Hugging Face model entirely inside AWS and exposes two API-key-protected inference endpoints with distinct Bedrock guardrails.

**Architecture:** Terraform provisions a private S3 bucket, CodeBuild lifecycle job, Bedrock import role, and endpoint modules. A remote Python lifecycle worker stages and imports the pinned model, stores its ARN in SSM Parameter Store, and performs cleanup; Lambda proxies apply guardrails before and after Bedrock inference.

**Tech Stack:** Terraform >= 1.6, AWS provider >= 5.80, Python 3.12, boto3, huggingface_hub, pytest, Bash, CodeBuild, Bedrock, S3, SSM, Lambda, API Gateway REST.

**Spec:** `docs/superpowers/specs/2026-09-17-bedrock-model-foundry-design.md`

## Global Constraints

- Terraform is the deployment interface.
- Model files and Hugging Face caches exist only in AWS ephemeral storage and S3.
- Default model: `Qwen/Qwen2.5-1.5B-Instruct`, pinned to an explicit revision.
- The lifecycle worker accepts only the configured Qwen repository and Qwen2 architecture.
- Region is configurable; default `us-east-1`.
- Live AWS testing may be deferred to a credentialed machine.
- API keys are the demo auth mechanism; IAM/SigV4 assumed identity is roadmap-only.
- Teardown must delete the imported model and staged model artifacts.

---

### Task 1: Repository skeleton and operator documentation

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Create: `docs/architecture/bedrock-model-foundry.mmd`
- Create: `docs/architecture/bedrock-model-foundry.svg`
- Modify: `docs/troubleshooting.md`

**Interfaces:**
- Consumes: approved design spec.
- Produces: exact deploy, test, and destroy commands plus the customer-feedback diagram.

- [x] **Step 1: Write the repository skeleton and README**
  Document prerequisites, offline checks, `terraform init/plan/apply/destroy`, API-key retrieval, remote-only model handling, cost warning, and live-test handoff.
- [x] **Step 2: Create the high-contrast diagram**
  Include Hugging Face, CodeBuild, S3, Bedrock import, SSM handoff, two endpoint stacks, explicit guardrail calls, Terraform boundary, and the IAM/SigV4 roadmap.
- [x] **Step 3: Verify links and incomplete text**
  Run: `rg -n "TBD|TODO|FIXME" README.md docs`
  Expected: no incomplete text.

### Task 2: Test-first cloud model lifecycle worker

**Files:**
- Create: `src/model_lifecycle.py`
- Create: `tests/test_model_lifecycle.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: `validate_model_metadata(model_dir: Path, expected_repo: str) -> None`, `import_model(config: Config) -> str`, `cleanup_model(config: Config) -> None`, and `main() -> int`.

- [x] **Step 1: Write failing unit tests**
  Cover exact repository allowlisting, Qwen2 metadata, required safetensor/config/tokenizer files, successful SSM ARN publication, failed import status, and idempotent cleanup.
- [x] **Step 2: Run tests to verify failure**
  Run: `python3 -m pytest tests/test_model_lifecycle.py -q`
  Expected: import failure because the lifecycle module does not exist.
- [x] **Step 3: Implement minimal lifecycle worker**
  Use `huggingface_hub.snapshot_download`, boto3 S3 upload, `create_model_import_job`, polling with bounded sleep, SSM publication, and remote cleanup.
- [x] **Step 4: Run tests**
  Run: `python3 -m pytest tests/test_model_lifecycle.py -q`
  Expected: pass.

### Task 3: Test-first guarded endpoint Lambda

**Files:**
- Create: `src/endpoint/app.py`
- Create: `tests/test_endpoint.py`

**Interfaces:**
- Produces: `lambda_handler(event, context) -> dict`, `extract_messages(body) -> list[dict]`, `extract_response_text(payload) -> str`.
- Consumes: environment variables `MODEL_ARN`, `GUARDRAIL_ID`, `GUARDRAIL_VERSION`, and `AWS_REGION`.

- [x] **Step 1: Write failing tests**
  Cover invalid JSON, missing messages, blocked input, successful inference/output scan, blocked output, and `ModelNotReadyException` to HTTP 503 with `Retry-After`.
- [x] **Step 2: Run tests to verify failure**
  Run: `python3 -m pytest tests/test_endpoint.py -q`
  Expected: import failure because endpoint code does not exist.
- [x] **Step 3: Implement handler**
  Use explicit `ApplyGuardrail` input/output calls and `InvokeModel` with an OpenAI Chat Completions-shaped body. Never log prompts.
- [x] **Step 4: Run tests**
  Run: `python3 -m pytest tests/test_endpoint.py -q`
  Expected: pass.

### Task 4: Terraform lifecycle infrastructure

**Files:**
- Create: `terraform/versions.tf`, `providers.tf`, `variables.tf`, `lifecycle.tf`, `iam.tf`, and `buildspec.yml`
- Create: `scripts/run-codebuild.sh`

**Interfaces:**
- Produces: private staging bucket, import role ARN, CodeBuild project name, SSM parameter, and imported-model ARN data source.
- Consumes: `src/model_lifecycle.py` uploaded as a code artifact.

- [x] **Step 1: Write Terraform checks and variable validations**
  Restrict regions and exact model repository; require a 40-character pinned revision.
- [x] **Step 2: Implement S3, IAM, SSM, and CodeBuild**
  Follow the AWS model-import IAM role guidance linked from `docs/troubleshooting.md`.
- [x] **Step 3: Implement Terraform orchestration**
  A durable cleanup anchor is created before the fallible import resource. Both start CodeBuild with the exact model identity, and local code only starts or waits for AWS jobs.
- [x] **Step 4: Format and validate**
  Run: `terraform -chdir=terraform fmt -recursive -check`
  Run: `terraform -chdir=terraform init -backend=false && terraform -chdir=terraform validate`
  Expected: both pass when provider download is available.

### Task 5: Reusable endpoint Terraform module

**Files:**
- Create: `terraform/modules/guarded_endpoint/main.tf`, `variables.tf`, and `outputs.tf`
- Create: `terraform/endpoints.tf`
- Create: `terraform/outputs.tf`

**Interfaces:**
- Consumes: imported model ARN and endpoint objects.
- Produces: REST API URLs and sensitive API-key values.

- [x] **Step 1: Define the endpoint object contract**
  Include name, guardrail kind, denied-topic configuration, PII entity types, throttle, quota, and tags.
- [x] **Step 2: Implement guardrail/version and least-privilege Lambda**
  Each module instance gets one immutable guardrail version and a role scoped to one model and one guardrail.
- [x] **Step 3: Implement API Gateway REST API**
  Create `POST /v1/chat/completions`, API key, usage plan, Lambda permission, deployment, stage, structured Lambda logs, optional structured API Gateway access-log settings, and outputs. Keep ownership of the account-wide API Gateway log role in the adoption stack.
- [x] **Step 4: Instantiate two endpoints**
  Add `pii-mask` and `denied-topic` against the same imported model.

### Task 6: Smoke tests and operator validation

**Files:**
- Create: `scripts/smoke-test.py`, `scripts/audit-teardown.py`
- Create: `scripts/check.sh`
- Create: `tests/test_project_policy.py`

**Interfaces:**
- Consumes: Terraform JSON outputs.
- Produces: bounded normal-response, auth-failure, PII, denied-topic, and cold-start retry checks.

- [x] **Step 1: Write project policy tests**
  Verify the default model/revision, remote-only staging documentation, two endpoint definitions, and absence of unfinished placeholders.
- [x] **Step 2: Implement smoke client**
  Retry HTTP 503 responses using `Retry-After` for at most five minutes; never print API keys. Add a credentialed post-destroy audit for exact project resources.
- [x] **Step 3: Implement offline check script**
  Run Python compile, pytest, shell syntax, Terraform formatting/validation when available, and SVG/XML parsing.
- [x] **Step 4: Run offline checks**
  Run: `./scripts/check.sh`
  Expected: pass, with an explicit skip only if Terraform or provider network access is unavailable.

### Task 7: Production-auth roadmap and final verification

**Files:**
- Create: `docs/roadmap.md`
- Modify: `README.md`

**Interfaces:**
- Produces: concrete IAM/SigV4 assumed-identity adoption steps without enabling them in the demo.

- [x] **Step 1: Document production auth**
  Specify API Gateway `AWS_IAM`, SigV4 callers, STS assumed roles, resource policies, per-endpoint authorization, and migration from API keys.
- [x] **Step 2: Document extension workflows**
  Show how to add a guardrail, endpoint, and model definition.
- [x] **Step 3: Run full verification**
  Run: `./scripts/check.sh`
  Run: `git diff --check`
  Expected: pass.
- [x] **Step 4: Commit**
  Stage only project files and create a conventional commit after initializing/fetching the supplied GitHub repository.
