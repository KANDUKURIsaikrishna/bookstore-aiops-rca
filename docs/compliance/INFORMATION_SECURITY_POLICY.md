# Information Security Policy

Document owner: [Role: to be assigned — e.g. Head of Engineering / CISO]
Effective date: [Date: to be confirmed at adoption]
Review cadence: annually, or on any material change to the architecture described below.

## 1. Purpose and Scope

This is the umbrella information security policy for the Bookstore platform (the "system"): the AWS/EKS microservices application described in [`../ARCHITECTURE.md`](../ARCHITECTURE.md), its Terraform-managed infrastructure, its CI/CD pipeline, and the AIOps root-cause-analysis (RCA) pipeline that sends log excerpts to the Anthropic Claude API. It exists to satisfy the umbrella-policy requirement of SOC 2 (Common Criteria, particularly CC-series logical/physical access and change management controls), ISO/IEC 27001:2022 Annex A.5.1 (policies for information security), and PCI DSS v4.0 Requirement 12 (organizational information security policy), and to state, in one place, what is actually true of this system's security posture today versus what remains an open commitment.

This policy is authoritative over, and should be read alongside, the following documents:

- [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) — what happens when a control here fails
- [`BUSINESS_CONTINUITY_DR_PLAN.md`](BUSINESS_CONTINUITY_DR_PLAN.md) — availability and recovery commitments
- [`CHANGE_MANAGEMENT_SOP.md`](CHANGE_MANAGEMENT_SOP.md) — how changes to this system are authorized and tracked
- [`VENDOR_RISK_MANAGEMENT_POLICY.md`](VENDOR_RISK_MANAGEMENT_POLICY.md) — third parties this policy's access-control and cryptography sections extend to
- [`DATA_CLASSIFICATION_RETENTION_POLICY.md`](DATA_CLASSIFICATION_RETENTION_POLICY.md) — what data exists and how long it is kept
- [`../SECURITY.md`](../SECURITY.md) — the public-facing vulnerability-reporting process
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the technical source of truth every control below cites

Where this policy states a control exists, it cites the specific file and line/resource that implements it. Where a control is described as partial or not yet in place, that is stated plainly — this document is written for an auditor and must not overstate the current state of the system.

## 2. Acceptable Use

