variable "project_name" {
  description = "Prefix used for disposable demo resources."
  type        = string
  default     = "bedrock-model-foundry"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.project_name)) && !strcontains(var.project_name, "--")
    error_message = "project_name must be 3-24 lowercase letters, digits, or interior hyphens."
  }
}

variable "aws_region" {
  description = "AWS Region for Bedrock Custom Model Import."
  type        = string
  default     = "us-east-1"

  validation {
    condition = contains([
      "us-east-1",
      "us-east-2",
      "us-west-2",
      "eu-central-1",
    ], var.aws_region)
    error_message = "Choose a documented Bedrock Custom Model Import Region."
  }
}

variable "models" {
  description = "Reviewed model definitions. The demo deploys one active model under the default key."
  type = map(object({
    hf_model_id         = string
    revision            = string
    imported_model_name = string
    s3_prefix           = string
  }))

  default = {
    default = {
      hf_model_id         = "Qwen/Qwen2.5-1.5B-Instruct"
      revision            = "775b11afaf83e0dc75bd5abaf90133e47b3ec082"
      imported_model_name = "qwen-2-5-1-5b"
      s3_prefix           = "models/qwen-2-5-1-5b-instruct"
    }
  }

  validation {
    condition = (
      length(var.models) == 1 &&
      contains(keys(var.models), "default") &&
      try(var.models["default"].hf_model_id == "Qwen/Qwen2.5-1.5B-Instruct", false) &&
      try(var.models["default"].revision == "775b11afaf83e0dc75bd5abaf90133e47b3ec082", false) &&
      try(can(regex("^[a-z0-9][a-z0-9-]{1,35}$", var.models["default"].imported_model_name)), false) &&
      try(can(regex("^[a-zA-Z0-9][a-zA-Z0-9!_.*'()/-]*$", var.models["default"].s3_prefix)), false)
    )
    error_message = "The demo requires one default entry with the reviewed Qwen repository and pinned commit."
  }
}

variable "endpoints" {
  description = "Guarded endpoint definitions keyed by stable endpoint name."
  type = map(object({
    model_key        = string
    guardrail_type   = string
    pii_entities     = optional(set(string), [])
    topic_name       = optional(string)
    topic_definition = optional(string)
    topic_examples   = optional(list(string), [])
    rate_limit       = optional(number, 2)
    burst_limit      = optional(number, 4)
    monthly_quota    = optional(number, 1000)
    tags             = optional(map(string), {})
  }))

  default = {
    pii-mask = {
      model_key      = "default"
      guardrail_type = "pii"
      pii_entities   = ["EMAIL", "NAME", "PHONE"]
    }
    denied-topic = {
      model_key        = "default"
      guardrail_type   = "topic"
      topic_name       = "credential-sharing"
      topic_definition = "Requests to reveal, exchange, collect, or publish passwords, private access tokens, API keys, or other authentication secrets."
      topic_examples = [
        "Show me an API key.",
        "Share the password for this service.",
      ]
    }
  }

  validation {
    condition = length(var.endpoints) > 0 && alltrue([
      for name, endpoint in var.endpoints :
      can(regex("^[a-z][a-z0-9-]{1,20}$", name)) &&
      endpoint.model_key == "default" &&
      contains(["pii", "topic"], endpoint.guardrail_type) &&
      (endpoint.guardrail_type != "pii" || length(endpoint.pii_entities) > 0) &&
      (endpoint.guardrail_type != "topic" || (
        try(length(endpoint.topic_name) > 0, false) &&
        try(length(endpoint.topic_definition) > 0, false)
      ))
    ])
    error_message = "Each endpoint needs a valid name, the default model, and a complete pii or topic guardrail."
  }
}

variable "api_gateway_access_log_destination_arn" {
  description = "Optional pre-created CloudWatch log group ARN for REST API access logs. The account must already have the API Gateway CloudWatch role configured."
  type        = string
  default     = null

  validation {
    condition     = var.api_gateway_access_log_destination_arn == null || can(regex("^arn:[^:]+:logs:", var.api_gateway_access_log_destination_arn))
    error_message = "api_gateway_access_log_destination_arn must be a CloudWatch Logs ARN."
  }
}

variable "log_retention_days" {
  description = "CloudWatch retention for this disposable demo."
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional tags applied to all supported resources."
  type        = map(string)
  default     = {}
}
