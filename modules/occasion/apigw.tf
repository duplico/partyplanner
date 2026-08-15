resource "aws_apigatewayv2_api" "rsvp" {
  name          = "${local.name}-rsvp"
  protocol_type = "HTTP"
  tags          = local.tags
}

resource "aws_apigatewayv2_integration" "rsvp" {
  api_id                 = aws_apigatewayv2_api.rsvp.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.rsvp.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "rsvp" {
  api_id    = aws_apigatewayv2_api.rsvp.id
  route_key = "ANY /api/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.rsvp.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.rsvp.id
  name        = "$default"
  auto_deploy = true
  tags        = local.tags

  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate
    throttling_burst_limit = var.api_throttle_burst
  }
}
