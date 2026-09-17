variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
}

variable "oidc_provider_arn" {
  description = "ARN of the EKS cluster OIDC provider — used for IRSA role creation"
  type        = string
}

variable "node_role_name" {
  description = "IAM role name of the EKS node group — receives the EBS CSI driver policy"
  type        = string
}

variable "oidc_provider_url" {
  description = "URL of the EKS cluster OIDC provider, WITH the https:// scheme — stripped via replace() at point of use in IRSA trust policy conditions"
  type        = string
}

variable "aws_region" {
  description = "AWS region — used to scope the external-secrets IRSA policy to this account/region"
  type        = string
}

variable "secrets_recovery_window_days" {
  description = "recovery_window_in_days for this module's Secrets Manager secrets (grafana_admin, monitoring_basic_auth). 0 = force delete (this project's dev-cycle default, see TF-012); 7-30 for a real production account."
  type        = number
  default     = 0
}

