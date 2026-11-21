variable "vpc_id" {
  description = "VPC the RCA Lambda's ENIs are attached in (needed to reach Loki on the monitoring EC2 over its private IP)"
  type        = string
}

variable "lambda_subnet_ids" {
  description = "Private subnet IDs for the RCA Lambda's VPC config — reuses the existing RDS subnets (module.network.private_subnet_ids[4:6]), which already have NAT egress and no EC2 vCPU quota implications since Lambda ENIs don't count against it"
  type        = list(string)
}

variable "ses_identity_arn" {
  description = "ARN of the already-verified SES email identity (aws_sesv2_email_identity.alerts) the RCA Lambda sends from"
  type        = string
}

variable "alert_email" {
  description = "Address RCA emails are sent from and to — same verified address Alertmanager's SMTP already uses (SES sandbox requires both sender and recipient verified)"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "account_id" {
  description = "AWS account ID, used to make the dashboard S3 bucket name globally unique"
  type        = string
}

variable "claude_model" {
  description = "Claude model ID the RCA Lambda calls. Defaults to Haiku, not Sonnet -- this is a structured log-summarization task, not deep multi-step reasoning, and Haiku costs dramatically less per token. Override to a Sonnet/Opus model ID for incidents that genuinely need deeper reasoning. Ignored when var.llm_provider isn't \"anthropic\" -- pass the other provider's model ID here instead (the Lambda reads whichever provider it's using out of this same field)."
  type        = string
  default     = "claude-haiku-4-5-20251001"
}

variable "llm_provider" {
  description = "Which LLM API the RCA Lambda calls: \"anthropic\" (default), \"openai\", or \"gemini\". Switching providers still uses var.llm_api_key / the /bookstore/llm-api-key secret to hold whichever provider's key you're using, and var.claude_model to hold that provider's model ID -- only the provider selector itself is a separate variable."
  type        = string
  default     = "anthropic"

  validation {
    condition     = contains(["anthropic", "openai", "gemini"], var.llm_provider)
    error_message = "llm_provider must be one of: anthropic, openai, gemini."
  }
}

variable "log_window_minutes" {
  description = "Minutes before/after the alert's firing timestamp to query Loki for (spec: ±5 min)"
  type        = number
  default     = 5
}

variable "max_log_lines_per_service" {
  description = "Cap on log lines per service included in the Claude prompt. The dominant token-cost driver here is prompt size (up to 5 services' logs in one prompt) -- this bounds it regardless of how noisy a service's logging is, without dropping any service from the cross-service correlation the RCA pipeline is built around."
  type        = number
  default     = 12
}

variable "max_log_line_chars" {
  description = "Cap on characters per individual log line included in the Claude prompt -- truncates (not drops) any single line longer than this, so one verbose stack-trace line can't blow out the token budget on its own."
  type        = number
  default     = 400
}

variable "claude_max_tokens" {
  description = "max_tokens on the Claude API call -- caps output token cost. 700 is comfortable for a root-cause + affected-tier + suggested-fix answer with line citations."
  type        = number
  default     = 700
}

variable "llm_api_key" {
  description = "Real API key (for whichever provider var.llm_provider selects) to populate /bookstore/llm-api-key with. Empty string (default) leaves the secret as an empty shell for manual population later -- set LLM_API_KEY in config.env + run scripts/configure.py instead of hand-editing terraform.tfvars directly, same convention as every other config.env-sourced variable in this project."
  type        = string
  default     = ""
  sensitive   = true
}

variable "secrets_recovery_window_days" {
  description = "recovery_window_in_days for the llm_api_key secret. 0 = force delete (this project's dev-cycle default, see TF-012); 7-30 for a real production account."
  type        = number
  default     = 0
}

variable "rca_report_retention_days" {
  description = "Days an RCA report survives in DynamoDB before TTL deletes it. RCA reports may contain raw log excerpts (potentially including request data routed through the Claude API) -- see docs/compliance/DATA_CLASSIFICATION_RETENTION_POLICY.md for the retention rationale. 400 matches this project's CloudTrail retention baseline (1 year + margin) since a report is itself an incident/audit artifact."
  type        = number
  default     = 400
}
