output "reports_table_name" {
  description = "DynamoDB table name holding RCA reports"
  value       = aws_dynamodb_table.rca_reports.name
}

output "lambda_security_group_id" {
  description = "Security group ID attached to the RCA Lambda's ENIs — monitoring-ec2's SG needs an ingress rule from this to allow the Lambda to reach Loki on port 3100"
  value       = aws_security_group.rca_lambda.id
}

output "webhook_invoke_url" {
  description = "Full invoke URL (including stage and /webhook path) Alertmanager should POST alerts to"
  value       = "${aws_api_gateway_stage.rca_webhook.invoke_url}/webhook"
}

output "webhook_rest_api_id" {
  description = "REST API ID — used by the root module to attach an IP-restricted resource policy without creating a circular module dependency"
  value       = aws_api_gateway_rest_api.rca_webhook.id
}

output "webhook_rest_api_arn" {
  description = "REST API execution ARN, used in the root-level resource policy's Resource field"
  value       = aws_api_gateway_rest_api.rca_webhook.execution_arn
}

output "dashboard_url" {
  description = "CloudFront URL serving the static RCA dashboard"
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
}

output "dashboard_read_api_url" {
  description = "Base URL of the dashboard-read HTTP API — the static dashboard's script.js calls $${this}/reports"
  value       = aws_apigatewayv2_stage.dashboard_read.invoke_url
}
