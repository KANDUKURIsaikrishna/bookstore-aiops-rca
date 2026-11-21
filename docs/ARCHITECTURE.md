# Architecture

Current state of `main`. This describes what the code actually builds, not an aspirational design.

## What this is

A bookstore web app built as a learning/reference implementation of a production-grade AWS three-tier architecture, now fully cut over to a microservices split. The old monolith is gone.

1. The **frontend** (`client/`, React) is what users actually load — deployed as its own `frontend`/`frontend-service` in the `bookstore` namespace, serving the React static build via nginx.
2. Every API call that frontend makes goes to the **microservices platform** — `catalog-service`, `user-service`, `order-service`, `notification-service`, all behind `api-gateway`. The old monolith's `backend/` (Node/Express API, the original `bookstore-backend` Rollout) was deleted outright once it was confirmed to have zero ingress routes and zero references anywhere in the live frontend bundle. `k8s/base/ingress/ingress.yaml` routes `bookstore.<domain>` → `frontend-service`; `k8s/services/api-gateway/base/ingress.yaml` exclusively owns `api.bookstore.<domain>`.

## Top-level system diagram (current, real traffic split)

```
                                   Internet
                                      |
                         Route53 (public zone, failover routing)
                                      |
                    ┌─────────────────────────────────┐
                    │   CloudFront (optional, off by   │
                    │      default) or direct to ALB   │
                    └─────────────────────────────────┘
                                      |
                        ALB (AWS Load Balancer Controller)
                          ┌───────────┴───────────┐
                 host: bookstore.<domain>   host: api.bookstore.<domain>
                          |                           |
                  frontend Service              api-gateway Service
              (React static, nginx)      (Node/Express, JWT verification)
                                                       |
                          ┌────────────┬──────────────┼──────────────┐
                          |            |              |              |
                  catalog-service  user-service  order-service  notification-service
                          |            |              |              |
                          └────────────┴──────┬───────┴──────────────┘
                                               |
                                        RDS MySQL 8.0
                                        (Multi-AZ, us-west-1, per-service schemas)
```

Everything above lives in one EKS cluster (`bookstore-eks`, us-west-1), split across the `bookstore` namespace (frontend only, now that backend is deleted) and 5 microservice namespaces (`catalog`, `user`, `order`, `notification`, `gateway`), deployed via ArgoCD from `k8s/overlays/prod` and `k8s/services/*/overlays/prod` respectively.

## Region layout

**Primary: us-west-1** — all live workloads (EKS, RDS, monitoring EC2, all traffic).
**Secondary: us-west-2** — DR only. ECR image replication + RDS automated-backup replication (needs an explicit KMS key, off by default) + a Route53 failover record. **No EKS cluster in us-west-2.** If us-west-1 goes down, there's no compute to fail over to yet — DR today is backup-level, not active-active. See [`dr.tf`](../terraform/dr.tf).

## Terraform module graph

`network → security → rds → route53 → ecr → eks → monitoring-ec2 → eks-addons` is the module *call* order in `main.tf`, but that's not the real dependency graph — Terraform parallelizes anything not actually connected by a resource/output reference, regardless of where it's written in the file. (No `acm` module appears here because none exists — the wildcard ACM cert is created directly by root-level `ingress-cert.tf`, not a module.) The real shape:

```
network ──┬─→ security ──┬─→ rds ──→ route53 ──→ ingress-cert.tf
          │              └─→ eks ──┬─→ eks-addons ─────┐
          │                        └─→ monitoring-ec2 ←┘ (needs eks + the
          │                              ↑                eks-addons Grafana
          └─→ aiops-rca ─────────────────┘                secret only, not
ecr  (independent)                                          any Helm install)
iam.tf (independent)
```

`ecr` and the root `iam.tf` resources have no dependency on `network` at all and run fully in parallel with it. `rds` and `eks` both depend only on `network`+`security`, not on each other, so they provision concurrently — this is why a full stand-up takes roughly `max(RDS time, EKS time)` for that stage, not the sum. `ingress-cert.tf`'s ACM cert only needs `route53`'s hosted zone to exist (for DNS validation records), not the zone's ALB-pointing alias records specifically, so it doesn't get stuck behind the `eks-addons`/ALB-discovery chain those alias records do wait on. `monitoring-ec2` used to have a blanket `depends_on = [module.eks_addons]` forcing it to wait for every Helm chart in `eks-addons` (up to 900s for ArgoCD) even though it only needs the fast Grafana secret — that's been removed.

