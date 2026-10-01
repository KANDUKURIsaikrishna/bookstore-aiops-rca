variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-west-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-\\d$", var.aws_region))
    error_message = "aws_region must look like a real AWS region code, e.g. us-west-1."
  }
}

variable "environment" {
  description = "Deployment environment tag applied to all resources"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "cost_center" {
  description = "Cost-allocation tag applied to every resource, for chargeback/FinOps grouping beyond Project/Environment"
  type        = string
  default     = "bookstore-platform"
}

variable "domain" {
  description = "Primary domain for ACM cert and ingress host rules (e.g. example.com)"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", var.domain))
    error_message = "domain must be a valid DNS domain, e.g. example.com."
  }
}

variable "github_repo" {
  description = "GitHub repository in owner/name format — scopes the OIDC CI role trust policy"
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repo))
    error_message = "github_repo must be in owner/repo format, e.g. octocat/hello-world."
  }
}

variable "secondary_region" {
  description = "Secondary AWS region for DR failover, IF enable_dr_replication is true (see that variable — this region alone does not turn replication on). Always needs a valid region string regardless, since the \"secondary\" provider alias in providers.tf is configured with it unconditionally. Default: us-west-2 (Oregon). Stamped from config.env's SECONDARY_REGION by scripts/configure.py (optional — omitting it there keeps this default). CloudFront ACM always uses us-east-1 regardless of this value."
  type        = string
  default     = "us-west-2"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-\\d$", var.secondary_region))
    error_message = "secondary_region must look like a real AWS region code, e.g. us-west-2."
  }
}

variable "enable_dr_replication" {
  description = "Turns on cross-region DR replication: the /bookstore/db-credentials Secrets Manager replica and ECR image replication into var.secondary_region. Off by default -- previously this was silently gated on secondary_region being non-empty, which it always was by default, so both replicas were created on every apply whether DR was wanted or not. See docs/TROUBLESHOOTING.md OBS-049."
  type        = bool
  default     = false
}

variable "primary_alb_dns" {
  description = "Nginx NLB DNS in primary region (us-west-1). Run: kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'. Leave empty to skip Route53 app records (first apply before EKS ready)."
  type        = string
  default     = ""
}

variable "secondary_alb_dns" {
  description = "Nginx NLB DNS in secondary region. Fill after secondary EKS is deployed. Leave empty to skip secondary failover record."
  type        = string
  default     = ""
}

variable "enable_cloudfront" {
  description = "Set to true to put CloudFront in front of the frontend. Requires primary_alb_dns to be set. CloudFront ACM cert is created in us-east-1 automatically."
  type        = bool
  default     = false
}

variable "dr_kms_key_id" {
  description = "CMK ARN in var.secondary_region for cross-region RDS backup replication. AWS-managed keys are region-scoped and cannot replicate cross-region. Leave empty to skip replication (demo default)."
  type        = string
  default     = ""

  validation {
    condition     = var.dr_kms_key_id == "" || can(regex("^arn:aws:kms:[a-z0-9-]+:\\d{12}:key/.+$", var.dr_kms_key_id))
    error_message = "dr_kms_key_id must be empty or a valid KMS key ARN, e.g. arn:aws:kms:us-west-2:123456789012:key/abcd-1234."
  }
}

variable "monitoring_admin_cidr" {
  description = "CIDR blocks allowed to reach Grafana (3000) and Prometheus (9090) on the monitoring EC2. Default allows all — restrict to your IP in production."
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = alltrue([for c in var.monitoring_admin_cidr : can(cidrhost(c, 0))])
    error_message = "monitoring_admin_cidr must be a list of valid CIDR blocks, e.g. [\"203.0.113.0/24\"]."
  }
}

variable "extra_admin_principal_arns" {
  description = "Additional IAM principal ARNs (teammates, CI/CD roles) to grant EKS cluster-admin, beyond whoever is currently running Terraform (which is always included automatically — see main.tf module.eks)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for a in var.extra_admin_principal_arns : can(regex("^arn:aws:iam::\\d{12}:(user|role)/.+$", a))])
    error_message = "extra_admin_principal_arns must be valid IAM user/role ARNs, e.g. arn:aws:iam::123456789012:role/CIRole."
  }
}

