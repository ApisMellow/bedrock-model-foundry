locals {
  policy_summary = {
    for name, endpoint in var.endpoints :
    name => endpoint.guardrail_type == "pii" ? "anonymizes ${lower(join(", ", sort(tolist(endpoint.pii_entities))))}" : "blocks ${endpoint.topic_name}"
  }

  endpoint_summary = join("\n", [
    for name in sort(keys(var.endpoints)) : join("\n", [
      format("  %-13s %s", name, module.endpoint[name].url),
      format("  %-13s guardrail %s v%s - %s", "", module.endpoint[name].guardrail_id, module.endpoint[name].guardrail_version, local.policy_summary[name]),
    ])
  ])

  demo_summary = <<-EOT
    Bedrock Model Foundry - ${var.aws_region} / ${data.aws_caller_identity.current.account_id}

    endpoints
    ${local.endpoint_summary}

    model         ${local.imported_model_arn}
    build project ${aws_codebuild_project.lifecycle.name}

    api keys      terraform -chdir=terraform output -json endpoint_api_keys
                  (the harness config holds them; they are never printed here)

    next          python3 scripts/generate-harness-config.py
                  python3 harness/foundry_shim.py
                  opencode
  EOT
}

# Printed at the end of every apply, and written to demo-endpoints.txt by
# scripts/generate-harness-config.py so a second window can hold it during a
# demonstration. Deliberately free of API keys: this text is meant to be shown.
output "demo_summary" {
  description = "Readable deployment summary. Contains no secret."
  value       = local.demo_summary
}
