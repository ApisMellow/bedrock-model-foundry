data "archive_file" "endpoint" {
  type        = "zip"
  source_dir  = "${path.module}/../src/endpoint"
  output_path = "${path.module}/endpoint.zip"
}

module "endpoint" {
  for_each = var.endpoints
  source   = "./modules/guarded_endpoint"

  name                                   = each.key
  project_name                           = var.project_name
  model_arn                              = local.imported_model_arn
  lambda_zip_path                        = data.archive_file.endpoint.output_path
  lambda_zip_hash                        = data.archive_file.endpoint.output_base64sha256
  guardrail_type                         = each.value.guardrail_type
  pii_entities                           = each.value.pii_entities
  topic_name                             = each.value.topic_name
  topic_definition                       = each.value.topic_definition
  topic_examples                         = each.value.topic_examples
  rate_limit                             = each.value.rate_limit
  burst_limit                            = each.value.burst_limit
  monthly_quota                          = each.value.monthly_quota
  log_retention_days                     = var.log_retention_days
  api_gateway_access_log_destination_arn = var.api_gateway_access_log_destination_arn

  tags = merge(each.value.tags, {
    Endpoint = each.key
    Model    = local.imported_model_name
  })
}
