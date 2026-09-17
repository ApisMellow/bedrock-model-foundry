data "aws_iam_policy_document" "bedrock_import_assume" {
  statement {
    sid     = "BedrockModelImport"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values = [
        "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:model-import-job/*"
      ]
    }
  }
}

resource "aws_iam_role" "bedrock_import" {
  name               = "${var.project_name}-bedrock-import"
  assume_role_policy = data.aws_iam_policy_document.bedrock_import_assume.json
}

data "aws_iam_policy_document" "bedrock_import" {
  statement {
    sid       = "ReadModelPrefix"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.models.arn}/${local.model_prefix}/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid       = "ListModelPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.models.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.model_prefix}/*"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role_policy" "bedrock_import" {
  name   = "${var.project_name}-read-model"
  role   = aws_iam_role.bedrock_import.id
  policy = data.aws_iam_policy_document.bedrock_import.json
}

data "aws_iam_policy_document" "codebuild_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild" {
  name               = "${var.project_name}-lifecycle"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume.json
}

data "aws_iam_policy_document" "codebuild" {
  statement {
    sid = "ModelObjects"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:PutObject",
    ]
    resources = [
      aws_s3_bucket.models.arn,
      "${aws_s3_bucket.models.arn}/*",
    ]
  }

  statement {
    sid = "ModelImport"
    actions = [
      "bedrock:CreateModelImportJob",
      "bedrock:DeleteImportedModel",
      "bedrock:GetImportedModel",
      "bedrock:GetModelImportJob",
      "bedrock:ListImportedModels",
      "bedrock:ListModelImportJobs",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "PassImportRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.bedrock_import.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["bedrock.amazonaws.com"]
    }
  }

  statement {
    sid = "ModelParameter"
    actions = [
      "ssm:GetParameter",
      "ssm:PutParameter",
    ]
    resources = [aws_ssm_parameter.imported_model_arn.arn]
  }

  statement {
    sid = "BuildLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.codebuild.arn}:*"]
  }
}

resource "aws_iam_role_policy" "codebuild" {
  name   = "${var.project_name}-lifecycle"
  role   = aws_iam_role.codebuild.id
  policy = data.aws_iam_policy_document.codebuild.json
}
