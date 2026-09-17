output "imported_model_arn" {
  description = "ARN published by the successful remote import."
  value       = local.imported_model_arn
}

output "endpoint_urls" {
  description = "Guarded API endpoint URLs by endpoint name."
  value       = { for name, endpoint in module.endpoint : name => endpoint.url }
}

output "endpoint_api_keys" {
  description = "Sensitive demo API keys by endpoint name."
  value       = { for name, endpoint in module.endpoint : name => endpoint.api_key }
  sensitive   = true
}

output "guardrails" {
  value = {
    for name, endpoint in module.endpoint : name => {
      id      = endpoint.guardrail_id
      version = endpoint.guardrail_version
    }
  }
}

output "model_lifecycle_codebuild_project" {
  value = aws_codebuild_project.lifecycle.name
}