`aiops-rca` only ever depends on `network` (`vpc_id`/`lambda_subnet_ids` for the RCA Lambda's VPC config) — it does not appear in the `security`/`rds`/`eks` chain at all, and it never references anything from `monitoring-ec2`. The arrow into `monitoring-ec2` in the diagram above is not a Terraform module dependency in the usual sense: `monitoring-ec2`'s own module call takes two of `aiops-rca`'s outputs (`lambda_security_group_id`, `webhook_invoke_url`) as plain input variables and does its own wiring with them (an SG ingress rule on port 3100, the webhook URL templated into Alertmanager's config). The relationship is strictly one-directional — `aiops-rca` produces, `monitoring-ec2` consumes, never the reverse — which is what keeps this from becoming a real cycle, since `monitoring-ec2` already depends on `eks`/`eks-addons` and a cycle back through `aiops-rca` would have no valid apply order. See [AIOps RCA pipeline](#aiops-rca-pipeline) below for the full picture, including the one piece of config that genuinely needs both modules' outputs at once and why it lives outside both of them.

| Module | Creates | Depends on |
|---|---|---|
| `network` | VPC `170.20.0.0/16`, 2 public + 6 private subnets, IGW, NAT gateway per AZ (one private route table each; `single_nat_gateway = true` collapses to one shared NAT), S3 Gateway VPC Endpoint (free — keeps ECR/S3 traffic off the NAT) | — |
| `security` | Security groups: ALB (80/443 from internet), RDS (3306 from VPC CIDR) | `network` |
| `rds` | MySQL 8.0 `db.t3.micro`, Multi-AZ, gp3 storage, Secrets Manager admin credentials, optional cross-region backup replication | `network`, `security` |
| `route53` | Private zone (RDS internal DNS) + public zone with active-passive failover records | `network`, `rds`, `eks` (needs ALB DNS) |
| `ecr` | ECR repos for `frontend`, plus any `extra_repos` (currently `catalog-service`, `user-service`, `order-service`, `notification-service`, `api-gateway`), 10-image lifecycle policy, optional cross-region replication — `backend` repo deleted along with the old monolith | — |
| `eks` | EKS 1.31 cluster, managed node group (`t3.medium`, min 1 / max 3 / desired 3), OIDC provider (enables IRSA), node launch template running node-exporter + Fluent Bit as systemd services | `network`, `security` |
| `eks-addons` | Helm-installed cluster add-ons: External Secrets Operator, AWS Load Balancer Controller (provisions the ALB), ArgoCD, Argo Rollouts; plus the VPC CNI (NetworkPolicy enforcement), EBS CSI, and metrics-server EKS addons | `eks` |
| `monitoring-ec2` | Standalone EC2 (`t3.small`) running Prometheus + Grafana + Loki + Alertmanager + kube-state-metrics via Docker Compose | `network`, `eks-addons` |
| `aiops-rca` | DynamoDB table (`bookstore-rca-reports`, PK `alert_id` / SK `report_timestamp`, GSI `by_created_at`), SQS DLQ (14-day retention), Secrets Manager shell (`/bookstore/llm-api-key`, populated manually), VPC-attached RCA Lambda + IP-restricted webhook API Gateway (REST), non-VPC dashboard-read Lambda + open HTTP API Gateway (CORS `*`), S3+CloudFront static dashboard (OAC-restricted bucket policy) | `network` only — and, one-directionally, feeds two outputs INTO `monitoring-ec2` without ever depending back on it |

No `acm` module — the wildcard ACM cert is created directly by root-level `ingress-cert.tf`, not a module. Root-level `.tf` files add cross-cutting resources not owned by any module: `iam.tf` (GitHub OIDC role for CI), `ingress-cert.tf` (wildcard ACM cert for the ingress domain), `cloudfront.tf` (optional CDN, ACM cert in us-east-1), `dr.tf` (cross-region backup replication).

No CloudWatch, GuardDuty, VPC Flow Logs, EKS control-plane log export, or RDS Enhanced Monitoring anywhere in this stack — removed 2026-08-23 (all shipped to CloudWatch Logs and nowhere else; GuardDuty findings had no alerting wired to them and nothing ever read CloudTrail *at the time*). Prometheus/Grafana/Loki/Alertmanager on `monitoring-ec2` cover metrics, logs, and alerting.

**CloudTrail was re-added** (`terraform/cloudtrail.tf`, compliance-hardening pass) — this is a genuine exception to the paragraph above, not a contradiction of its reasoning. The 2026-08-23 removal was correct for a stack with no external compliance obligation: nothing here needed a record of *who called which AWS API*, only what Prometheus/Loki already cover (app metrics, app logs, alerting). A SOC 2/ISO 27001/PCI DSS gap analysis is the reason it came back — none of those frameworks' audit-trail requirements (SOC 2 CC7.2, ISO A.8.15, PCI Req 10) can be satisfied by Loki or Prometheus, since neither records AWS account-level activity (IAM changes, console access, API calls) at all. The re-added trail is multi-region, log-file-validation on, and writes to an S3 bucket locked with Object Lock in COMPLIANCE mode (`var.cloudtrail_retention_days`, default 400 days) — not even the account root can delete a log entry early. See [`docs/compliance/INFORMATION_SECURITY_POLICY.md`](compliance/INFORMATION_SECURITY_POLICY.md) for the full rationale and [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) OBS-072/OBS-073 for the two bugs fixed alongside this pass. GuardDuty and VPC Flow Logs stay removed — CloudTrail specifically closes the "no audit trail" gap none of the frameworks above will pass without; the other two were pure cost with no framework citing them as a hard requirement here.

A destroy-time-only `null_resource.cleanup_eks_networking` (root `main.tf`) sits between `network` and `eks` in the destroy graph — `module.eks` `depends_on` it, so on `terraform destroy` it runs after the cluster is gone but before `network`'s VPC/subnets, cleaning up orphaned VPC CNI ENIs and the EKS-auto-created cluster security group (both created directly via the EC2 API, outside Terraform's own resource graph, and both able to block the VPC destroy with `DependencyViolation` if left behind).