variable "alert_email" {
  description = "Email address Alertmanager sends alert notifications to, via SES SMTP. Also used as the SES sender identity -- SES starts every new account in sandbox mode, which requires both sender and recipient to be verified addresses, so using the same address for both means one verification email to click. Move to a separate verified sender + request SES production access if you outgrow sandbox limits (200 msgs/day, 1/sec). No default -- set ALERT_EMAIL in config.env and run `python scripts/configure.py`, same as domain/account/repo. terraform.tfvars is gitignored and fully regenerated by that script, so don't hand-edit it here directly."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "llm_api_key" {
  description = "Real API key (for whichever provider the RCA Lambda's llm_provider is set to) for the RCA Lambda. Optional -- empty string (the default) leaves /bookstore/llm-api-key as an empty shell for manual population via `aws secretsmanager put-secret-value` later. Set LLM_API_KEY in config.env and run `python3 scripts/configure.py` instead of hand-editing terraform.tfvars directly, same convention as domain/alert_email/etc -- terraform.tfvars is gitignored and fully regenerated by that script."
  type        = string
  default     = ""
  sensitive   = true
}

variable "llm_provider" {
  description = "Which LLM API the RCA Lambda calls: \"anthropic\" (default), \"openai\", or \"gemini\" -- passed straight through to module.aiops_rca.llm_provider. Set LLM_PROVIDER in config.env and run scripts/configure.py instead of hand-editing terraform.tfvars; it must match whichever provider llm_api_key's key actually belongs to, or every RCA call fails auth."
  type        = string
  default     = "anthropic"

  validation {
    condition     = contains(["anthropic", "openai", "gemini"], var.llm_provider)
    error_message = "llm_provider must be one of: anthropic, openai, gemini."
  }
}

variable "claude_model" {
  description = "Model ID the RCA Lambda calls, for whichever provider llm_provider selects (the name stayed \"claude_model\" for historical reasons -- it holds any provider's model ID, not just Anthropic's). Set LLM_MODEL in config.env and run scripts/configure.py instead of hand-editing terraform.tfvars."
  type        = string
  default     = "claude-haiku-4-5-20251001"
}

variable "cloudtrail_retention_days" {
  description = "Days CloudTrail logs are locked under S3 Object Lock (COMPLIANCE mode) before the lifecycle rule is allowed to expire them. Only takes effect when enable_cloudtrail_object_lock is true. 400 = 1 year + a 35-day margin, a common SOC 2/ISO 27001 audit-evidence retention baseline. Raise for PCI DSS Req 10.5.1 (1 year online + 3 months immediately available) or a longer regulatory requirement."
  type        = number
  default     = 400
}

variable "enable_cloudtrail_object_lock" {
  description = "Locks the CloudTrail S3 bucket with Object Lock (COMPLIANCE mode) so logged events can't be deleted before cloudtrail_retention_days elapses -- not even by the account root. Off by default: this project's dev-cycle workflow destroys and recreates the whole stack often (see docs/TROUBLESHOOTING.md TF-012's same reasoning), and Object Lock makes that impossible for this one bucket specifically -- confirmed live 2026-09-17 (OBS-074), a locked bucket permanently orphans on `terraform destroy` with no override, by AWS design. Turn this on only for a real audit-scoped deployment where the CloudTrail bucket surviving every future destroy is the point, not a surprise -- and note the bucket name will need to change again (see cloudtrail.tf's own comment) since the account's default CloudTrail bucket name is already permanently claimed by this project's first live-validation orphan."
  type        = bool
  default     = false
}

variable "secrets_recovery_window_days" {
  description = "recovery_window_in_days applied to every aws_secretsmanager_secret in this project. 0 (the long-standing default here) force-deletes on destroy with no soft-delete window -- deliberate, since this stack gets destroyed/recreated often during development (see docs/TROUBLESHOOTING.md TF-012). Set to 7-30 for a real production account so an accidental delete/taint of a live credential is recoverable; this is an explicit, conscious override, not a silent default flip, because most engineers on this project want the current 0 behavior during active development."
  type        = number
  default     = 0

  validation {
    condition     = var.secrets_recovery_window_days == 0 || (var.secrets_recovery_window_days >= 7 && var.secrets_recovery_window_days <= 30)
    error_message = "secrets_recovery_window_days must be 0 (force-delete) or between 7 and 30 (AWS Secrets Manager's supported recovery window range)."
  }
}

variable "enable_rds_secret_rotation" {
  description = "Deploys the AWS Serverless Application Repository single-user MySQL rotation Lambda and wires it into module.rds's rotation_lambda_arn. Off by default so a fresh apply doesn't require SAR CAPABILITY_* IAM acknowledgement from day one; turn on for any account with real (non-demo) data. See docs/compliance/INFORMATION_SECURITY_POLICY.md's cryptography section."
  type        = bool
  default     = false
}

variable "rds_secret_rotation_days" {
  description = "Days between automatic RDS admin credential rotations, once enable_rds_secret_rotation is true."
  type        = number
  default     = 90
}

variable "enable_chaos_node_group" {
  description = "Creates a second, Spot-backed EKS node group (module.eks's aws_eks_node_group.chaos) dedicated to chaos-engineering test runs, kept separate from the primary on-demand node group so fault injection never competes with it for capacity or EC2 quota. Off by default -- turn on before installing a chaos-engineering tool (e.g. Chaos Mesh) that needs somewhere isolated to run test workloads."
  type        = bool
  default     = false
}

variable "chaos_node_max_size" {
  description = "Max nodes in the chaos node group, only used when enable_chaos_node_group is true."
  type        = number
  default     = 2
}

variable "chaos_node_desired_size" {
  description = "Desired nodes in the chaos node group at rest, only used when enable_chaos_node_group is true. Default 0 -- no running instances, no cost, until you bump this for a test window (targeted `terraform apply` or `aws eks update-nodegroup-config`) and drop it back to 0 afterward. Unlike the CloudTrail bucket's create/destroy-per-cycle pattern, this node group doesn't need a full destroy to become ephemeral -- min_size=0 makes 0 nodes a valid steady state on its own."
  type        = number
  default     = 0
}
