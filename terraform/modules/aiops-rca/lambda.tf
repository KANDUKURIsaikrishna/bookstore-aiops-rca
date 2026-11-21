data "archive_file" "rca_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../../lambdas/rca-lambda/lambda_function.py"
  output_path = "${path.module}/build/rca-lambda.zip"
}

resource "aws_lambda_function" "rca" {
  function_name    = "bookstore-rca-lambda"
  role             = aws_iam_role.rca_lambda.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.rca_lambda.output_path
  source_code_hash = data.archive_file.rca_lambda.output_base64sha256

  vpc_config {
    subnet_ids         = var.lambda_subnet_ids
    security_group_ids = [aws_security_group.rca_lambda.id]
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.rca_dlq.arn
  }

  environment {
    variables = {
      DYNAMODB_TABLE            = aws_dynamodb_table.rca_reports.name
      LLM_API_KEY_SECRET_ARN    = aws_secretsmanager_secret.llm_api_key.arn
      SES_FROM_EMAIL            = var.alert_email
      SES_TO_EMAIL              = var.alert_email
      LLM_PROVIDER              = var.llm_provider
      CLAUDE_MODEL              = var.claude_model
      LOG_WINDOW_MINUTES        = tostring(var.log_window_minutes)
      REPORT_RETENTION_DAYS     = tostring(var.rca_report_retention_days)
      MAX_LOG_LINES_PER_SERVICE = tostring(var.max_log_lines_per_service)
      MAX_LOG_LINE_CHARS        = tostring(var.max_log_line_chars)
      CLAUDE_MAX_TOKENS         = tostring(var.claude_max_tokens)
    }
  }
}