1. Engineers with access to this repository, its AWS account(s), or its production credentials must use that access only for legitimate development, operational, or incident-response purposes related to the Bookstore platform.
2. No credential, password, private key, or account ID may be stored in plain text in any committed file. This is enforced mechanically: Gitleaks scans every commit and the full git history on every push (`.github/workflows/ci-cd.yml`, `secret-scan` job).
3. Local development credentials (`.env` files per service) are never committed — see each service's `.gitignore` — and are scoped to a developer's own local MySQL instance, not shared production credentials.
4. Production access (AWS console/API, `kubectl` against the live EKS cluster, Grafana/Prometheus/Alertmanager on the monitoring EC2) is limited to personnel who need it for their role. See Section 3 for how that access is currently granted and the open gap in enforcing it.
5. AI-assisted tooling used against this codebase (including the RCA pipeline's own use of the Claude API, see Section 4.4) must not be pointed at data outside this system's own classification without a data-handling review — see [`DATA_CLASSIFICATION_RETENTION_POLICY.md`](DATA_CLASSIFICATION_RETENTION_POLICY.md).

## 3. Access Control Policy

### 3.1 Source code and infrastructure-as-code access

Every path in this repository requires review from a designated code owner before merge, enforced by `.github/CODEOWNERS`. Terraform (`*.tf`, `modules/`), CI/CD workflows (`.github/`), Kubernetes manifests (`k8s/`), and IAM definitions (`iam.tf`) each carry an explicit CODEOWNERS callout in addition to the repository-wide default rule, reflecting their higher blast radius. CODEOWNERS enforcement itself depends on the corresponding GitHub branch-protection rule ("require review from Code Owners") being turned on for the target branch — this is a GitHub repository *setting*, not something expressed in code, and is called out as an open item in Section 3.3 below.

### 3.2 CI/CD and machine-identity access

The CI/CD pipeline authenticates to AWS exclusively via GitHub OIDC federation — no static AWS access keys exist anywhere in this repository or its GitHub Secrets (`.github/workflows/ci-cd.yml`'s `configure-aws-credentials` step assumes `AWS_ROLE_ARN` via `id-token: write`; see [`../README.md`](../README.md#secret-management)). Deploys to production additionally require a human reviewer to approve the GitHub Environment gate named `production` before the `deploy` job runs (`.github/workflows/ci-cd.yml`, `deploy:` job, `environment: production`) — see [`CHANGE_MANAGEMENT_SOP.md`](CHANGE_MANAGEMENT_SOP.md) for the full change-approval flow this gate is part of.

### 3.3 Open organizational requirement: MFA on human access

Multi-factor authentication for human IAM/console access to the AWS account is **not currently enforced in this codebase**, and cannot be, by design: GitHub's CI OIDC role is a machine identity, and AWS STS has no mechanism for a federated OIDC role assumption to carry an MFA claim — adding an MFA condition to that role's trust policy would simply break every CI run, not add security. MFA enforcement for human access is therefore an organizational requirement that has to be set at the identity-provider layer (AWS IAM Identity Center, or equivalent), not something this Terraform can express.

**Status:** open. [Action: adopt an org-wide MFA requirement for all human AWS console/CLI access via IAM Identity Center or equivalent, and record the enforcement mechanism and verification date here once done.]

Similarly, GitHub branch-protection rules (required PR reviews, required status checks before merge) live in GitHub's repository *settings*, not in this repository's content, so they cannot be verified or asserted by reading the codebase. They must be captured as a dated screenshot or `gh api` export each audit cycle, or configured via `gh api` with the repository owner's explicit sign-off.

**Status:** open. [Action: capture branch-protection settings as an evidence artifact each audit cycle; owner: repository administrator.]

### 3.4 In-cluster secret access

Kubernetes workloads never hold static AWS credentials. The External Secrets Operator (ESO) uses IRSA (IAM Roles for Service Accounts) to read AWS Secrets Manager and materialize native Kubernetes `Secret` objects per namespace (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md#secrets-flow-and-the-bug-that-used-to-break-it)). The `ClusterSecretStore` and its backing IRSA role are cluster-wide, scoped in IAM to the `/bookstore/*` secret-name prefix rather than to individual services. This is a documented, deliberate simplification, not an oversight: true per-service secret isolation would require a per-namespace `SecretStore` per service instead of one shared `ClusterSecretStore`, which this project's current stage does not yet justify. It is accepted as a known risk rather than remediated in this pass — see [`../ARCHITECTURE.md`](../ARCHITECTURE.md#secrets-flow-and-the-bug-that-used-to-break-it) for the full reasoning, and Section 3 of [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) for how this shared blast radius affects containment during an incident.

### 3.5 Network segmentation

Kubernetes `NetworkPolicy` restricts pod-to-pod traffic between namespaces — for example, `catalog-service`/`user-service`/`order-service` accept ingress only from the `gateway` namespace, not from the whole cluster (`docs/ARCHITECTURE.md`'s "microservices platform" section, commit `153bed2`). RDS's security group is scoped to the EKS node CIDRs specifically, not the full VPC CIDR (see `terraform/modules/security/main.tf`, fixed under TROUBLESHOOTING OBS-049).

## 4. Cryptography Policy

### 4.1 Encryption at rest

- **RDS (MySQL 8.0, Multi-AZ):** `storage_encrypted = true` in `terraform/modules/rds/main.tf`, using an AWS-managed KMS key by default (`kms_key_arn` is left null unless a customer-managed key is supplied).
- **EKS Kubernetes Secrets:** a dedicated customer-managed KMS key (`aws_kms_key.eks_secrets`, `terraform/modules/eks/main.tf`) provides envelope encryption on the EKS control plane's Secrets API, on top of the underlying etcd-volume encryption — this protects against a compromised etcd snapshot in a way volume encryption alone does not.
- **RCA dashboard S3 bucket:** explicit SSE-S3 encryption and versioning (`terraform/modules/aiops-rca/dashboard_site.tf`), rather than relying on an AWS account-level default.
- **CloudTrail S3 bucket:** SSE-KMS with bucket-key enabled (`terraform/cloudtrail.tf`).

### 4.2 Encryption in transit

TLS is enforced for all customer-facing traffic via cert-manager + Let's Encrypt / ACM, with forced HTTP → HTTPS redirection (see [`../README.md`](../README.md#security-controls)). Prometheus and Alertmanager on the monitoring EC2 sit behind bcrypt-hashed HTTP basic auth (`terraform/modules/eks-addons/monitoring-basic-auth-secret.tf`) rather than being reachable without a credential at all (fixed under TROUBLESHOOTING OBS-063).

### 4.3 Audit trail immutability

CloudTrail (`terraform/cloudtrail.tf`) is a re-added control: a multi-region trail with log file validation enabled, writing to an S3 bucket. This has been applied and validated against a real AWS account (2026-09-17, full 181-resource stack, see `docs/TROUBLESHOOTING.md` OBS-074) — it is operational, not just coded.

Object Lock in **COMPLIANCE mode** (`aws_s3_bucket_object_lock_configuration`, `mode = "COMPLIANCE"`, `var.cloudtrail_retention_days` default 400 days) — the property that makes an audit trail actually immutable, since not even the AWS account root can delete or overwrite a locked log object before retention elapses — is **available but off by default** (`var.enable_cloudtrail_object_lock`). It was on during the 2026-09-17 validation run and confirmed to work exactly as intended: it also confirmed, the hard way, that a locked CloudTrail bucket makes `terraform destroy` permanently unable to remove that one resource, which conflicts directly with this project's own destroy-and-recreate development workflow (the same tension `docs/TROUBLESHOOTING.md` TF-012 already documents for Secrets Manager's recovery window). Given this project's current phase — a demo/reference stack, not yet a system with real customer data or a live compliance obligation — the default was changed to off so `terraform destroy` leaves nothing behind. **This is an open, honestly-stated compliance gap, not a silent regression**: without Object Lock, the CloudTrail bucket's contents can be deleted (by a sufficiently privileged principal), so the "not even the account root" immutability property does not currently hold. Before this system is ever treated as audit-ready or handles real customer/regulated data, `enable_cloudtrail_object_lock` must be turned on — and note that doing so requires a bucket rename of its own, since the account's default CloudTrail bucket name is already permanently orphaned from the validation run (see OBS-074).

### 4.4 Third-party cryptographic exposure: the LLM API

The RCA Lambda (`lambdas/rca-lambda/lambda_function.py`) sends raw Loki log excerpts, gathered from a ±5-minute window around a firing alert across all five microservices, to the configured LLM API over TLS for root-cause narrative generation — the Anthropic Claude API by default; `var.llm_provider` also supports OpenAI and Gemini, in which case this section applies to whichever of those is actually selected. Capped at `var.max_log_lines_per_service` (12) lines per service and `var.max_log_line_chars` (400) characters per line — a token-cost control, not a security control, but it also bounds the volume of potentially sensitive log content disclosed per call. This is the single most sensitive data flow in the newer AIOps pipeline — see [`DATA_CLASSIFICATION_RETENTION_POLICY.md`](DATA_CLASSIFICATION_RETENTION_POLICY.md) and [`VENDOR_RISK_MANAGEMENT_POLICY.md`](VENDOR_RISK_MANAGEMENT_POLICY.md) for the full treatment. The API key itself is stored in AWS Secrets Manager (`/bookstore/llm-api-key`); it is not derivable from anything Terraform has access to, so it always originates from a human, but Terraform can now populate it automatically: setting `LLM_API_KEY` in `config.env` and re-running `scripts/configure.py` + `terraform apply` writes the real value via `var.llm_api_key` (`sensitive = true`, never printed, never committed — `config.env`/`terraform.tfvars` are both gitignored). The key does land in Terraform state via this path, in the same encrypted S3 backend every other secret in this project's state already sits in. Leaving `LLM_API_KEY` unset in `config.env` falls back to the fully manual path (`aws secretsmanager put-secret-value`) — see [`../DEPLOYMENT.md`](../DEPLOYMENT.md#populate-the-llm-api-key-rca-pipeline) for both.

### 4.5 Credential rotation

- **Secrets Manager recovery window.** Every `aws_secretsmanager_secret` in this project sets `recovery_window_in_days` from `var.secrets_recovery_window_days`, which defaults to **0** (immediate, unrecoverable deletion on destroy, not a 30-day soft-delete window). This is a deliberate development-cycle tradeoff, not an oversight: this stack is destroyed and recreated frequently during development, and AWS Secrets Manager's default 30-day recovery window blocks a subsequent `terraform apply` from recreating a secret under the same name (see [`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md#tf-012--secrets-managers-default-recovery-window-blocks-destroyrecreate-cycles), TF-012). **For a real production account this variable must be overridden to a value between 7 and 30** — the code enforces this range via a validation block, so `0` and any value outside `[7, 30]` are the only two states possible, but choosing production's actual value is an organizational decision, not a code default. [Decision: set `secrets_recovery_window_days` to a specific value in `[7, 30]` for the production `terraform.tfvars`, and record that value and the date it took effect here.]
- **RDS admin credential rotation.** `module.rds` has always supported a `rotation_lambda_arn`/`rotation_days` pair, but until this session nothing ever deployed a rotation Lambda to populate it, so `/bookstore/db-credentials` has never rotated on a schedule. `terraform/rds-secret-rotation.tf` (new this session) deploys AWS's own officially published single-user MySQL rotation Lambda from the Serverless Application Repository, gated behind `var.enable_rds_secret_rotation` (**default `false`**) with a 90-day rotation period (`var.rds_secret_rotation_days`) once turned on. This code was written and `terraform validate`'d without a live AWS account in this session — its parameter schema (`functionName`, `endpoint`, `vpcSecurityGroupIds`, `vpcSubnetIds`) should be confirmed against a live `aws serverlessrepo get-application` lookup before first enabling it in a real account (see the comment block at the top of `terraform/rds-secret-rotation.tf`). **Status: coded, off by default, not yet applied or tested against live infrastructure.** [Decision: turn `enable_rds_secret_rotation` on for any account holding real, non-demo data, and record the date it was verified working.]

### 4.6 Honest current-state summary

**Updated 2026-09-17.** A full `terraform apply` of the complete stack (181 resources — network, EKS, RDS, monitoring EC2, the aiops-rca pipeline, and CloudTrail) was run against a real AWS account and verified: ArgoCD sync, the ExternalSecrets IRSA fix, the RCA dashboard/API/DynamoDB table, and CloudTrail itself all confirmed operational, not just coded. A full `terraform destroy` was also run and confirmed clean, with one real, now-documented exception: the CloudTrail bucket cannot be destroyed while Object Lock is enabled (see Section 4.4 and `docs/TROUBLESHOOTING.md` OBS-074) — as a direct result, `enable_cloudtrail_object_lock` now defaults to off, and Object Lock is a known, explicit gap rather than an assumed-on control. RDS secret rotation (`var.enable_rds_secret_rotation`) remains untested against a live account — it was written and `terraform validate`'d without a live account available at the time, and the note in `terraform/rds-secret-rotation.tf` to verify the SAR app's parameter schema before first use still stands. [Action: exercise `enable_rds_secret_rotation=true` against a real account and update this section accordingly.]

## 5. PCI DSS Scoping Conclusion

This system has **no Cardholder Data Environment (CDE)**. `order-service`'s schema (see [`../UML.md`](../UML.md), `ORDERS` entity) stores `user_id`, `book_id`, `quantity`, `status`, and `created_at` — it does not store price, and no payment-card data of any kind (PAN, expiry, CVV, cardholder name tied to a card) exists in any table, log stream, or downstream data store in this codebase. No payment gateway integration exists anywhere in the codebase; checkout (`POST /orders/checkout`) creates an order record with no card-capture step. Consequently:

- PCI DSS controls that apply specifically to a CDE (network segmentation validation, cardholder-data encryption, SAQ/ROC scope reduction) do not apply to this system, because there is no cardholder data to scope them around.
- The organizational and access-control requirements of PCI Req 12 that this document otherwise satisfies (this policy itself, access control, change management, incident response) are addressed on their own merits above, independent of the CDE question.

This scoping conclusion should be restated, not re-derived, in every other document in this set that touches PCI applicability — see [`DATA_CLASSIFICATION_RETENTION_POLICY.md`](DATA_CLASSIFICATION_RETENTION_POLICY.md) Section 2 for the same conclusion applied to data classification.

## 6. Policy Exceptions and Enforcement

Any deviation from this policy (for example, an emergency change bypassing the standard CODEOWNERS review — see [`CHANGE_MANAGEMENT_SOP.md`](CHANGE_MANAGEMENT_SOP.md) Section 3) must be documented after the fact with a justification and a named approver. Repeated or unjustified deviations are a matter for [Role: to be assigned] to escalate.

## 7. Revision History

| Date | Change | Author |
|---|---|---|
| [Date: to be confirmed at adoption] | Initial version, drafted as part of the SOC 2 / ISO 27001 / PCI DSS gap-remediation pass on branch `compliance/soc2-iso27001-hardening`. | [Author: to be assigned] |