## Why monitoring runs on EC2, not in the cluster

The original design put `kube-prometheus-stack` in EKS. On a single `t3.medium` node it starved every other pod pulling images and never became `Ready` within any reasonable Helm timeout. The fix: move Prometheus, Grafana, Loki, and Alertmanager to a dedicated EC2 instance running Docker Compose. The EKS cluster itself runs **zero monitoring pods** — `node-exporter` and `Fluent Bit` run as systemd services baked into the node launch template instead of DaemonSets, and `kube-state-metrics` runs as a Docker container on the monitoring EC2, reading the cluster over the network via a read-only EKS access entry.

`k8s/base/monitoring/` once held `ServiceMonitor`/`PrometheusRule` CRD manifests; they were removed (2026-08-29). Nothing installs the Prometheus Operator that would consume them, and the EC2 Prometheus scrapes via static configs and `file_sd_configs` (a cron script rewriting target files), not via `ServiceMonitor` discovery. Re-adding them would also break ArgoCD sync outright — an unknown CRD type fails the whole sync batch (TROUBLESHOOTING.md OBS-012).

## AIOps RCA pipeline

Phase 2 of the AIOps work on this branch — Phase 1 was structured JSON logging (`winston`) across all 5 microservices plus bounding Loki's retention to 14 days, both landed earlier on the same branch. `terraform/modules/aiops-rca/` provisions an automated root-cause-analysis pipeline that runs on every Alertmanager alert, end to end, with no human in the loop until the report lands in an inbox:

