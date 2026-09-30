# aiops-rca

AIOps root-cause-analysis pipeline: an IP-restricted API Gateway webhook receives Alertmanager's firing alerts and invokes a VPC-attached Lambda that queries Loki, calls an LLM API (Anthropic Claude by default; OpenAI/Gemini via `var.llm_provider`), writes a narrative report to DynamoDB, and emails it via SES — failed invocations land in an SQS DLQ. A second, non-VPC Lambda behind an open HTTP API serves those reports to a static S3+CloudFront dashboard. This module never references anything from `monitoring-ec2` (see [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#aiops-rca-pipeline) for the full flow and why the dependency between the two only ever runs one way).


<!-- BEGIN_TF_DOCS -->
## Requirements

No requirements.

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_archive"></a> [archive](#provider\_archive) | n/a |
| <a name="provider_aws"></a> [aws](#provider\_aws) | n/a |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [aws_api_gateway_deployment.rca_webhook](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_deployment) | resource |
| [aws_api_gateway_integration.webhook_lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_integration) | resource |
| [aws_api_gateway_method.webhook_post](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_method) | resource |
| [aws_api_gateway_resource.webhook](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_resource) | resource |
| [aws_api_gateway_rest_api.rca_webhook](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_rest_api) | resource |
| [aws_api_gateway_stage.rca_webhook](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_stage) | resource |
| [aws_apigatewayv2_api.dashboard_read](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apigatewayv2_api) | resource |
| [aws_apigatewayv2_integration.dashboard_read](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apigatewayv2_integration) | resource |
| [aws_apigatewayv2_route.get_report](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apigatewayv2_route) | resource |
| [aws_apigatewayv2_route.list_reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apigatewayv2_route) | resource |
| [aws_apigatewayv2_stage.dashboard_read](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apigatewayv2_stage) | resource |
| [aws_cloudfront_distribution.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_distribution) | resource |
| [aws_cloudfront_origin_access_control.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudfront_origin_access_control) | resource |
| [aws_dynamodb_table.rca_reports](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/dynamodb_table) | resource |
| [aws_iam_role.dashboard_read_lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role.rca_lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role) | resource |
| [aws_iam_role_policy.dashboard_read_lambda_inline](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy.rca_lambda_inline](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy) | resource |
| [aws_iam_role_policy_attachment.dashboard_read_lambda_basic_logs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy_attachment) | resource |
| [aws_iam_role_policy_attachment.rca_lambda_vpc_access](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_role_policy_attachment) | resource |
| [aws_lambda_function.dashboard_read](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_lambda_function.rca](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_function) | resource |
| [aws_lambda_permission.dashboard_read_invoke](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_lambda_permission.webhook_invoke](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_s3_bucket.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket) | resource |
| [aws_s3_bucket_policy.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_policy) | resource |
| [aws_s3_bucket_public_access_block.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_public_access_block) | resource |
| [aws_s3_bucket_server_side_encryption_configuration.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_server_side_encryption_configuration) | resource |
| [aws_s3_bucket_versioning.dashboard](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_versioning) | resource |
| [aws_s3_object.dashboard_index](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_object) | resource |
| [aws_s3_object.dashboard_script](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_object) | resource |
| [aws_s3_object.dashboard_style](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_object) | resource |
| [aws_secretsmanager_secret.llm_api_key](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/secretsmanager_secret) | resource |
| [aws_secretsmanager_secret_version.llm_api_key](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/secretsmanager_secret_version) | resource |
| [aws_security_group.rca_lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/security_group) | resource |
| [aws_sqs_queue.rca_dlq](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sqs_queue) | resource |
| [archive_file.dashboard_read_lambda](https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file) | data source |
| [archive_file.rca_lambda](https://registry.terraform.io/providers/hashicorp/archive/latest/docs/data-sources/file) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_account_id"></a> [account\_id](#input\_account\_id) | AWS account ID, used to make the dashboard S3 bucket name globally unique | `string` | n/a | yes |
| <a name="input_alert_email"></a> [alert\_email](#input\_alert\_email) | Address RCA emails are sent from and to — same verified address Alertmanager's SMTP already uses (SES sandbox requires both sender and recipient verified) | `string` | n/a | yes |
| <a name="input_claude_max_tokens"></a> [claude\_max\_tokens](#input\_claude\_max\_tokens) | max\_tokens on the Claude API call -- caps output token cost. 700 is comfortable for a root-cause + affected-tier + suggested-fix answer with line citations. | `number` | `700` | no |
| <a name="input_claude_model"></a> [claude\_model](#input\_claude\_model) | Claude model ID the RCA Lambda calls. Defaults to Haiku, not Sonnet -- this is a structured log-summarization task, not deep multi-step reasoning, and Haiku costs dramatically less per token. Override to a Sonnet/Opus model ID for incidents that genuinely need deeper reasoning. Ignored when var.llm\_provider isn't "anthropic" -- pass the other provider's model ID here instead (the Lambda reads whichever provider it's using out of this same field). | `string` | `"claude-haiku-4-5-20251001"` | no |
| <a name="input_lambda_subnet_ids"></a> [lambda\_subnet\_ids](#input\_lambda\_subnet\_ids) | Private subnet IDs for the RCA Lambda's VPC config — reuses the existing RDS subnets (module.network.private\_subnet\_ids[4:6]), which already have NAT egress and no EC2 vCPU quota implications since Lambda ENIs don't count against it | `list(string)` | n/a | yes |
| <a name="input_llm_api_key"></a> [llm\_api\_key](#input\_llm\_api\_key) | Real API key (for whichever provider var.llm\_provider selects) to populate /bookstore/llm-api-key with. Empty string (default) leaves the secret as an empty shell for manual population later -- set LLM\_API\_KEY in config.env + run scripts/configure.py instead of hand-editing terraform.tfvars directly, same convention as every other config.env-sourced variable in this project. | `string` | `""` | no |
| <a name="input_llm_provider"></a> [llm\_provider](#input\_llm\_provider) | Which LLM API the RCA Lambda calls: "anthropic" (default), "openai", or "gemini". Switching providers still uses var.llm\_api\_key / the /bookstore/llm-api-key secret to hold whichever provider's key you're using, and var.claude\_model to hold that provider's model ID -- only the provider selector itself is a separate variable. | `string` | `"anthropic"` | no |
| <a name="input_log_window_minutes"></a> [log\_window\_minutes](#input\_log\_window\_minutes) | Minutes before/after the alert's firing timestamp to query Loki for (spec: ±5 min) | `number` | `5` | no |
| <a name="input_max_log_line_chars"></a> [max\_log\_line\_chars](#input\_max\_log\_line\_chars) | Cap on characters per individual log line included in the Claude prompt -- truncates (not drops) any single line longer than this, so one verbose stack-trace line can't blow out the token budget on its own. | `number` | `400` | no |
| <a name="input_max_log_lines_per_service"></a> [max\_log\_lines\_per\_service](#input\_max\_log\_lines\_per\_service) | Cap on log lines per service included in the Claude prompt. The dominant token-cost driver here is prompt size (up to 5 services' logs in one prompt) -- this bounds it regardless of how noisy a service's logging is, without dropping any service from the cross-service correlation the RCA pipeline is built around. | `number` | `12` | no |
| <a name="input_rca_report_retention_days"></a> [rca\_report\_retention\_days](#input\_rca\_report\_retention\_days) | Days an RCA report survives in DynamoDB before TTL deletes it. RCA reports may contain raw log excerpts (potentially including request data routed through the Claude API) -- see docs/compliance/DATA\_CLASSIFICATION\_RETENTION\_POLICY.md for the retention rationale. 400 matches this project's CloudTrail retention baseline (1 year + margin) since a report is itself an incident/audit artifact. | `number` | `400` | no |
| <a name="input_region"></a> [region](#input\_region) | AWS region | `string` | n/a | yes |
| <a name="input_secrets_recovery_window_days"></a> [secrets\_recovery\_window\_days](#input\_secrets\_recovery\_window\_days) | recovery\_window\_in\_days for the llm\_api\_key secret. 0 = force delete (this project's dev-cycle default, see TF-012); 7-30 for a real production account. | `number` | `0` | no |
| <a name="input_ses_identity_arn"></a> [ses\_identity\_arn](#input\_ses\_identity\_arn) | ARN of the already-verified SES email identity (aws\_sesv2\_email\_identity.alerts) the RCA Lambda sends from | `string` | n/a | yes |
| <a name="input_vpc_id"></a> [vpc\_id](#input\_vpc\_id) | VPC the RCA Lambda's ENIs are attached in (needed to reach Loki on the monitoring EC2 over its private IP) | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_dashboard_read_api_url"></a> [dashboard\_read\_api\_url](#output\_dashboard\_read\_api\_url) | Base URL of the dashboard-read HTTP API — the static dashboard's script.js calls ${this}/reports |
| <a name="output_dashboard_url"></a> [dashboard\_url](#output\_dashboard\_url) | CloudFront URL serving the static RCA dashboard |
| <a name="output_lambda_security_group_id"></a> [lambda\_security\_group\_id](#output\_lambda\_security\_group\_id) | Security group ID attached to the RCA Lambda's ENIs — monitoring-ec2's SG needs an ingress rule from this to allow the Lambda to reach Loki on port 3100 |
| <a name="output_reports_table_name"></a> [reports\_table\_name](#output\_reports\_table\_name) | DynamoDB table name holding RCA reports |
| <a name="output_webhook_invoke_url"></a> [webhook\_invoke\_url](#output\_webhook\_invoke\_url) | Full invoke URL (including stage and /webhook path) Alertmanager should POST alerts to |
| <a name="output_webhook_rest_api_arn"></a> [webhook\_rest\_api\_arn](#output\_webhook\_rest\_api\_arn) | REST API execution ARN, used in the root-level resource policy's Resource field |
| <a name="output_webhook_rest_api_id"></a> [webhook\_rest\_api\_id](#output\_webhook\_rest\_api\_id) | REST API ID — used by the root module to attach an IP-restricted resource policy without creating a circular module dependency |
<!-- END_TF_DOCS -->
