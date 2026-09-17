# Bedrock Model Foundry

Bedrock Model Foundry is a disposable demo for importing an open Hugging Face model into Amazon Bedrock and putting different guardrails in front of the same model.

The default model is [Qwen/Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct), pinned to commit `775b11afaf83e0dc75bd5abaf90133e47b3ec082`. It uses the Qwen2 architecture supported by Bedrock Custom Model Import. The selected model is not one of the native Qwen 3 foundation models in the current Bedrock catalog.

![Bedrock Model Foundry architecture](docs/architecture/bedrock-model-foundry.svg)

## What the demo creates

Terraform provisions:

- a private S3 bucket for temporary model staging;
- a CodeBuild job that downloads the model inside AWS;
- the IAM role Bedrock uses to read the staged model;
- a Bedrock imported model;
- two API Gateway REST endpoints backed by separate Lambda functions;
- one immutable Bedrock guardrail version per endpoint;
- API keys, usage plans, short log retention, and teardown hooks.

The `pii-mask` endpoint anonymizes email addresses, names, and phone numbers. The `denied-topic` endpoint blocks requests to reveal or exchange authentication secrets. Both endpoints call the same imported model.

Model weights never download to the operator's computer. Terraform uploads the small lifecycle script, starts CodeBuild, and waits. CodeBuild handles the Hugging Face snapshot, S3 upload, Bedrock import, and cleanup.

## Cost and teardown warning

A successful invocation of an imported model starts Bedrock Custom Model Unit billing in timed windows. Run `terraform destroy` when the demo is over.

The destroy hook starts CodeBuild in cleanup mode before Terraform removes the project. It deletes the imported model and the staged model prefix. The S3 bucket also has `force_destroy = true` because this repository is only for a disposable demo.

## Prerequisites

The deployment machine needs:

- Terraform 1.6 or newer;
- AWS CLI v2;
- AWS credentials for IAM, S3, SSM, CodeBuild, CloudWatch, Lambda, API Gateway, and Bedrock Custom Model Import;
- outbound access from CodeBuild to `huggingface.co`;
- Bedrock Custom Model Import quota in the selected Region.

Supported Regions for this configuration are `us-east-1`, `us-east-2`, `us-west-2`, and `eu-central-1`.

This workstation blocks direct Hugging Face traffic through its web filter. That does not affect the design because CodeBuild performs the download. Live deployment should run from an AWS account and network that permit the required calls.

## Offline checks

Create a Python environment if desired, install test dependencies, then run:

```bash
python3 -m pip install -r requirements-dev.txt
./scripts/check.sh
```

The check script compiles the Python sources, runs unit tests, checks shell syntax, formats Terraform, validates Terraform when providers are initialized, and parses the SVG.

## Deploy

Copy the example settings if you need to change the Region or tags:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Initialize, review, and apply:

```bash
terraform -chdir=terraform init
terraform -chdir=terraform plan -out=demo.tfplan
terraform -chdir=terraform apply demo.tfplan
```

The apply can take a while. CodeBuild downloads about 3 GB of model weights, uploads the snapshot to S3, submits the import job, and waits for Bedrock.

Retrieve the endpoint URLs:

```bash
terraform -chdir=terraform output -json endpoint_urls
```

Retrieve API keys only when needed. Terraform marks this output sensitive:

```bash
terraform -chdir=terraform output -json endpoint_api_keys
```

Do not paste API keys into source files, tickets, or logs.

## Run the live checks

After apply completes:

```bash
python3 scripts/smoke-test.py
```

The script checks missing-key rejection, normal inference on both endpoints, PII anonymization, denied-topic blocking, and the imported-model restore path. A cold model can return `ModelNotReadyException`. The Lambda maps that state to HTTP 503, and the smoke client retries for up to five minutes without printing API keys.

## Destroy

```bash
terraform -chdir=terraform plan -destroy -out=destroy.tfplan
terraform -chdir=terraform apply destroy.tfplan
```

If destroy stops during remote cleanup, inspect the CodeBuild logs before retrying. See [troubleshooting](docs/troubleshooting.md).

## Add a guardrail or endpoint

Add an entry to `local.endpoints` in `terraform/endpoints.tf`. Each entry creates its own guardrail version, Lambda role, Lambda function, REST API, API key, and usage plan.

The module currently supports two policy shapes:

- `pii`, with an allowlisted set of Bedrock PII entity types;
- `topic`, with a denied-topic name, definition, and examples.

Run `terraform plan` and review the new endpoint resources before applying.

## Add a model

The first version supports one active imported model to keep the lifecycle obvious. To change it:

1. confirm that Bedrock Custom Model Import supports the model architecture and tokenizer;
2. pin a full Hugging Face commit hash;
3. update the Terraform allowlist and the Python metadata validation together;
4. run the offline checks;
5. deploy in a disposable AWS account and record import size, time, CMU count, and cold-start behavior.

Multiple simultaneous imported models are listed in [the roadmap](docs/roadmap.md).

## Repository map

```text
docs/
  architecture/              customer diagram and editable Mermaid source
  superpowers/               approved design and implementation plan
  roadmap.md                 IAM adoption and platform extension notes
  troubleshooting.md         import-role and environment checks
scripts/
  check.sh                   offline verification
  run-codebuild.sh           Terraform's remote-job waiter
  smoke-test.py              credentialed endpoint checks
src/
  endpoint/app.py            guardrail and inference Lambda
  model_lifecycle.py         CodeBuild download/import/cleanup worker
terraform/
  modules/guarded_endpoint/  endpoint, guardrail, IAM, Lambda, and API module
  *.tf                       lifecycle infrastructure and two demo endpoints
tests/                       offline Python tests
```

## Known limits

- Bedrock model import is not a Terraform resource. A `terraform_data` provisioner starts the remote CodeBuild lifecycle job.
- The demo uses API keys. The production path uses API Gateway IAM authorization and assumed identities.
- The proxy uses `ApplyGuardrail` before and after inference because that path is model independent. Inline guardrails on imported-model calls remain a live compatibility test.
- API Gateway cannot wait through a long model restore. The client handles bounded 503 retries.
- This repository has no long-term support or availability target.
