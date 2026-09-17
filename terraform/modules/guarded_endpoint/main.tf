data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_bedrock_guardrail" "this" {
  name                      = "${var.project_name}-${var.name}"
  description               = "Endpoint-specific guardrail for ${var.name}."
  blocked_input_messaging   = "Blocked by the ${var.name} endpoint guardrail."
  blocked_outputs_messaging = "Blocked by the ${var.name} endpoint guardrail."

  dynamic "sensitive_information_policy_config" {
    for_each = var.guardrail_type == "pii" ? [1] : []

    content {
      dynamic "pii_entities_config" {
        for_each = var.pii_entities

        content {
          type           = pii_entities_config.value
          action         = "ANONYMIZE"
          input_action   = "ANONYMIZE"
          output_action  = "ANONYMIZE"
          input_enabled  = true
          output_enabled = true
        }
      }
    }
  }

  dynamic "topic_policy_config" {
    for_each = var.guardrail_type == "topic" ? [1] : []

    content {
      topics_config {
        name       = var.topic_name
        definition = var.topic_definition
        examples   = var.topic_examples
        type       = "DENY"
      }

      tier_config {
        tier_name = "CLASSIC"
      }
    }
  }

  tags = var.tags
}

resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Terraform-pinned version for ${var.name}."
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project_name}-${var.name}-proxy"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "InvokeImportedModel"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = [var.model_arn]
  }

  statement {
    sid     = "ApplyEndpointGuardrail"
    effect  = "Allow"
    actions = ["bedrock:ApplyGuardrail"]
    resources = [
      aws_bedrock_guardrail.this.guardrail_arn,
      "${aws_bedrock_guardrail.this.guardrail_arn}:*",
    ]
  }

  statement {
    sid = "WriteFunctionLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.project_name}-${var.name}-proxy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-${var.name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "proxy" {
  function_name    = "${var.project_name}-${var.name}"
  description      = "Pins one imported model and one guardrail for ${var.name}."
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "app.lambda_handler"
  filename         = var.lambda_zip_path
  source_code_hash = var.lambda_zip_hash
  timeout          = 25
  memory_size      = 256

  environment {
    variables = {
      MODEL_ARN          = var.model_arn
      GUARDRAIL_ID       = aws_bedrock_guardrail.this.guardrail_id
      GUARDRAIL_VERSION  = aws_bedrock_guardrail_version.this.version
      GUARDRAIL_BEHAVIOR = var.guardrail_type == "pii" ? "anonymize" : "block"
    }
  }

  depends_on = [
    aws_iam_role_policy.lambda,
    aws_cloudwatch_log_group.lambda,
  ]

  tags = var.tags
}

resource "aws_api_gateway_rest_api" "this" {
  name        = "${var.project_name}-${var.name}"
  description = "Disposable guarded endpoint for ${var.name}."

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = var.tags
}

resource "aws_api_gateway_resource" "v1" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "v1"
}

resource "aws_api_gateway_resource" "chat" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.v1.id
  path_part   = "chat"
}

resource "aws_api_gateway_resource" "completions" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_resource.chat.id
  path_part   = "completions"
}

resource "aws_api_gateway_method" "post" {
  rest_api_id      = aws_api_gateway_rest_api.this.id
  resource_id      = aws_api_gateway_resource.completions.id
  http_method      = "POST"
  authorization    = "NONE"
  api_key_required = true
}

resource "aws_api_gateway_integration" "post" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.completions.id
  http_method             = aws_api_gateway_method.post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.proxy.invoke_arn
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.proxy.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.this.execution_arn}/*/POST/v1/chat/completions"
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.completions.id,
      aws_api_gateway_method.post.id,
      aws_api_gateway_integration.post.id,
      aws_lambda_function.proxy.source_code_hash,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [aws_api_gateway_integration.post]
}

resource "aws_api_gateway_stage" "demo" {
  deployment_id = aws_api_gateway_deployment.this.id
  rest_api_id   = aws_api_gateway_rest_api.this.id
  stage_name    = "demo"
  tags          = var.tags
}

resource "aws_api_gateway_api_key" "this" {
  name    = "${var.project_name}-${var.name}"
  enabled = true
  tags    = var.tags
}

resource "aws_api_gateway_usage_plan" "this" {
  name = "${var.project_name}-${var.name}"

  api_stages {
    api_id = aws_api_gateway_rest_api.this.id
    stage  = aws_api_gateway_stage.demo.stage_name
  }

  quota_settings {
    limit  = var.monthly_quota
    period = "MONTH"
  }

  throttle_settings {
    burst_limit = var.burst_limit
    rate_limit  = var.rate_limit
  }

  tags = var.tags
}

resource "aws_api_gateway_usage_plan_key" "this" {
  key_id        = aws_api_gateway_api_key.this.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.this.id
}
