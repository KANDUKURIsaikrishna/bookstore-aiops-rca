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
  description = "Claude model ID the RCA Lambda calls"
  type        = string
  default     = "claude-sonnet-5"
}

variable "log_window_minutes" {
  description = "Minutes before/after the alert's firing timestamp to query Loki for (spec: ±5 min)"
  type        = number
  default     = 5
}
