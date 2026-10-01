# Bookstore — AWS Three-Tier Microservices Platform

A production-grade, cloud-native bookstore application on AWS, built as a reference implementation of a three-tier architecture cut over to microservices. Infrastructure is fully codified in Terraform, services are containerised with Docker, orchestrated on Kubernetes (EKS) via ArgoCD GitOps, and protected by a DevSecOps CI/CD pipeline with full observability.

## Documentation

| Doc | Covers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System-level view: current state, module graph, region layout, the microservices platform |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | How to stand this up from zero, step by step |
| [`docs/UML.md`](docs/UML.md) | Application-layer UML: component/class/ER diagrams, auth + checkout sequence diagrams |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Real incidents (OBS-NNN/TF-NNN), symptom/cause/fix — referenced by ID throughout the codebase |
| [`docs/compliance/`](docs/compliance/) | SOC 2 / ISO 27001 / PCI DSS policy documents |

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Tech Stack](#tech-stack)
3. [Repository Structure](#repository-structure)
4. [Prerequisites](#prerequisites)
5. [Local Development](#local-development)
6. [Building and Pushing Docker Images](#building-and-pushing-docker-images)
7. [Infrastructure, Deploy, and CI/CD](#infrastructure-deploy-and-cicd)
8. [Secret Management](#secret-management)
9. [Security Controls](#security-controls)
10. [GitHub Secrets Reference](#github-secrets-reference)
11. [License](#license)

---

## Architecture Overview

```
                                   Internet
                                      |
                         Route 53 (public zone, failover routing)
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

Everything runs in one EKS cluster (`bookstore-eks`, `us-west-1`), split across the `bookstore` namespace (frontend only) and five microservice namespaces (`catalog`, `user`, `order`, `notification`, `gateway`), deployed via ArgoCD from `k8s/overlays/prod` and `k8s/services/*/overlays/prod`. Monitoring (Prometheus, Grafana, Loki, Alertmanager) runs off-cluster on a dedicated EC2 instance — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for why.

**Traffic flow:**
1. User → Route 53 → ALB → frontend Service (React SPA via nginx)
2. Frontend calls `api.bookstore.<domain>` → same ALB → `api-gateway` (JWT verification, request routing)
3. `api-gateway` fans out to `catalog-service`, `user-service`, `order-service`, `notification-service`
4. Each service reads/writes its own schema in the shared RDS MySQL instance
5. Each service exposes `/metrics` (prom-client); the monitoring EC2 scrapes them via static/file_sd configs into Prometheus → Grafana

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Nginx (Alpine) |
| Microservices | Node.js, Express, mysql2, prom-client, helmet, morgan (HTTP access logs), winston (structured JSON app/error logs) |
| Database | MySQL 8.0 (RDS, Multi-AZ) |
| Container Registry | Amazon ECR |
| Orchestration | Kubernetes 1.31 on Amazon EKS |
| Ingress | AWS Load Balancer Controller (ALB) |
| Progressive Delivery | Argo Rollouts |
| Infrastructure as Code | Terraform ≥ 1.7, AWS provider ~5.0, Helm provider, Archive provider (Lambda zip packaging) |
| CI/CD | GitHub Actions |
| GitOps | ArgoCD |
| Secret Management | AWS Secrets Manager + External Secrets Operator |
| Observability | Prometheus + Grafana + Loki + Alertmanager on a dedicated EC2 (Docker Compose) |
| AIOps RCA | Alertmanager webhook → Lambda (Python 3.12) → Loki query → Claude API narrative → DynamoDB + SES email; static S3/CloudFront dashboard backed by a second read-only Lambda |
| Security Scanning | Trivy (containers + IaC config scan), Gitleaks (secrets), SonarCloud (code quality + coverage gate) |
| TLS | cert-manager + Let's Encrypt / ACM |
| Testing | Vitest per service (`vi.fn()` mock db), pytest + moto for the two RCA Lambdas |
| DR | Cross-region (us-west-2) ECR replication + RDS backup replication + Route53 failover |

---

## Repository Structure

```
.
├── terraform/                  # All infrastructure as code
│   ├── main.tf                  # Root config — module call order + Helm provider
│   ├── argocd.tf / cloudfront.tf / dr.tf
│   ├── iam.tf / observability-rbac.tf / outputs.tf / providers.tf / variables.tf / versions.tf
│   ├── modules/                  # Reusable modules
│   │   ├── ecr/                    # ECR repositories per service
│   │   ├── eks/                     # EKS cluster + OIDC + node group
│   │   ├── eks-addons/               # Helm: ALB controller, ESO, ArgoCD, Argo Rollouts, VPC CNI, EBS CSI
│   │   ├── aiops-rca/                # RCA webhook + dashboard-read Lambdas, DynamoDB, SQS DLQ, S3/CloudFront dashboard
│   │   ├── monitoring-ec2/            # Standalone Prometheus/Grafana/Loki/Alertmanager EC2
│   │   ├── network/                 # VPC, subnets, NAT gateway
│   │   ├── rds/                     # RDS MySQL (Multi-AZ)
│   │   ├── route53/                 # Public + private hosted zones
│   │   └── security/                # Security groups
│   └── environments/             # Per-environment tfvars templates (dev, staging)
│
├── client/                    # React frontend
│   ├── Dockerfile              # Multi-stage: build → Nginx
│   └── src/
│
├── services/                  # Microservices (all Node/Express, Vitest)
│   ├── api-gateway/            # JWT verification, request routing
│   ├── catalog-service/
│   ├── user-service/
│   ├── order-service/
│   └── notification-service/
│   # each: Dockerfile, app.js, logger.js (winston JSON), index.js, __tests__/
│
├── lambdas/                    # AIOps RCA pipeline, Python 3.12, stdlib + boto3 only
│   ├── rca-lambda/               # Alertmanager-triggered: Loki query → Claude → DynamoDB → SES
│   └── dashboard-read-lambda/    # Read-only API behind the dashboard (list/detail over DynamoDB)
│   # each: lambda_function.py, tests/ (pytest + moto), requirements-dev.txt
│
├── dashboard/                   # Static RCA report viewer (index.html/script.js/style.css), served via S3+CloudFront
│
├── k8s/                        # Kubernetes manifests (Kustomize base + overlays)
│   ├── base/                    # Shared frontend resources
│   ├── services/                # Per-microservice base + overlays
│   │   ├── api-gateway/
│   │   ├── catalog-service/
│   │   ├── user-service/
│   │   ├── order-service/
│   │   └── notification-service/
│   ├── overlays/dev/ , overlays/prod/
│   └── argocd/                  # ArgoCD Application manifests
│
├── scripts/                     # all Python 3, stdlib only. preflight, configure, init_backend,
│                                 #   init_domain, build_and_push, simulate_load, monitoring_credentials,
│                                 #   and the local-exec helpers Terraform invokes (derive_ses_smtp_password,
│                                 #   wait_for_alb_hostname, cleanup_eks_networking, delete_ingress_objects)
├── docs/                        # ARCHITECTURE.md, DEPLOYMENT.md, UML.md
└── .github/workflows/           # ci-cd.yml, terraform.yml, terraform-drift.yml
```

---

## Prerequisites

| Tool | Minimum Version | Purpose |
|---|---|---|
| Node.js | 18 | Local service/frontend development |
| Docker | 24 | Building images |
| Terraform | 1.10.0 | Provisioning AWS infrastructure (native S3 state locking) |
| AWS CLI | 2.x | ECR login, EKS kubeconfig, `local-exec` scripts |
| kubectl | 1.31 | Deploying k8s manifests, `local-exec` scripts |
| Python | 3.8 | Every `local-exec` provisioner + all `scripts/*.py` |
| helm | 3.x | Querying cluster add-ons (installed by Terraform) |
| kustomize | 5.x | Building manifests locally |

Run `python3 scripts/preflight.py` to verify `terraform` / `kubectl` / `aws` /
`python3` are on `PATH` and new enough, that AWS credentials resolve, and that
`config.env` is filled in. `make plan` and `make apply` run it automatically —
a missing or old tool fails there with a clear message instead of deep inside
a `terraform apply`.

---

## Local Development

### A microservice (e.g. `order-service`)

```bash
cd services/order-service
npm install

# Create .env with your local MySQL details
cat > .env <<EOF
DB_HOST=localhost
DB_USERNAME=root
DB_PASSWORD=yourpassword
DB_PORT=3306
DB_NAME=order_db
APP_PORT=3000
EOF

npm run dev     # nodemon, auto-reload
# or
npm start
```

Each service exposes `/metrics` (prom-client) alongside its API routes.

### Run tests (no database required)

```bash
cd services/<service-name>
npm test
# Vitest, vi.fn() mock db — no MySQL needed.
```

### Frontend

```bash
cd client
npm install
npm start          # development server
# or
npm run build      # production build → build/
```

---

## Building and Pushing Docker Images

```bash
# Usage -- account ID, region and API URL are read from config.env
python3 scripts/build_and_push.py <IMAGE_TAG>

# Example
python3 scripts/build_and_push.py v1.2.0

# Override any of them for a one-off
python3 scripts/build_and_push.py v1.2.0 --account-id 123456789012 --region us-west-1 \
  --api-url https://api.bookstore.your-domain.com
```

> The CI/CD pipeline performs these steps automatically on every merge to `main`. Manual use of this script is for hotfixes or pre-release testing only.

---

## Infrastructure, Deploy, and CI/CD

The platform is provisioned by 9 Terraform modules plus root-level cross-cutting resources (IAM/OIDC, CloudFront, DR), and deployed via ArgoCD GitOps across the frontend and five microservice namespaces. The 9th module, `aiops-rca`, provisions an Alertmanager-triggered root-cause-analysis pipeline (Lambda + Claude API + DynamoDB + SES, plus a static S3/CloudFront dashboard) — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#aiops-rca-pipeline). Full step-by-step instructions — Terraform state bootstrap, `config.env`/`scripts/configure.py`, the apply itself, and post-apply verification — live in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Module-by-module and traffic-flow detail is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

The GitHub Actions pipeline (`.github/workflows/ci-cd.yml`) runs, per push: secret scan (Gitleaks) → test/audit/validate (Vitest + coverage, npm audit, SonarCloud, kubeconform) → build-and-push (Docker build → Trivy scan → ECR push) → deploy on `main` (manual approval gate, `kustomize edit set image` → commit → ArgoCD sync).

![Bookstore CI/CD Pipeline](CICD_Diagram1.png)

<details>
<summary>Full stage-by-stage breakdown (secret scan, per-service test matrix, kubeconform, Trivy, GitOps image-tag bump, ArgoCD auto-sync)</summary>

![Bookstore DevSecOps CI/CD Pipeline detail](CICD_Diagram2.png)

</details>

---

## Secret Management

| Context | Mechanism | How it works |
|---|---|---|
| Production (EKS) | External Secrets Operator | ESO reads AWS Secrets Manager and creates native k8s Secrets in-cluster, per namespace |
| CI/CD pipeline | GitHub Secrets only | `AWS_ROLE_ARN`, `AWS_ACCOUNT_ID`, `API_URL` — no DB credentials in the pipeline at all |
| Local development | `.env` file | Never committed; see `.gitignore` |
| Terraform state | AWS Secrets Manager | RDS admin credentials at `/bookstore/db-credentials`, Grafana admin at `/bookstore/grafana-admin` |
| RCA Lambda | AWS Secrets Manager, config.env-driven | LLM API key at `/bookstore/llm-api-key` (Anthropic by default, or OpenAI/Gemini — set `LLM_API_KEY`/`LLM_PROVIDER`/`LLM_MODEL` in `config.env`, run `scripts/configure.py` + `terraform apply`) — Terraform creates the real secret value, not just an empty shell; manual `aws secretsmanager put-secret-value` is only needed if you leave the key blank in config.env |

**Rule:** No credential, password, or account ID should ever appear in plain text in any committed file.

---

## Security Controls

| Control | Implementation |
|---|---|
| Secret detection | Gitleaks scans every commit and full git history |
| Code quality / SAST | SonarCloud Quality Gate (bugs, code smells, security hotspots, coverage) |
| Unit tests | Vitest per service — runs before audit in CI |
| Dependency CVEs | `npm audit --omit=dev --audit-level=high` per service and frontend |
| Container CVEs | Trivy blocks pushes on CRITICAL/HIGH unfixed vulns |
| IaC security | Trivy's config scanner runs on every Terraform change |
| Image provenance | Every pushed image signed with cosign (keyless, GitHub OIDC) — `cosign verify` proves which CI run built it |
| Dependency freshness | Dependabot — weekly PRs across npm, pip, Terraform, Docker base images, GitHub Actions |
| Account-level audit trail | CloudTrail, multi-region, log file validation on — see `terraform/cloudtrail.tf`. S3 Object Lock (compliance mode) is available but off by default (`enable_cloudtrail_object_lock`), since it makes this project's own destroy-and-recreate dev workflow permanently orphan the bucket (see `docs/TROUBLESHOOTING.md` OBS-074) |
| No static AWS keys | GitHub OIDC → IAM role assumption |
| Secrets in-cluster | External Secrets Operator + AWS Secrets Manager |
| Non-root containers | All pods run as non-root |
| Read-only filesystems | `readOnlyRootFilesystem: true` on all app containers |
| Network segmentation | Kubernetes NetworkPolicy restricts pod-to-pod traffic |
| TLS everywhere | cert-manager + Let's Encrypt / ACM; force-redirect HTTP → HTTPS |
| Progressive delivery | Argo Rollouts — easy rollback if errors spike |
| Manual deploy gate | GitHub Environments `production` requires reviewer approval |

---

## GitHub Secrets Reference

Configure these in **Settings → Secrets and variables → Actions** before running the pipeline:

| Secret | Description | Example |
|---|---|---|
| `AWS_ACCOUNT_ID` | Your 12-digit AWS account ID | `123456789012` |
| `AWS_ROLE_ARN` | ARN of the OIDC IAM role the pipeline assumes | `arn:aws:iam::123456789012:role/bookstore-github-oidc-role` |
| `API_URL` | Public URL of the api-gateway (injected into the React build) | `https://api.bookstore.your-domain.com` |
| `SONAR_TOKEN` | SonarCloud auth token — sonarcloud.io → My Account → Security | `token...` |
| `SONAR_ORGANIZATION` | SonarCloud organization key | `kandukurisaikrishna` |
| `SONAR_PROJECT_KEY` | SonarCloud project key | `KANDUKURIsaikrishna_aws_three_tier_archi_observability` |

---

## License

[MIT](LICENSE)
