# Adoption roadmap

The demo stops at API keys and one active imported model. These changes turn the same layout into a reusable internal component.

## IAM and assumed identities

Replace `authorization = "NONE"` and `api_key_required = true` on the API Gateway method with `authorization = "AWS_IAM"`.

Callers then sign requests with SigV4. Human and workload callers should assume a narrowly scoped role through STS rather than use long-lived IAM user keys. Give each caller role permission to invoke only its API method ARN:

```text
arn:aws:execute-api:<region>:<account>:<api-id>/<stage>/POST/v1/chat/completions
```

Keep a separate Lambda execution role per endpoint. The caller identity controls API access, while the Lambda role still pins the model ARN and guardrail. This prevents a caller from switching either value in the request.

Before removing API keys:

1. add IAM authorization beside the existing demo path in a nonproduction account;
2. test allowed and denied assumed roles;
3. add an API resource policy if callers come from known accounts or organizations;
4. move CI to workload identity federation;
5. remove the API keys and usage-plan key resources after consumers migrate.

## DevSecOps workflow

Treat models, guardrails, and endpoints as reviewed configuration changes.

A model change should include its Hugging Face repository, immutable revision, license, architecture, tokenizer, expected size, Region, and cost notes. CI should run the offline suite before a credentialed promotion job starts the import.

A guardrail change should include examples that must pass and examples that must block or anonymize. Run those checks against a test endpoint before promoting the immutable guardrail version.

An endpoint change should name its model, guardrail policy, caller role, quota, owner, and teardown date. Terraform plan output is the review artifact.

## Multiple models

Refactor the single lifecycle resources into a `for_each` model module. Give every model its own:

- S3 prefix;
- SSM parameter;
- imported-model name;
- CodeBuild trigger hash;
- endpoint references and cost tags.

Do not share a mutable parameter between concurrent imports. Store one imported-model ARN per model key.

## Guardrail enforcement tests

Run three tests in a lab account before relying on inline enforcement:

1. call the imported model with the correct guardrail identifier and version;
2. call it with a different guardrail;
3. call it without a guardrail.

Repeat those calls with a Lambda role that has a `bedrock:GuardrailIdentifier` condition on `bedrock:InvokeModel`. Keep the explicit `ApplyGuardrail` path until both imported-model behaviors are proven.

## Live console for demonstrations

A demonstration currently shows Terraform log lines while the stack builds. A local web console would show the same progress against the architecture diagram: every component drawn from the start, each one lit as it comes online.

The console should read state rather than accept it. Each element maps to a check that already exists:

- staging bucket, IAM roles, CodeBuild project, and SSM parameter: resource existence calls;
- model download and upload: CodeBuild phase and the lifecycle job's own log lines;
- imported model: the Bedrock import job status, then the imported-model listing;
- guardrails, Lambdas, REST APIs, keys, and usage plans: per-endpoint describe calls;
- first warm response: the time to first successful invocation the smoke test already measures.

A small local server can poll those checks on an interval and push state changes to the page over server-sent events, reusing the shim's pattern. `docs/architecture/bedrock-model-foundry.mmd` is the diagram source, so the console and the customer diagram cannot drift apart.

Two properties matter more than the visual. The console must be read-only, because a demonstration aid that can mutate infrastructure is a liability. It must also distinguish "not yet created" from "created and unhealthy", since a demonstration audience reads a dark component as a failure either way.

Teardown deserves the same treatment in reverse: components going dark as `terraform destroy` removes them ends a demonstration on the cost story rather than on a scrolling log.

## Shared observability and production controls

The demo keeps structured Lambda logs inside resources it owns and exposes `api_gateway_access_log_destination_arn` for structured REST access logs. API Gateway REST execution access logging requires an account-wide CloudWatch role, so the disposable module does not create or replace that shared setting. An adoption stack should manage the account role and destination log group once, pass the group ARN to this module, and set retention centrally. Prompt and response bodies should remain excluded unless a reviewed data policy explicitly permits them.

A supported service also needs private network paths, customer-managed KMS keys, artifact checksum or signature verification, Bedrock invocation logging, quota alarms, budget alarms, a documented rollback process, encrypted remote Terraform state, key rotation, and an owner for imported-model costs.
