locals {
  model_prefix        = "models/qwen-2-5-1-5b-instruct"
  imported_model_name = "${var.project_name}-qwen-2-5-1-5b"
  script_key          = "automation/model_lifecycle.py"
}

resource "aws_s3_bucket" "models" {
  bucket        = "${var.project_name}-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "models" {
  bucket = aws_s3_bucket.models.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "models" {
  bucket = aws_s3_bucket.models.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "models" {
  bucket = aws_s3_bucket.models.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_object" "lifecycle_script" {
  bucket       = aws_s3_bucket.models.id
  key          = local.script_key
  source       = "${path.module}/../src/model_lifecycle.py"
  etag         = filemd5("${path.module}/../src/model_lifecycle.py")
  content_type = "text/x-python"
}

resource "aws_ssm_parameter" "imported_model_arn" {
  name        = "/${var.project_name}/imported-model-arn"
  description = "Handoff from the cloud model-import job to Terraform."
  type        = "String"
  value       = "PENDING"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/${var.project_name}-model-lifecycle"
  retention_in_days = var.log_retention_days
}

resource "aws_codebuild_project" "lifecycle" {
  name           = "${var.project_name}-model-lifecycle"
  description    = "Cloud-only Hugging Face download and Bedrock model lifecycle."
  service_role   = aws_iam_role.codebuild.arn
  build_timeout  = 180
  queued_timeout = 30

  artifacts {
    type = "NO_ARTIFACTS"
  }

  source {
    type      = "NO_SOURCE"
    buildspec = file("${path.module}/buildspec.yml")
  }

  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/amazonlinux-x86_64-standard:6.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "ACTION"
      value = "import"
    }

    environment_variable {
      name  = "HF_MODEL_ID"
      value = var.hf_model_id
    }

    environment_variable {
      name  = "MODEL_REVISION"
      value = var.model_revision
    }

    environment_variable {
      name  = "S3_BUCKET"
      value = aws_s3_bucket.models.id
    }

    environment_variable {
      name  = "S3_PREFIX"
      value = local.model_prefix
    }

    environment_variable {
      name  = "SSM_PARAMETER_NAME"
      value = aws_ssm_parameter.imported_model_arn.name
    }

    environment_variable {
      name  = "IMPORT_ROLE_ARN"
      value = aws_iam_role.bedrock_import.arn
    }

    environment_variable {
      name  = "IMPORTED_MODEL_NAME"
      value = local.imported_model_name
    }

    environment_variable {
      name  = "LIFECYCLE_SCRIPT_KEY"
      value = aws_s3_object.lifecycle_script.key
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.codebuild.name
      stream_name = "lifecycle"
    }
  }
}

resource "terraform_data" "model_lifecycle" {
  input = {
    project_name = aws_codebuild_project.lifecycle.name
    region       = var.aws_region
  }

  triggers_replace = [
    var.hf_model_id,
    var.model_revision,
    aws_s3_object.lifecycle_script.etag,
  ]

  provisioner "local-exec" {
    command = "bash ${path.module}/../scripts/run-codebuild.sh import"
    environment = {
      CODEBUILD_PROJECT = self.output.project_name
      AWS_REGION        = self.output.region
    }
  }

  provisioner "local-exec" {
    when    = destroy
    command = "bash ${path.module}/../scripts/run-codebuild.sh cleanup"
    environment = {
      CODEBUILD_PROJECT = self.output.project_name
      AWS_REGION        = self.output.region
    }
  }

  depends_on = [
    aws_iam_role_policy.codebuild,
    aws_iam_role_policy.bedrock_import,
    aws_s3_object.lifecycle_script,
  ]
}

data "aws_ssm_parameter" "imported_model_arn" {
  name       = aws_ssm_parameter.imported_model_arn.name
  depends_on = [terraform_data.model_lifecycle]
}
