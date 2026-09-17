variable "project_name" {
  description = "Prefix used for disposable demo resources."
  type        = string
  default     = "bedrock-model-foundry"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.project_name))
    error_message = "project_name must be 3-32 lowercase letters, digits, or hyphens."
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

variable "hf_model_id" {
  description = "Approved ungated Hugging Face model repository."
  type        = string
  default     = "Qwen/Qwen2.5-1.5B-Instruct"

  validation {
    condition     = var.hf_model_id == "Qwen/Qwen2.5-1.5B-Instruct"
    error_message = "This demo intentionally allows only the reviewed Qwen repository."
  }
}

variable "model_revision" {
  description = "Immutable Hugging Face commit used by the remote CodeBuild download."
  type        = string
  default     = "775b11afaf83e0dc75bd5abaf90133e47b3ec082"

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.model_revision))
    error_message = "model_revision must be a full 40-character commit hash."
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
