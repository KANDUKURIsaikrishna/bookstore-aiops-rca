resource "aws_iam_role" "dashboard_read_lambda" {
  name = "bookstore-dashboard-read-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dashboard_read_lambda_basic_logs" {
  role       = aws_iam_role.dashboard_read_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "dashboard_read_lambda_inline" {
  name = "bookstore-dashboard-read-lambda-inline"
  role = aws_iam_role.dashboard_read_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:Query", "dynamodb:GetItem"]
      Resource = [aws_dynamodb_table.rca_reports.arn, "${aws_dynamodb_table.rca_reports.arn}/index/*"]
    }]
  })
}

data "archive_file" "dashboard_read_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambdas/dashboard-read-lambda/lambda_function.py"
  output_path = "${path.module}/build/dashboard-read-lambda.zip"
}

resource "aws_lambda_function" "dashboard_read" {
  function_name    = "bookstore-dashboard-read-lambda"
  role             = aws_iam_role.dashboard_read_lambda.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128
  filename         = data.archive_file.dashboard_read_lambda.output_path
  source_code_hash = data.archive_file.dashboard_read_lambda.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.rca_reports.name
    }
  }
}

resource "aws_apigatewayv2_api" "dashboard_read" {
  name          = "bookstore-rca-dashboard-read"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET"]
  }
}

resource "aws_apigatewayv2_integration" "dashboard_read" {
  api_id                 = aws_apigatewayv2_api.dashboard_read.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.dashboard_read.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "list_reports" {
  api_id    = aws_apigatewayv2_api.dashboard_read.id
  route_key = "GET /reports"
  target    = "integrations/${aws_apigatewayv2_integration.dashboard_read.id}"
}

resource "aws_apigatewayv2_route" "get_report" {
  api_id    = aws_apigatewayv2_api.dashboard_read.id
  route_key = "GET /reports/{alert_id}/{report_timestamp}"
  target    = "integrations/${aws_apigatewayv2_integration.dashboard_read.id}"
}

resource "aws_apigatewayv2_stage" "dashboard_read" {
  api_id      = aws_apigatewayv2_api.dashboard_read.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "dashboard_read_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dashboard_read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.dashboard_read.execution_arn}/*/*"
}