```
Alertmanager (monitoring EC2)
        | POST <webhook_invoke_url>/webhook, on every firing alert
        v
API Gateway REST API — bookstore-rca-webhook
  (IP-restricted to the monitoring EC2's own Elastic IP; nothing else ever calls this)
        v
RCA Lambda — bookstore-rca-lambda (VPC-attached)
  1. query Loki, ±5 min around the alert, all 5 services
  2. call the LLM API (Anthropic Claude by default) for a root-cause narrative
  3. write the report → DynamoDB (bookstore-rca-reports)
  4. email the narrative → SES
        |
        | (failed invocation, after Lambda's own retries)
        v
  SQS DLQ — bookstore-rca-lambda-dlq (14-day retention)

DynamoDB (bookstore-rca-reports)
        ^ Query (GSI by_created_at) / GetItem
        |
dashboard-read Lambda — bookstore-dashboard-read-lambda (not VPC-attached)
        ^ GET /reports, GET /reports/{alert_id}/{report_timestamp}
        |
HTTP API Gateway v2 (open, CORS *)
        ^
        |
S3 + CloudFront static dashboard (OAC-restricted bucket policy)
```

Alertmanager's `default-webhook`/`critical-webhook` receivers (`terraform/modules/monitoring-ec2/user-data.sh.tftpl`) POST the firing alert to the webhook instead of the old `http://localhost:5001/` placeholder. The webhook's REST API carries no resource policy of its own inside the `aiops-rca` module — the IP restriction (source = the monitoring EC2's own public IP, since Alertmanager is the only expected caller) is a separate root-level resource, `aws_api_gateway_rest_api_policy.rca_webhook` in `terraform/main.tf`, precisely because that policy needs `monitoring_ec2`'s public IP output and the module never references anything from `monitoring-ec2` (see below).

The RCA Lambda is VPC-attached so it can reach Loki on the monitoring EC2's port 3100 over its *private* IP — it discovers that IP at invocation time via `ec2:DescribeInstances` (filtered on the monitoring instance's `Name` tag), the same runtime-discovery pattern the EKS node launch template's Fluent Bit already uses to find Loki, rather than a static/templated address that would need the instance's public IP and wouldn't route back cleanly from a private subnet anyway. It queries all 5 microservices' logs, plus the alert's own `pod` label when present (`_log_search_targets` — pod-level alerts like `PodCrashLooping`/`HighPodCPUUsage` never carry a `service` label and can fire on any pod in the cluster, not just a bookstore one; see `docs/TROUBLESHOOTING.md` OBS-080), for a ±5 minute window around the alert's `startsAt` timestamp, builds a prompt from the raw log excerpts, and calls the configured LLM API (Anthropic Claude by default; `var.llm_provider` also supports OpenAI and Gemini, config.env-driven end to end — see `docs/DEPLOYMENT.md`) — the key is read from Secrets Manager at `/bookstore/llm-api-key`. Terraform creates the real secret value too, not just an empty shell, whenever `LLM_API_KEY` is set in `config.env` (`aws_secretsmanager_secret_version.claude_api_key`, count-gated on it being non-empty); leaving it blank is the only case where a human has to populate the secret by hand afterward. `call_llm` retries transient failures (timeouts, generic 5xx) up to 3x in-invocation, but fails immediately on any 4xx or on 503 — those mean "this exact request will fail the same way every time" or "back off," not "try again right now" (see `docs/TROUBLESHOOTING.md` OBS-079).

The report is written to a single DynamoDB table (`bookstore-rca-reports`) using a single-table design: hash key `alert_id`, range key `report_timestamp`, so one `PutItem` never overwrites another. The handler checks first, though (`has_ok_report`, a `Query` on `alert_id`): once an alert has one successful report, every later duplicate notification for the same still-firing alert — Alertmanager retries on failure and keeps notifying per `repeat_interval` — is skipped outright (`status: "skipped_duplicate"`, no Loki query, no LLM call, no email), not re-analyzed into a second report. See `docs/TROUBLESHOOTING.md` OBS-079 for why that matters on a rate-limited LLM key. A GSI (`by_created_at`, hash `gsi_pk`, range `created_at`) lets the dashboard `Query` "most recent reports first" instead of an unbounded `Scan` — every item shares the same literal `gsi_pk = "REPORT"`, since a real per-item partition key isn't needed for a table this small and single-purpose. The narrative also goes out by email via SES, reusing the same verified `alert_email` identity Alertmanager's own SMTP already sends through — SES sandbox mode requires both sender and recipient verified, so one already-verified address covers both ends. Failed invocations, after Lambda's own internal retries are exhausted, land in an SQS DLQ (14-day retention, SQS's max) instead of vanishing silently.

