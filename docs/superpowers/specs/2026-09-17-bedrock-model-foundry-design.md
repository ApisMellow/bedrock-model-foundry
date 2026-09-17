# Bedrock Model Foundry Design

**Date:** 2026-09-17
**Status:** Approved for implementation
**Project:** `bedrock-model-foundry`

## Purpose

Bedrock Model Foundry is a disposable demonstration of the path from an open Hugging Face model to a guarded Amazon Bedrock inference API. It is a reusable platform component for other Bedrock-focused demos, but it is not intended to be a permanently supported production service.

The demo must let a reviewer see and understand these operations:

1. Add a model definition.
2. Download its Hugging Face artifacts inside AWS.
3. Stage the artifacts in S3 and import the model into Bedrock.
4. Add guardrail definitions.
5. Publish two API endpoints against the same model with different guardrail behavior.
6. Test the endpoints and tear down every billable resource.

## Constraints and decisions

- Terraform is the deployment interface. Operators run `terraform` commands rather than hand-building resources in the console.
- The model default is `Qwen/Qwen2.5-1.5B-Instruct` at an explicitly pinned Hugging Face revision. It is small, ungated, Apache-2.0, uses a Bedrock Custom Model Import-supported Qwen2 architecture, and is not one of the native Qwen 3 foundation models in the current Bedrock catalog.
- No model weight, Hugging Face cache, or model-named artifact is downloaded to the developer workstation. CodeBuild performs all model-file handling in AWS ephemeral storage and S3.
- The project permits only the configured Qwen demonstration repository; arbitrary model repositories are rejected by validation.
- AWS Region is configurable. The default is `us-east-1`; supported alternatives are `us-east-2`, `us-west-2`, and `eu-central-1` for the selected import path.
- Local AWS credentials may be insufficient. Static validation and unit tests run locally; live deployment and integration tests may be handed to a credentialed machine or CI worker.
- The demo uses API Gateway API keys and usage plans. The production roadmap documents IAM/SigV4 authorization and assumed identities without implementing them in the demo path.
- Imported-model lifecycle is imperative because Bedrock Custom Model Import has no native Terraform or CloudFormation resource. Terraform may orchestrate a remote job, but it cannot own the imported model as a first-class provider resource.

## Architecture

### Control plane

Terraform creates:

- an encrypted, private S3 staging bucket;
- a Bedrock import service role with read-only access to the model prefix;
- a CodeBuild role and project for remote download, import, status polling, and cleanup;
- an SSM parameter used as the handoff from the remote import job to Terraform;
- two endpoint stacks, each containing a guardrail, immutable guardrail version, Lambda proxy, REST API route, API key, usage plan, logs, and least-privilege IAM role.

A cleanup `terraform_data` anchor is recorded before a second, fallible import resource invokes a small local orchestration script. That script only calls AWS APIs: it starts CodeBuild and waits for the remote build. It never downloads model artifacts. CodeBuild downloads the pinned Hugging Face snapshot, validates the required files, uploads them to S3, starts the Bedrock import, polls to a terminal state, and records the imported-model ARN in Parameter Store. A dependent Terraform data source reads the ARN before constructing endpoint IAM policies and Lambda configuration.

On `terraform destroy`, the independent cleanup anchor starts the same CodeBuild project in cleanup mode even if the import resource was tainted. The remote cleanup deletes the imported model, removes the staged S3 objects, and clears the handoff parameter before Terraform removes the remaining infrastructure.

### Data plane

Each endpoint is independently configured and tagged:

```text
client -> API Gateway REST API -> Lambda proxy -> ApplyGuardrail(INPUT)
       -> imported Bedrock model -> ApplyGuardrail(OUTPUT) -> client
```

The Lambda proxy owns the model ARN and guardrail identifier/version. Consumers cannot select a different model or guardrail. Requests use an OpenAI Chat Completions-shaped JSON body and are sent to Bedrock through `InvokeModel`.

The guaranteed default is explicit `ApplyGuardrail` before and after inference. Inline guardrails on an imported model remain a separately documented spike because AWS does not clearly guarantee that combination. A future mode may switch to inline guardrail request parameters after a live test proves support.

### Demo endpoints

Both endpoints call the same imported model:

- `pii-mask`: detects configured PII and anonymizes it.
- `denied-topic`: blocks a clear, demo-safe denied topic.

Sending the same prompts to both endpoints demonstrates that the endpoint configuration—not the underlying model—controls the safety policy.

## Configuration model

Models and endpoints are map-shaped Terraform inputs so extension is additive:

- a model definition contains Hugging Face repository, revision, S3 prefix, and imported-model name;
- an endpoint definition contains model key, guardrail policy, API quota, throttle, and tags;
- adding a guardrail means adding or changing an endpoint policy object;
- adding an endpoint means adding another endpoint map entry;
- adding a model means adding a model entry and allowing the remote lifecycle job to import it.

