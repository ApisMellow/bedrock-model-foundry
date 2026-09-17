provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(var.tags, {
      Project   = var.project_name
      ManagedBy = "Terraform"
      Lifecycle = "DisposableDemo"
    })
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
