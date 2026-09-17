locals {
  model               = var.models["default"]
  model_prefix        = local.model.s3_prefix
  imported_model_name = "${var.project_name}-${local.model.imported_model_name}"
  script_key          = "automation/model_lifecycle.py"
  # The SSM provider marks every parameter value sensitive. The handoff value is a
  # model ARN, not a secret, so unmark it once here for outputs and endpoint wiring.
  imported_model_arn = nonsensitive(data.aws_ssm_parameter.imported_model_arn.value)
  model_deployment_id = sha256(jsonencode({
    project_name   = var.project_name
    region         = var.aws_region
    model          = local.model
    lifecycle_code = aws_s3_object.lifecycle_script.etag
  }))
  lifecycle_identity = {
    project_name          = aws_codebuild_project.lifecycle.name
    region                = var.aws_region
    hf_model_id           = local.model.hf_model_id
    model_revision        = local.model.revision
    bucket                = aws_s3_bucket.models.id
    prefix                = local.model_prefix
    parameter_name        = aws_ssm_parameter.imported_model_arn.name
    import_role_arn       = aws_iam_role.bedrock_import.arn
    imported_model_name   = local.imported_model_name
    lifecycle_script_key  = aws_s3_object.lifecycle_script.key
    lifecycle_script_etag = aws_s3_object.lifecycle_script.etag
    model_deployment_id   = local.model_deployment_id
  }
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
      value = local.model.hf_model_id
    }

    environment_variable {
      name  = "MODEL_REVISION"
      value = local.model.revision
    }

    environment_variable {
      name  = "MODEL_DEPLOYMENT_ID"
      value = local.model_deployment_id
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

# This resource is created before the fallible import. It stays in state even when
# the import resource becomes tainted, so a later destroy can still run cleanup.
resource "terraform_data" "model_cleanup" {
  input = local.lifecycle_identity

  triggers_replace = [local.lifecycle_identity]

  provisioner "local-exec" {
    when    = destroy
    command = "bash ${path.module}/../scripts/run-codebuild.sh cleanup"
    environment = {
      CODEBUILD_PROJECT    = self.output.project_name
      AWS_REGION           = self.output.region
      HF_MODEL_ID          = self.output.hf_model_id
      MODEL_REVISION       = self.output.model_revision
      MODEL_DEPLOYMENT_ID  = self.output.model_deployment_id
      S3_BUCKET            = self.output.bucket
      S3_PREFIX            = self.output.prefix
      SSM_PARAMETER_NAME   = self.output.parameter_name
      IMPORT_ROLE_ARN      = self.output.import_role_arn
      IMPORTED_MODEL_NAME  = self.output.imported_model_name
      LIFECYCLE_SCRIPT_KEY = self.output.lifecycle_script_key
    }
  }

  depends_on = [
    aws_iam_role_policy.codebuild,
    aws_iam_role_policy.bedrock_import,
    aws_s3_object.lifecycle_script,
  ]
}

resource "terraform_data" "model_import" {
  input = local.lifecycle_identity

  triggers_replace = [local.lifecycle_identity]

  provisioner "local-exec" {
    command = "bash ${path.module}/../scripts/run-codebuild.sh import"
    environment = {
      CODEBUILD_PROJECT    = self.output.project_name
      AWS_REGION           = self.output.region
      HF_MODEL_ID          = self.output.hf_model_id
      MODEL_REVISION       = self.output.model_revision
      MODEL_DEPLOYMENT_ID  = self.output.model_deployment_id
      S3_BUCKET            = self.output.bucket
      S3_PREFIX            = self.output.prefix
      SSM_PARAMETER_NAME   = self.output.parameter_name
      IMPORT_ROLE_ARN      = self.output.import_role_arn
      IMPORTED_MODEL_NAME  = self.output.imported_model_name
      LIFECYCLE_SCRIPT_KEY = self.output.lifecycle_script_key
    }
  }

  depends_on = [terraform_data.model_cleanup]
}

data "aws_ssm_parameter" "imported_model_arn" {
  name       = aws_ssm_parameter.imported_model_arn.name
  depends_on = [terraform_data.model_import]
}
