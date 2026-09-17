terraform {
  required_version = ">= 1.6.0"

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.7.0, < 3.0.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.11.0, < 7.0.0"
    }
  }
}
