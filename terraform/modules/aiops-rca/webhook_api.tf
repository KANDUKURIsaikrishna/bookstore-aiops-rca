resource "aws_api_gateway_rest_api" "rca_webhook" {
  name = "bookstore-rca-webhook"
  # No policy set here -- the source-IP restriction is attached at root
  # level (terraform/main.tf) via aws_api_gateway_rest_api_policy, once
  # both this module's output and monitoring_ec2's public IP output exist.
  # Setting it here would make this module depend on monitoring_ec2,
  # recreating the circular dependency this plan's architecture section
  # describes avoiding.
}

resource "aws_api_gateway_resource" "webhook" {
  rest_api_id = aws_api_gateway_rest_api.rca_webhook.id
  parent_id   = aws_api_gateway_rest_api.rca_webhook.root_resource_id
  path_part   = "webhook"
}

resource "aws_api_gateway_method" "webhook_post" {
  rest_api_id   = aws_api_gateway_rest_api.rca_webhook.id
  resource_id   = aws_api_gateway_resource.webhook.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "webhook_lambda" {
  rest_api_id             = aws_api_gateway_rest_api.rca_webhook.id
  resource_id             = aws_api_gateway_resource.webhook.id
  http_method             = aws_api_gateway_method.webhook_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.rca.invoke_arn
}

resource "aws_lambda_permission" "webhook_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rca.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.rca_webhook.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "rca_webhook" {
  rest_api_id = aws_api_gateway_rest_api.rca_webhook.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.webhook.id,
      aws_api_gateway_method.webhook_post.id,
      aws_api_gateway_integration.webhook_lambda.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "rca_webhook" {
  deployment_id = aws_api_gateway_deployment.rca_webhook.id
  rest_api_id   = aws_api_gateway_rest_api.rca_webhook.id
  stage_name    = "prod"
}