A second, unrelated Lambda (`bookstore-dashboard-read-lambda`) serves those reports to a static dashboard. It is deliberately *not* VPC-attached — it only ever talks to DynamoDB over the public AWS API endpoint, so there's no reason to pay a VPC-attached Lambda's ENI cold-start cost for a read-only listing. It sits behind an open HTTP API Gateway v2 (`cors_configuration.allow_origins = ["*"]`, no auth — a read-only, non-sensitive reports listing) serving `GET /reports` (list, most-recent-first) and `GET /reports/{alert_id}/{report_timestamp}` (detail). The dashboard itself (`dashboard/index.html`/`script.js`/`style.css`) is static content in S3 behind CloudFront, using an Origin Access Control so the bucket policy trusts only the CloudFront distribution's own service principal (scoped further with an `AWS:SourceArn` condition) — the bucket has all 4 public-access-block flags on and is never reachable directly.

**Circular-dependency avoidance.** `aiops-rca` takes only `network`'s `vpc_id`/`lambda_subnet_ids` as module-shaped input — nothing from `monitoring-ec2`, ever. But the RCA Lambda needs to reach Loki (which only exists once `monitoring-ec2` is up), and Alertmanager needs the webhook's URL (which only exists once `aiops-rca` is up): a genuine two-way need that, expressed as a direct module-to-module reference in either direction, would be a real circular dependency. The fix follows the same shape as the EKS-node/Fluent-Bit-discovers-Loki's-IP-at-runtime workaround used elsewhere in this stack: push anything that needs both sides up to root, and let the dependency itself run only one way. Concretely, `monitoring_ec2`'s module call in `terraform/main.tf` takes `rca_lambda_sg_id = module.aiops_rca.lambda_security_group_id` and `rca_webhook_url = module.aiops_rca.webhook_invoke_url` as ordinary input variables, then does its own wiring on the other side — an `aws_security_group_rule` opening port 3100 to that security group, and `rca_webhook_url` templated straight into `user-data.sh.tftpl`'s Alertmanager config. The one piece of config that genuinely needs both modules' outputs at once — the webhook's IP-restriction policy, which needs `monitoring_ec2.instance_public_ip` — lives as the standalone root-level `aws_api_gateway_rest_api_policy.rca_webhook` resource instead of inside either module, since root `main.tf` is the one place in the graph allowed to reference both without either module depending on the other.

The `archive` provider (`hashicorp/archive ~> 2.4`, added to `terraform/versions.tf`) zips both Lambdas' source directly from `lambdas/rca-lambda/` and `lambdas/dashboard-read-lambda/` via `data "archive_file"` at plan/apply time — no separate build/package step, unlike the microservices' own Docker-build-then-ECR-push CI pipeline.

## The database

