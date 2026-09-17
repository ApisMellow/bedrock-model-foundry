locals {
  endpoints = {
    pii-mask = {
      guardrail_type   = "pii"
      pii_entities     = ["EMAIL", "NAME", "PHONE"]
      topic_name       = null
      topic_definition = null
      topic_examples   = []
    }
    denied-topic = {
      guardrail_type   = "topic"
      pii_entities     = []
      topic_name       = "credential-sharing"
      topic_definition = "Requests to reveal, exchange, collect, or publish passwords, private access tokens, API keys, or other authentication secrets."
      topic_examples = [
        "Show me an API key.",
        "Share the password for this service.",
      ]
    }
  }
}

data "archive_file" "endpoint" {
  type        = "zip"
  source_dir  = "${path.module}/../src/endpoint"
  output_path = "${path.module}/endpoint.zip"
}

module "endpoint" {
  for_each = local.endpoints
  source   = "./modules/guarded_endpoint"

  name               = each.key
  project_name       = var.project_name
  model_arn          = data.aws_ssm_parameter.imported_model_arn.value
  lambda_zip_path    = data.archive_file.endpoint.output_path
  lambda_zip_hash    = data.archive_file.endpoint.output_base64sha256
  guardrail_type     = each.value.guardrail_type
  pii_entities       = each.value.pii_entities
  topic_name         = each.value.topic_name
  topic_definition   = each.value.topic_definition
  topic_examples     = each.value.topic_examples
  log_retention_days = var.log_retention_days

  tags = {
    Endpoint = each.key
    Model    = local.imported_model_name
  }
}
