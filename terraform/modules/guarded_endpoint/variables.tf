variable "name" { type = string }
variable "project_name" { type = string }
variable "model_arn" { type = string }
variable "lambda_zip_path" { type = string }
variable "lambda_zip_hash" { type = string }

variable "guardrail_type" {
  type = string
  validation {
    condition     = contains(["pii", "topic"], var.guardrail_type)
    error_message = "guardrail_type must be pii or topic."
  }
}

variable "pii_entities" {
  type    = set(string)
  default = []
}

variable "topic_name" {
  type    = string
  default = null
}

variable "topic_definition" {
  type    = string
  default = null
}

variable "topic_examples" {
  type    = list(string)
  default = []
}

variable "rate_limit" {
  type    = number
  default = 2
}

variable "burst_limit" {
  type    = number
  default = 4
}

variable "monthly_quota" {
  type    = number
  default = 1000
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