Single RDS MySQL 8.0 instance, `db.t3.micro`, Multi-AZ, in two private subnets dedicated to RDS (subnet indices 4-5 of the 6 private subnets — see [Subnet layout](#subnet-layout)). Admin credentials live in Secrets Manager at `/bookstore/db-credentials`, synced into the cluster as a K8s Secret via External Secrets Operator.

Credential rotation exists (`terraform/rds-secret-rotation.tf`, AWS's own official single-user MySQL rotation Lambda deployed via the Serverless Application Repository) but is **off by default** behind `var.enable_rds_secret_rotation` — turning it on deploys the rotation Lambda into the same VPC as RDS and wires `module.rds`'s `rotation_lambda_arn`/`rotation_days` automatically. `recovery_window_in_days` on every Secrets Manager secret in this project (not just this one) is similarly a variable now (`var.secrets_recovery_window_days`, default 0 = force-delete, matching this project's frequent destroy/recreate dev cycle — see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) TF-012) rather than hardcoded, so a real production account can opt into a 7-30 day soft-delete window as a conscious choice.

The dead `k8s/base/database/` files (`mysql-statefulset.yaml`, `mysql-service.yaml`, `mysql-init-configmap.yaml`) from an earlier in-cluster-MySQL design have been deleted (2026-08-14) — they were never referenced by `k8s/base/kustomization.yaml`. RDS is, and has always been in the live deployment, the real, only database.

## Secrets flow (and the bug that used to break it)

```
AWS Secrets Manager (/bookstore/*)
        |
        | IRSA (IAM Role for Service Account)
        v
External Secrets Operator (external-secrets-sa, namespace external-secrets)
        |
        | ClusterSecretStore "aws-secretsmanager" (cluster-scoped, shared)
        v
ExternalSecret (per namespace, e.g. db-secret, catalog-db-secret)
        |
        v
K8s Secret → mounted into pod env vars
```

This used to be broken: the External Secrets Operator's Helm release created a ServiceAccount with no IRSA role and no annotation, even though the `ClusterSecretStore` already expected one named exactly `external-secrets-sa`. No ExternalSecret anywhere in the cluster — old or new — could actually pull from Secrets Manager. Fixed in `modules/eks-addons/external-secrets.tf` (IRSA role + trust policy, Helm release now names and annotates the ServiceAccount correctly).

The `ClusterSecretStore` + shared IRSA role is **cluster-wide**, scoped in IAM to `/bookstore/*`. Every service's `ExternalSecret` — old `db-secret` and every future microservice's own secret — reuses the same role. This is a deliberate simplification: true per-service secret isolation would need per-namespace `SecretStore` objects instead of one shared `ClusterSecretStore`, which is more machinery than this project's current stage justifies.

## GitOps / deployment flow

```
git push → GitHub Actions CI
  1. secret-scan (Gitleaks)
  2. sast (Semgrep, npm audit, unit tests)          ┐
     validate (ESLint, kubeconform)                  ├─ parallel
  3. build-and-push (Docker build → Trivy scan → ECR push)
  4. deploy (main branch only, manual approval gate):
       kustomize edit set image ... → commit → push
                    |
                    v
            ArgoCD polls repo every 3 min
                    |
                    v
        kustomize build k8s/overlays/prod
                    |
                    v
          reconciles cluster (auto-prune, self-heal)
```

CI never runs `kubectl` directly — it only edits image tags in git, and ArgoCD does the actual apply.

## The microservices platform (live — every frontend API call goes through it)

All 5 services are implemented, registered with ArgoCD, and live. **The frontend's every API call — `/books`, `/auth`, `/cart`, `/orders`, all of it — goes through `api-gateway`.** This isn't partial: `client/`'s build-time `API_URL` (`REACT_APP_API_URL`, set via the `API_URL` GitHub secret) points at `api.bookstore.<domain>`, which `api-gateway`'s `Ingress` exclusively owns — confirmed by inspecting the deployed JS bundle directly. `k8s/base/ingress/ingress.yaml` routes `bookstore.<domain>` (path `/`) to `frontend-service`.

```
frontend (React static assets, served by frontend-service via bookstore-ingress)
    |  every API call, unconditionally
api-gateway (Node/Express + http-proxy-middleware, JWT verification) — sole entry point
    ├── /books         → catalog-service         (GET public; POST needs a JWT; PUT/DELETE need admin role)
    ├── /auth, /users   → user-service            (login/register/profile)
    ├── /orders, /cart   → order-service           (cart, checkout, order history)
    └── (internal)        → notification-service    (called by order-service, not by the frontend directly)
```

The cutover is done — the old monolith's `backend/` (Node/Express, the `bookstore-backend` Rollout, its ECR repo, and every backend-only manifest) was deleted outright once confirmed to have zero ingress routes and zero references anywhere in the live frontend bundle.

Each service: own ECR repo, own K8s namespace, own Deployment/Service/HPA/PDB, own schema inside the *same* shared RDS instance (schema-level isolation, not per-service RDS — that's an explicit non-goal for now), own `/metrics` endpoint labeled `service="<name>"`. Deployed via a single ArgoCD `ApplicationSet` (`k8s/argocd/applicationset-microservices.yaml`) with a list generator, now listing all 5 services.

All 6 app Deployments (frontend + 5 services) carry `topologySpreadConstraints` on `topology.kubernetes.io/zone` then `kubernetes.io/hostname`, `whenUnsatisfiable: ScheduleAnyway` — a soft hint that spreads replicas across AZs and nodes without ever blocking a pod from scheduling (the cluster's pod-slot budget is tight, so a hard constraint would risk `Pending`). Zero extra pods or vCPU; it just biases the scheduler away from co-locating replicas so a lost node or AZ can't take a whole service down. Effective now for the 2-replica workloads (frontend, api-gateway); correct-by-default when HPA scales the others up.

`catalog-service`/`user-service`/`order-service`'s `NetworkPolicy`s were tightened once `api-gateway` existed — ingress is now scoped to the `gateway` namespace instead of allowing all traffic (see commit `153bed2`). `api-gateway` has a real public `Ingress` (`k8s/services/api-gateway/base/ingress.yaml`) for `api.bookstore.<domain>`, and it's now the sole claimant of that host — `k8s/base/ingress/ingress.yaml`'s duplicate rule was removed as part of building the frontend's login/cart/checkout UI (see [Plan 5](#related)), which needed the gateway to be reliably reachable rather than winning by undefined nginx tie-breaking.

Explicitly deferred (see the design spec's Non-goals): service mesh / mTLS, async messaging (SQS), per-service RDS instances, distributed tracing, NetworkPolicy hardening beyond what's described above.

## Subnet layout

```
VPC 170.20.0.0/16 (us-west-1)

public[0]   170.20.1.0/24   us-west-1a   — IGW, ALB
public[1]   170.20.2.0/24   us-west-1c   — IGW, ALB
private[0]  170.20.3.0/24   us-west-1a   — EKS nodes
private[1]  170.20.4.0/24   us-west-1c   — EKS nodes
private[2]  170.20.5.0/24   us-west-1a   — EKS nodes
private[3]  170.20.6.0/24   us-west-1c   — EKS nodes
private[4]  170.20.7.0/24   us-west-1a   — RDS
private[5]  170.20.8.0/24   us-west-1c   — RDS
```

NAT gateway per AZ by default — one in each public subnet, each AZ's private subnets routed through the NAT in their own AZ, so losing an AZ only takes out that AZ's egress. NAT is a managed service and doesn't count against the account's EC2 vCPU quota, so per-AZ is available even while that quota is capped. Set `single_nat_gateway = true` on the `network` module to collapse back to one shared NAT in `public[0]` (cheaper, single-AZ SPOF) — the old demo default.

## Traffic flow (current, real split)

```
Static assets (HTML/JS/CSS):
Internet → Route53 (bookstore.<domain>) → (CloudFront, optional) → ALB
    → frontend Service (static React via nginx)

Every API call the loaded frontend makes:
Internet → Route53 (api.bookstore.<domain>) → (CloudFront, optional) → ALB
    → api-gateway Service (JWT verification on writes)
    → catalog-service / user-service / order-service / notification-service
    → RDS :3306 (per-service schema, shared instance)
```

The old backend's Argo Rollout (canary 10%→25%→50%→100%) is gone — deleted along with the rest of the monolith. There's no canary deploy anywhere in the platform right now; each microservice deploys as a plain rolling-update Deployment via its ArgoCD `ApplicationSet` entry.

## Related docs

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — how to actually stand this up
- [`UML.md`](UML.md) — application-layer UML: component/class/ER/sequence diagrams
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — real incidents (OBS-NNN/TF-NNN), symptom/cause/fix, referenced by ID throughout the codebase
- [`compliance/`](compliance/) — SOC 2/ISO 27001/PCI DSS policy documents (Information Security Policy, Incident Response Plan, BCP/DR Plan, Change Management SOP, Vendor Risk Management Policy, Data Classification & Retention Policy)
- [`../README.md`](../README.md) — tech stack, repo structure, local development, CI/CD overview