The first implementation supports one active imported model because the demo deadline values clarity. The file layout and variable types leave a documented path to `for_each` model imports later.

## Security

- S3 blocks all public access, uses server-side encryption, and is force-destroyable only because this is an explicitly disposable demo.
- IAM permissions are separated between the CodeBuild lifecycle role, Bedrock import role, and each endpoint Lambda role.
- Lambda roles can invoke only the selected imported-model ARN and apply only their endpoint guardrail.
- API keys are sensitive Terraform outputs. They are not written to source files or logs, but they remain in Terraform state, which must use encrypted storage and restricted access outside a local demo.
- Lambda environment variables contain resource identifiers, not credentials.
- CloudWatch log groups have explicit short retention periods. Lambda writes structured correlation fields without prompt bodies. API Gateway's account-wide logging role is left to the adoption stack.
- The model revision is pinned to make downloads reproducible and reduce supply-chain drift.
- The cloud lifecycle validates the exact repository allowlist, required files, and allowed architecture metadata before upload or import.
- A production implementation should add VPC endpoints/PrivateLink, customer-managed KMS keys, model invocation logging, artifact signatures or checksums, CI policy checks, and workload identity federation.

## Failure handling

- Hugging Face download, validation, S3 upload, import submission, and import polling failures fail the CodeBuild run and therefore fail `terraform apply`.
- The lifecycle script reports the Bedrock import failure message without logging secrets.
- Lambda validates request shape and returns structured 4xx errors.
- Guardrail intervention returns a consistent blocked response without calling the model for blocked input.
- API Gateway's synchronous timeout is shorter than a possible imported-model restore. Lambda performs only short SDK retries; persistent `ModelNotReadyException` becomes HTTP 503 with `Retry-After`. The smoke-test client retries across requests for a bounded period.
- Endpoint failures include a correlation ID in logs and responses while excluding prompt bodies from application logs.

## Testing

Local/offline checks:

- Terraform formatting and validation;
- Shell syntax checks;
- Python unit tests for lifecycle validation, response extraction, guardrail intervention, and `ModelNotReadyException` mapping;
- an offline repository-policy scan for excluded model identifiers, credential patterns, and local model artifacts.

Credentialed integration checks:

1. `terraform apply` completes the cloud-only download and Bedrock import.
2. Both endpoint health/inference paths return a normal response.
3. PII input/output is anonymized on the PII endpoint.
4. The denied-topic endpoint blocks its configured topic.
5. API calls without an API key fail.
6. An idle model returns either normally or through the documented 503/retry path; the test records time to first successful response.
7. `terraform destroy` removes the imported model and staged artifacts; the credentialed teardown audit finds no scoped demo resource.

Live testing is explicitly allowed to occur on another machine or CI worker with suitable AWS permissions.

## Customer feedback diagram

The repository includes a high-contrast SVG and editable Mermaid source. The diagram separates the model lifecycle from the customer inference path and highlights the intentional Terraform/imperative boundary. It also labels API-key access as the demo choice and IAM/SigV4 assumed identity as the adoption roadmap.

## Production-adoption roadmap

The code and documentation preserve these next steps without implementing them today:

1. Replace API keys with API Gateway IAM authorization.
2. Map a caller's assumed IAM identity to an allowed endpoint and tenant context.
3. Add CI workload identity and approval gates for model and guardrail changes.
4. Expand model definitions to multiple imported models with isolated import state.
5. Test inline guardrails and `bedrock:GuardrailIdentifier` enforcement on imported-model ARNs.
6. Add an account-level comprehensive guardrail floor only in a dedicated account, with model include/exclude scoping proven first.
7. Add private networking, customer-managed encryption keys, audit/invocation logging, quotas, cost controls, and artifact provenance.

## Non-goals

- Long-term operations or an availability SLA.
- A graphical administration console.
- Automatic EventBridge ingestion from arbitrary buckets.
- Intelligent routing across models.
- Keeping a model warm continuously.
- Production IAM federation in the demo implementation.
- Support for arbitrary architectures outside Bedrock Custom Model Import's documented list.

## Acceptance criteria

- A new operator can understand the repository from the README and diagram.
- A credentialed operator can deploy with documented Terraform commands without model files touching their workstation.
- The same imported model is reachable through two API-key-protected endpoints with visibly different guardrail behavior.
- Model, guardrail, and endpoint additions have clear configuration extension points.
- Offline tests pass without AWS credentials.
- Live checks and limitations are clearly separated from offline verification.
- Teardown is documented, automated, and covers imported-model and S3 cleanup.
