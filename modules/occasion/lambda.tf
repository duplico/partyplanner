data "archive_file" "rsvp" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/.build/rsvp.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rsvp" {
  name               = "${local.name}-rsvp"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "rsvp" {
  statement {
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:UpdateItem",
    ]
    resources = [aws_dynamodb_table.occasion.arn]
  }

  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "rsvp" {
  name   = "rsvp"
  role   = aws_iam_role.rsvp.id
  policy = data.aws_iam_policy_document.rsvp.json
}

resource "aws_lambda_function" "rsvp" {
  function_name                  = "${local.name}-rsvp"
  role                           = aws_iam_role.rsvp.arn
  runtime                        = "python3.12"
  handler                        = "handler.lambda_handler"
  filename                       = data.archive_file.rsvp.output_path
  source_code_hash               = data.archive_file.rsvp.output_base64sha256
  timeout                        = 10
  memory_size                    = 128
  reserved_concurrent_executions = var.lambda_reserved_concurrency
  tags                           = local.tags

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.occasion.name
    }
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rsvp.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rsvp.execution_arn}/*/*"
}
