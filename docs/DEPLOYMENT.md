# Deployment

How to actually stand this project up, end to end, from a fresh AWS account. This is the "nothing exists yet" path (verified 2026-07-31: `aws eks describe-cluster --name bookstore-eks` returns `ResourceNotFoundException` — nothing is running, despite a stale local `kubectl` context suggesting otherwise. Always verify against AWS directly, never trust a cached kubeconfig).

> All Terraform (`*.tf`, `modules/`, `environments/`) lives under `terraform/`, not repo root — every raw `terraform` command below runs from inside that directory (`cd terraform` first). `make plan`/`make apply`/`make destroy` from repo root handle this automatically (`Makefile` uses `terraform -chdir=terraform`), if you'd rather not `cd` by hand.

## Contents

- [Before you start](#before-you-start)
- [Step 1 — Fill in your config, generate `terraform.tfvars`](#step-1--fill-in-your-config-generate-terraformtfvars)
- [Step 2 — Bootstrap Terraform state](#step-2--bootstrap-terraform-state-once-per-aws-account)
- [Step 3 — Bootstrap the domain](#step-3--bootstrap-the-domain-once-per-domain-ever)
- [Step 4 — One apply, everything](#step-4--one-apply-everything)
- [Populate the LLM API key (RCA pipeline)](#populate-the-llm-api-key-rca-pipeline)
- [Optional: harden for a real (non-demo) account](#optional-harden-for-a-real-non-demo-account)
- [Step 5 — Configure GitHub Secrets & Variables](#step-5--configure-github-secrets--variables-for-cicd)
- [Step 6 — Import known-conflicting entries](#step-6--import-known-conflicting-entries-if-re-deploying)
- [Step 7 — Confirm the ExternalSecrets IRSA fix](#step-7--confirm-the-externalsecrets-irsa-fix-actually-took)
- [Step 8 — Watch all apps come up](#step-8--watch-all-apps-come-up)
- [Ongoing deploys](#ongoing-deploys-once-the-initial-stand-up-is-done)
- [Monitoring access](#monitoring-access)
- [Tearing it down](#tearing-it-down)
- [Related](#related)

## Before you start

**Required tooling:**

- AWS credentials configured (`aws sts get-caller-identity` should work) with permissions to create VPCs, EKS clusters, RDS instances, IAM roles, etc.
- `terraform` >= 1.10.0 (native S3 state locking needs it), `kubectl`, `aws` CLI, and `python3` — all four on `PATH` on whatever machine runs `terraform apply`.
  > Every `local-exec` provisioner in this Terraform config invokes `python3` directly (`interpreter = ["python3"]`, not a shell) to run a real script from `scripts/` — ALB hostname discovery, SES SMTP password derivation, destroy-time Ingress/flow-log-group cleanup — so it behaves identically on Windows, macOS, and Linux instead of assuming a bash-compatible shell exists. `helm` itself isn't needed on your machine — the `helm` Terraform provider talks to the Helm API directly, no CLI required.
- A domain you control (for `terraform.tfvars`' `domain` value — ACM DNS validation needs it).

**Check the toolchain first:**

```bash
python3 scripts/preflight.py
```

Verifies all four tools are on `PATH` and new enough (`terraform` >= 1.10.0), that `aws sts get-caller-identity` works, and that `config.env` has its 5 required keys. Report only — it never installs anything or touches `PATH`; on a failure it prints the install command for your OS and exits non-zero. `make plan` and `make apply` run it automatically as a prerequisite, so a missing or old tool fails here with a clear message instead of deep inside an apply (usually `null_resource.wait_for_alb_hostname`).

**One-time GitHub OIDC provider** (not a Terraform resource — `iam.tf`'s trust policy just references its ARN by string, so Terraform never checks it exists):

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

Safe to skip if it already exists — `aws iam list-open-id-connect-providers` to check first; the create call fails loudly (`EntityAlreadyExists`) if you skip that check and run it twice.

> **Time & cost:** expect roughly 20-30 minutes, and real money the moment RDS/EKS/the monitoring EC2 exist. Don't run `terraform apply` on the full stack "just to see what happens." This branch removed some unnecessary serialization in the Terraform graph — RDS/EKS already ran concurrently, but `eks-addons`'s 5 Helm charts now all install concurrently instead of partly one-after-another, and `monitoring-ec2` no longer waits on all of `eks-addons` to finish. See [`ARCHITECTURE.md`](ARCHITECTURE.md#terraform-module-graph). Not yet verified against a real apply — if a Helm release in `eks-addons` times out, check `terraform apply`'s own error output for which chart failed and re-run; Helm releases here are idempotent, so a re-apply picks up where it left off.

## Step 1 — Fill in your config, generate `terraform.tfvars`

```bash
cp config.env.example config.env
# edit config.env: AWS_ACCOUNT_ID, AWS_REGION, DOMAIN, GITHUB_REPO, ALERT_EMAIL
# (GITHUB_BRANCH and SECONDARY_REGION are optional -- default to "main"
# and "us-west-2" respectively if left unset)
python3 scripts/configure.py
```

Do this **before** Step 2 — Step 2's backend bootstrap reads `AWS_REGION` from this same `config.env` file, so it needs to exist first (see Step 2's own notes on region resolution).

> **Any AWS region works here**, not just `us-west-1` — AZs (`locals.tf`), the ECR registry URL, and the `ClusterSecretStore`'s region are all derived from `AWS_REGION`/`var.aws_region`, nothing left hardcoded. If you set `AWS_REGION` to anything other than `us-west-1`, also set a matching `AWS_REGION` repo **Variable** in GitHub (Settings → Secrets and variables → Actions → **Variables** tab, not Secrets — the region isn't sensitive). `.github/workflows/ci-cd.yml`, `terraform.yml`, and `terraform-drift.yml` all fall back to `us-west-1` if that Variable isn't set, which would build/push images and run `terraform plan`/`apply` against the wrong region.

`terraform.tfvars` is **generated, not hand-written** — `scripts/configure.py` is the only supported way to produce it (also stated on `alert_email`'s own description in `variables.tf`: "don't hand-edit it here directly"). The script does two things:

1. **Writes `terraform.tfvars`** with the 4 variables that have no safe default (`aws_region`, `domain`, `github_repo`, `alert_email`) — everything else in `variables.tf` ships with a working default. Leave `primary_alb_dns` and `secondary_alb_dns` out of `config.env` entirely:
   - `primary_alb_dns` is auto-discovered within the same apply now (see Step 4) — only set it by hand afterward if you want to override discovery and point DNS at a different/manually-managed load balancer.
   - `secondary_alb_dns` stays empty until a secondary-region EKS cluster actually exists (it doesn't yet — see [`ARCHITECTURE.md`](ARCHITECTURE.md#region-layout)).
2. **Stamps your real domain/repo/account ID/region** over placeholder values (`YOUR_DOMAIN_HERE.com`, `YOUR_GITHUB_USERNAME/aws_three_tier_code`, `ACCOUNT_ID`, `AWS_REGION_HERE`) in five checked-in template files:
   - `k8s/base/ingress/ingress.yaml`
   - `k8s/services/api-gateway/base/configmap.yaml` (`FRONTEND_URL`, used for CORS)
   - `k8s/argocd/application.yaml`
   - `k8s/overlays/prod/kustomization.yaml`
   - `k8s/base/secrets/external-secret.yaml` (the shared `ClusterSecretStore`'s `region` field — every service's `ExternalSecret` references this one by name, so a wrong region here breaks secret sync cluster-wide)

**Commit and push those 5 stamped files before your first ArgoCD sync matters** — ArgoCD deploys `k8s/base` and `k8s/services` content straight from git, not from whatever's sitting on your local disk. Skip this and the very first sync deploys the literal placeholder strings, not your real domain:

```bash
git add k8s/base/ingress/ingress.yaml k8s/services/api-gateway/base/configmap.yaml \
        k8s/argocd/application.yaml k8s/overlays/prod/kustomization.yaml \
        k8s/base/secrets/external-secret.yaml
git commit -m "chore: configure for <your-domain>"
git push
```

> `k8s/argocd/application.yaml` itself is read directly off local disk by `argocd.tf`'s `kubectl_manifest` resource at `terraform apply` time — pushing it isn't strictly required for that one apply to pick up the right value, but commit it anyway so the checked-in file matches what's actually running.

`config.env` and `terraform.tfvars` are both gitignored — never commit either one.

## Step 2 — Bootstrap Terraform state (once per AWS account)

```bash
python3 scripts/init_backend.py
```

Creates the S3 bucket, patches `versions.tf` in place with the real bucket name *and region*, runs `terraform init`. State locking is native S3 conditional-write locking (`use_lockfile = true`, no DynamoDB table).

> Skipping this step means Terraform silently uses local state — `terraform plan` will look like it wants to create everything from scratch even if a cluster is already running elsewhere, because local state has no idea what exists. **If a `terraform plan` ever shows a suspiciously large "to add" count, check `terraform state list` and confirm the backend is actually configured before doing anything else.**

Region resolution here is layered, in priority order:

1. An explicit CLI arg (`python3 scripts/init_backend.py us-west-2`, if you want to override)
2. `AWS_REGION` from `config.env` (the normal path, since Step 1 already created it)
3. `us-west-1` as a last-resort default if neither is set

The Terraform backend block in `versions.tf` genuinely cannot reference `var.aws_region` at all — Terraform resolves backend configuration before any variables are evaluated, a real HCL limitation, not an oversight — so this script patching the literal value in is the only way that field ever stays correct.

## Step 3 — Bootstrap the domain (once per domain, ever)

```bash
python3 scripts/init_domain.py
```

Creates the public Route53 hosted zone for `DOMAIN` (from `config.env`) if it doesn't already exist, and prints the 4 NS values to set at your registrar (GoDaddy, Namecheap, etc.).

**Do this now, before Step 4** — Terraform reads this zone via a `data` lookup, it never creates or destroys it, so the registrar update only ever needs to happen once for this domain's whole lifetime, not once per apply/destroy cycle. Set the NS values now and give them a few minutes to propagate while Step 4 works through the rest of the stack; if `aws_acm_certificate_validation.ingress` still hangs in Step 4, the NS records haven't propagated yet.

## Step 4 — One apply, everything

```bash
cd terraform
terraform plan -out=tfplan
# review it — expect ~145 resources on a genuinely fresh account, plus the
# aiops-rca module's own ~30 (see below):
#   VPC + subnets + NAT + IGW + S3 endpoint, security groups, 2 ACM certs
#   (CloudFront's, off by default, + the real one the ALB uses), RDS instance,
#   private Route53 zone (the public zone is looked up, not created — see Step 3),
#   ECR repos, EKS cluster + node group + OIDC provider,
#   eks-addons (ESO, AWS Load Balancer Controller, ArgoCD, Argo Rollouts),
#   monitoring EC2 + EIP, GitHub OIDC role,
#   the ArgoCD AppProject + Application + ApplicationSet (kubectl_manifest, see below),
#   aiops-rca: DynamoDB table + GSI, SQS DLQ, the LLM API key secret shell,
#   the RCA Lambda + its VPC security group + IP-restricted webhook API Gateway,
#   the dashboard-read Lambda + open HTTP API Gateway, S3 bucket + CloudFront
#   distribution + OAC for the static dashboard (see ARCHITECTURE.md#aiops-rca-pipeline),
#   a multi-region CloudTrail trail + its own S3 bucket (Object Lock,
#   COMPLIANCE mode) -- see ARCHITECTURE.md's "No CloudWatch..." section
terraform apply tfplan
```

> **Why one apply instead of two:** this used to need a second apply — Terraform couldn't create the public Route53 record until it knew the ingress load balancer's hostname, and that didn't exist until after `eks-addons` finished, so you had to check it by hand, paste it into `terraform.tfvars`, and apply again. `argocd.tf`'s `data "kubernetes_ingress_v1" "bookstore"` now reads that hostname within the same apply, gated behind a `null_resource` that first polls for the `bookstore-ingress` Ingress object to exist at all (it's deployed by ArgoCD, asynchronously — not created directly by this apply the way ingress-nginx's Helm-installed Service used to be), then `kubectl wait --for=jsonpath=...` for the AWS Load Balancer Controller to finish provisioning the real ALB and populate its hostname. One apply, start to finish, just with a wider safety-margin timeout than the old single-stage wait needed.

`argocd.tf` also applies `k8s/argocd/appproject.yaml`, `k8s/argocd/application.yaml`, and `k8s/argocd/applicationset-microservices.yaml` directly (via the `kubectl_manifest` resource, `gavinbunney/kubectl` provider) — no more manual `kubectl apply -f k8s/argocd/...` after the fact. All three wait on `module.eks_addons` (they need ArgoCD's CRDs to exist); the Application and ApplicationSet additionally wait on the AppProject, since ArgoCD rejects either one naming a project that doesn't exist.

RDS (~10-15 min) and EKS (~15-20 min) are the slow parts and provision concurrently, since neither depends on the other directly (both depend on `network`/`security`, not on each other). The `eks-addons` Helm releases run after the cluster is up, now fully concurrently with each other too (see [`ARCHITECTURE.md`](ARCHITECTURE.md#terraform-module-graph)). As long as Step 3's NS records were set and have propagated, `aws_acm_certificate_validation.ingress` resolves on its own within a few minutes — no manual registrar step here anymore (that used to be required after *every* destroy+recreate cycle, since the public zone was Terraform-managed and got brand-new NS values each time it was recreated; it's now a `data` lookup instead, see Step 3).

### Populate the LLM API key (RCA pipeline)

A real API key isn't derivable from anything Terraform has, so it always comes from a human. The RCA Lambda calls Anthropic's Claude API by default. **Switching provider or model — including later, to rotate a key or try a different model — never means hand-editing `terraform.tfvars` or any code.** It's always the same three lines in `config.env`, then the same two commands:

**Preferred: set it in `config.env` before you apply.**

```bash
# in config.env:
LLM_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic        # or "openai" / "gemini" -- must match the key above
LLM_MODEL=                    # optional -- blank uses a sensible per-provider default

python3 scripts/configure.py   # regenerates terraform.tfvars with it
terraform apply                # creates the secret's real value, not just the shell
```

To switch provider, rotate a key, or try a different model later: change those same three lines in `config.env` and re-run the same two commands — nothing else. `config.env` and the `terraform.tfvars` it generates are both gitignored, so the key never touches git; `terraform plan`/`apply` never prints it either (the variable is `sensitive`). It does land in Terraform state, in the same encrypted S3 backend every other secret in this project's state already sits in (DB credentials, JWT secret, Grafana admin) — a deliberate, consistent tradeoff, not a new one.

A model name can stop working between one apply and the next — providers retire models. If the RCA Lambda's CloudWatch logs show the LLM call failing with a 404 naming the model, that's not a bug here; update `LLM_MODEL` in `config.env` to whatever the provider's error message recommends and re-run the two commands above.

**Alternative: leave `LLM_API_KEY` blank and populate the secret by hand instead**, once, after apply:

```bash
aws secretsmanager put-secret-value \
  --secret-id /bookstore/llm-api-key \
  --secret-string "sk-ant-..."
```

Either way, until the secret has a real value, the RCA Lambda (triggered by Alertmanager on a firing alert) still runs, still queries Loki, and still writes a report — but the report's `narrative` field comes back as whatever error the LLM API call raised (auth failure), not a real root-cause analysis. Skip this entirely if you don't want the RCA pipeline live yet; nothing else in the stack depends on it.

By default the RCA Lambda calls Claude Haiku, not Sonnet (`var.claude_model`, cheaper per token — this is a log-summarization task, not deep reasoning) with `max_tokens` capped at 700 (`var.claude_max_tokens`) and the prompt capped at 12 log lines per service, 400 characters each (`var.max_log_lines_per_service` / `var.max_log_line_chars`) — the biggest cost lever for this pipeline is prompt size (up to 5 services' logs in one call), so these caps exist specifically to keep the token bill predictable regardless of how noisy a service's logging gets. Override any of them in `terraform.tfvars` if you want deeper (pricier) analysis for a harder incident.

### Optional: harden for a real (non-demo) account

Everything below defaults to this project's usual dev-cycle-friendly posture (fast, cheap, frequently destroyed/recreated) and needs an explicit opt-in for anything closer to production. None of it is required to stand the stack up.

- **`enable_rds_secret_rotation`** (default `false`) — deploys AWS's official single-user MySQL rotation Lambda (via the Serverless Application Repository) and wires it into `/bookstore/db-credentials`. Read the comment at the top of `terraform/rds-secret-rotation.tf` before turning this on — it was written and `terraform validate`'d without a live AWS account to check the SAR app's current parameter schema against, so confirm that first with `aws serverlessrepo get-application`.
- **`secrets_recovery_window_days`** (default `0`) — every Secrets Manager secret in this project force-deletes on destroy with no soft-delete window, deliberately, so this stack can be torn down and rebuilt often during development (see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) TF-012). Set to `7`-`30` for a real account so an accidental delete/taint of a live credential is recoverable.
- **`cloudtrail_retention_days`** (default `400`) — how long CloudTrail logs are locked under S3 Object Lock before they can be cleaned up. 400 (~1yr + margin) is a reasonable audit-evidence baseline; raise it if a specific compliance framework you're targeting requires longer.
- **GitHub branch protection on `main`** — already configured on this project's own canonical repo (required CODEOWNERS review, required CI status checks, no force-push/deletion) directly via `gh api`, **not** by Terraform or `scripts/configure.py`. A fork or a differently-named repo needs its own — see [`docs/compliance/CHANGE_MANAGEMENT_SOP.md`](compliance/CHANGE_MANAGEMENT_SOP.md) for the exact settings.

See [`docs/compliance/`](compliance/) for the full SOC 2/ISO 27001/PCI DSS policy set these controls exist to satisfy, and [`docs/compliance/INFORMATION_SECURITY_POLICY.md`](compliance/INFORMATION_SECURITY_POLICY.md) specifically for the cryptography/rotation rationale.

## Step 5 — Configure GitHub Secrets & Variables for CI/CD

Nothing in `.github/workflows/` works until these exist — every push fails predictably at the AWS-auth step otherwise, on a genuinely fresh repo. `AWS_ROLE_ARN` names the `aws_iam_role.github_oidc` role Step 4's apply just created (using *your own* local AWS CLI credentials — CI has no way to bootstrap that role itself, since it needs the role to authenticate in the first place). The OIDC identity provider that role's trust policy points at is already in place too, from **Before you start**. From here on, every CI-driven apply uses OIDC — no static AWS keys ever touch GitHub.

### Setting up SonarCloud (once, per fork/account)

`.github/workflows/ci-cd.yml`'s `test` job runs `SonarSource/sonarqube-scan-action`, feeding it the 6 services' `lcov.info` coverage output and reporting to sonarcloud.io — no self-hosted Sonar server, nothing runs on your own infrastructure. It authenticates with a token and passes the org/project key as CLI args (`-Dsonar.organization`, `-Dsonar.projectKey`), not a checked-in `sonar-project.properties` — there isn't one in this repo, by design, since org/project key differ per fork/account and shouldn't be hardcoded into a file everyone shares.

1. **Sign up** at [sonarcloud.io](https://sonarcloud.io) — "Sign up with GitHub" is the fast path; it also handles the GitHub App install/permissions step for you.
2. **Create an organization** (free tier — "Sonar Way" is fine): sonarcloud.io → **+** → **Create new organization** → pick "Free plan" → import from GitHub (select your account/org). This mints your **org key** — visible right in the URL (`sonarcloud.io/organizations/<org-key>`) and under **Administration → Organization settings**.
3. **Create the project**: **+** → **Analyze new project** → pick this repo from the GitHub list (install/grant the SonarCloud GitHub App access to it first if it's not showing). This mints the **project key** — shown on the project's **Information** panel, and changeable later under **Administration → Update Key**. It defaults to `<org>_<repo-name>`.
4. **Switch off Automatic Analysis** — SonarCloud defaults new projects to analyzing on its own via the GitHub App, which conflicts with (and gets silently skipped in favor of) CI-driven analysis. **Administration → Analysis Method** → toggle **Automatic Analysis** off. Skip this and the CI-based scan can report "automatic analysis is enabled" and refuse to accept the CI's results.
5. **Generate the token**: avatar (top-right) → **My Account → Security → Generate Token**. Type **Project Analysis Token**, scoped to just this project, is enough — no need for a broader user/global token. **Copy it now**; SonarCloud shows it exactly once and never again. This is `SONAR_TOKEN`.

Free-tier note: public repos are free automatically; private repos are free up to a per-organization lines-of-code cap (check your plan on sonarcloud.io if `test` starts failing with a LOC-limit error instead of a quality-gate one).

In GitHub: **Settings → Secrets and variables → Actions**.

**Secrets** tab:

| Secret | Value |
|---|---|
| `AWS_ACCOUNT_ID` | `aws sts get-caller-identity --query Account --output text` |
| `AWS_ROLE_ARN` | `terraform output -raw github_oidc_role_arn` — only exists after Step 4's apply |
| `API_URL` | `https://api.bookstore.<your-domain>` |
| `SONAR_TOKEN` | from step 5 above — the Project Analysis Token, copied at generation time |
| `SONAR_ORGANIZATION` | your org key from step 2 above (URL / Administration → Organization settings) |
| `SONAR_PROJECT_KEY` | your project key from step 3 above (project's Information panel) |

**Variables** tab (not sensitive — skip entirely if deploying to `us-west-1`):

| Variable | Value |
|---|---|
| `AWS_REGION` | must match `config.env`'s `AWS_REGION` from Step 1, or CI defaults to `us-west-1` regardless of where Terraform actually provisioned |

> Until all the Secrets above exist, expect exactly two failures on every push, both normal for a fresh repo: `Terraform CI/CD` fails at "Configure AWS credentials (OIDC)" (no `AWS_ROLE_ARN` yet), and `DevSecOps Pipeline` fails at the SonarCloud step (no `SONAR_TOKEN` yet). Neither is a region problem or a code problem — just missing secrets.

## Step 6 — Import known-conflicting entries (if re-deploying)

Only needed if this isn't a truly fresh account — a lost or switched Terraform state backend (e.g. pointing `init_backend.py` at a different S3 bucket than a previous session used) can leave entries in AWS that the *current* state doesn't know about, even though a real `terraform destroy` against the *correct* state cleans all of them up properly: three Secrets Manager secrets (`/bookstore/db-credentials`, `/bookstore/grafana-admin`, `/bookstore/jwt-secret`) plus the SES email identity for `ALERT_EMAIL`. Confirmed live 2026-08-26 against an account with two different state buckets from past testing — an apply against the "wrong" one hit `AlreadyExistsException` on `aws_sesv2_email_identity.alerts`:

```bash
make import
```

Safe no-op on a genuinely fresh account (`|| echo already in state` on each).

## Step 7 — Confirm the ExternalSecrets IRSA fix actually took

This bit silently broke every secret sync in the cluster until fixed on this branch (see [`ARCHITECTURE.md`](ARCHITECTURE.md#secrets-flow-and-the-bug-that-used-to-break-it)) — don't skip verifying it:

```bash
kubectl get serviceaccount external-secrets-sa -n external-secrets -o jsonpath='{.metadata.annotations}'
# should contain: "eks.amazonaws.com/role-arn":"arn:aws:iam::<account>:role/bookstore-external-secrets"

kubectl get clustersecretstore aws-secretsmanager -o jsonpath='{.status.conditions[0]}'
# should show "status":"True","type":"Ready" -- if this isn't Ready, every
# ExternalSecret below will fail regardless of anything else being correct,
# since they all reference this one ClusterSecretStore by name

kubectl get externalsecret admin-db-secret -n catalog
# STATUS column should show SecretSynced, not an error -- every microservice
# (catalog/user/order/notification/gateway) has its own admin-db-secret,
# this one's just picked as the first to check
```

## Step 8 — Watch all apps come up

Both `k8s/argocd/application.yaml` (the `bookstore` Application — the React frontend and its shared namespace resources: storage class, secrets bootstrap, network policy, PDB, quota) and `k8s/argocd/applicationset-microservices.yaml` (all 5 backend microservices: catalog, user, order, notification, api-gateway, one ArgoCD `Application` each) were already applied by Terraform in Step 4 — nothing to `kubectl apply` here. There is no backend monolith anymore; the original single frontend/backend pair was fully replaced by these 5 microservices, and `application.yaml` deploys frontend only.

Just watch ArgoCD reconcile, within 3 minutes of the apply finishing:

```bash
kubectl get applications -n argocd
kubectl get applicationsets -n argocd
kubectl get pods -n bookstore
kubectl get pods -n catalog
kubectl get pods -n user
kubectl get pods -n order
kubectl get pods -n notification
kubectl get pods -n gateway
```

For catalog-service/user-service/order-service/notification-service, ArgoCD's sync also runs each service's own `<service>-schema-init` PreSync hook Job automatically (creates its schema, creates its own DB user) — no manual secret-copying, no manual Job apply. Each reads its own admin credentials from an `admin-db-secret` ExternalSecret, which pulls the same `/bookstore/db-credentials` entry the old monolith already uses, materialized into that service's namespace by ESO. `api-gateway` has no schema-init Job — it's stateless.

Watch any service's hook if you want to confirm it ran cleanly:

```bash
kubectl get jobs -n catalog
kubectl logs job/catalog-schema-init -n catalog   # only exists briefly — hook-delete-policy removes it after success
```

`api-gateway` has a real public `Ingress` for `api.bookstore.<domain>` — the old monolith's ingress no longer declares that host (the collision described in earlier revisions of this doc is resolved), so `api.bookstore.<domain>` reaching `api-gateway` is the live, working path, not something to route around:

```bash
curl -s https://api.bookstore.<domain>/health
```

If you'd rather bypass DNS/ingress entirely (e.g. verifying straight after an apply, before DNS has propagated), `kubectl port-forward` still works the same as always:

```bash
kubectl port-forward -n gateway svc/gateway-service 8082:80
curl -s http://localhost:8082/health
```

If images haven't been built/pushed by CI yet (first-ever deploy, before any CI run has landed), all 6 pods (`frontend` + the 5 microservices) will sit in `ImagePullBackOff` until real images exist in their ECR repos — **this is expected on a genuinely fresh account**, not a sign anything is broken. Either wait for a CI run to land on `main` (fastest — just push any commit), or push once by hand per service:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=$(grep '^AWS_REGION=' config.env | cut -d= -f2)   # whatever you set in Step 1 -- never hardcode this
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# Frontend
docker build -t "$REGISTRY/bookstore-frontend:manual" client/
docker push "$REGISTRY/bookstore-frontend:manual"
(cd k8s/overlays/prod && kustomize edit set image bookstore-frontend="$REGISTRY/bookstore-frontend:manual")

# Each of the 5 microservices follows the identical pattern -- swap the name:
docker build -t "$REGISTRY/bookstore-catalog-service:manual" services/catalog-service/
docker push "$REGISTRY/bookstore-catalog-service:manual"
(cd k8s/services/catalog-service/overlays/prod && kustomize edit set image bookstore-catalog-service="$REGISTRY/bookstore-catalog-service:manual")
# ...repeat for user-service, order-service, notification-service, api-gateway

git add k8s/overlays/prod/kustomization.yaml k8s/services/*/overlays/prod/kustomization.yaml
git commit -m "chore: manual image push for first deploy" && git push
```

Verify catalog-service directly (bypassing the gateway, useful for isolating whether a problem is in the service itself or in the gateway/ingress path):

```bash
kubectl port-forward -n catalog svc/catalog-service 8081:80
# in another terminal:
curl -s http://localhost:8081/health
curl -s http://localhost:8081/books
curl -s http://localhost:8081/metrics | grep 'service="catalog-service"'
```

### Verify the frontend end-to-end (real UI, not just curl)

The React app at `bookstore.<domain>` has a real login/cart/checkout/order-history flow wired to `api-gateway` — worth clicking through after any deploy that touches `client/` or the gateway:

1. Open `https://bookstore.<domain>` — should show the book catalog (public, no login needed).
2. Register a new account, then log in.
3. Click "Add to Cart" on a book, go to Cart, adjust quantity, proceed to Checkout, place the order.
4. Check Orders — the placed order should show with status `pending`.
5. Log out, confirm `/cart`, `/checkout`, `/orders` all redirect to `/login` when visited directly while logged out.

Equivalent via `curl` if you don't have browser access (e.g. testing from a box without a display):

```bash
curl -s https://api.bookstore.<domain>/auth/register -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass123"}'
TOKEN=$(curl -s https://api.bookstore.<domain>/auth/login -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass123"}' | jq -r .token)
curl -s https://api.bookstore.<domain>/cart -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"book_id":1,"quantity":1}'
curl -s -X POST https://api.bookstore.<domain>/orders/checkout -H "Authorization: Bearer $TOKEN"
curl -s https://api.bookstore.<domain>/orders -H "Authorization: Bearer $TOKEN"
```

## Ongoing deploys (once the initial stand-up is done)

You almost never run `kubectl apply` for app changes after this point — push to `main`, let CI build/scan/push the image, approve the `deploy` job's manual gate, and ArgoCD picks it up within 3 minutes. See [`ARCHITECTURE.md`](ARCHITECTURE.md) (GitOps / deployment flow section).

## Monitoring access

```bash
cd terraform
terraform output grafana_url        # Grafana, default user "admin"
terraform output prometheus_url     # Prometheus, also user "admin"
terraform output alertmanager_url   # Alertmanager, also user "admin"
aws secretsmanager get-secret-value --secret-id /bookstore/grafana-admin --query SecretString --output text
aws secretsmanager get-secret-value --secret-id /bookstore/monitoring-basic-auth --query SecretString --output text

# RCA pipeline (no login — the webhook is IP-restricted to the monitoring EC2
# itself, not user-facing; the dashboard is open, read-only)
terraform output rca_dashboard_url            # static S3+CloudFront RCA report viewer
terraform output rca_dashboard_read_api_url   # what the dashboard's script.js calls
terraform output rca_webhook_invoke_url       # what Alertmanager POSTs alerts to
```

> **On Windows**, run `python3 scripts/monitoring_credentials.py` instead of the block above — Git Bash silently mangles any argument starting with `/` (like `--secret-id /bookstore/grafana-admin`) into a Windows path before it reaches `aws.exe`, which fails with a confusing "Invalid name" error. The script prints every URL + password in one table, sidestepping that entirely.

`Makefile` has `make monitoring-status` (Docker Compose status on the box) and `make monitoring-logs` (tails the init/dashboard-import logs) — both auto-fetch an auto-generated SSH key from Terraform state via a `monitoring-key` prerequisite target (saved locally as `.monitoring-ssh-key.pem`, gitignored), no manual key management needed.

## Tearing it down

```bash
cd terraform
terraform destroy
```

**The public Route53 zone survives `terraform destroy` untouched** — `module.route53`'s public zone is `data "aws_route53_zone" "public"` (`terraform/modules/route53/main.tf`), a lookup, not a managed resource; Terraform never creates or destroys it (see Step 3). Confirmed live 2026-09-17: a full destroy left `b17catsvsdogs.xyz`'s zone, NS records, and registrar delegation completely untouched — no re-pointing needed on the next apply. (An older revision of this doc claimed the opposite — that the zone got torn down and rebuilt with new nameservers every cycle — that was true before this data-lookup refactor and is no longer accurate.)

**`aws_s3_bucket.cloudtrail` destroys cleanly by default** — `var.enable_cloudtrail_object_lock` defaults to `false` specifically so this project's destroy-and-recreate dev workflow (`make apply` → verify → `make destroy`, repeat) leaves nothing behind; `force_destroy = true` on the bucket empties it on every `terraform destroy`, no manual steps, no orphan. **If you turn `enable_cloudtrail_object_lock` on** (a real audit-scoped deployment, where the whole point is that CloudTrail logs can't be deleted), the tradeoff flips completely: `terraform destroy` will then fail on this bucket specifically with `BucketNotEmpty`, every time, permanently, for as long as any logged event is still inside `var.cloudtrail_retention_days` — confirmed live 2026-09-17, and not a bug; it's what "immutable audit trail" means. `make destroy` handles that case automatically (runs the destroy, then unconditionally `terraform state rm`s the bucket so it can't block the next `plan`/`apply`; `make apply`/`make import` re-adopts the same surviving bucket on the next cycle) — but if you're running `terraform destroy` directly instead of `make destroy` with the lock enabled, run `terraform state rm aws_s3_bucket.cloudtrail` yourself afterward. See `docs/TROUBLESHOOTING.md` OBS-074 for the full incident, including why the bucket is named `bookstore-cloudtrail-<account_id>-v2` (the un-suffixed name is permanently claimed by a bucket orphaned during this exact discovery).

This project's Terraform has real destroy-safety automation baked in specifically because this stack gets destroyed and recreated often during development:

- Ingress/ALB release before VPC teardown
- `recovery_window_in_days = 0` on Secrets Manager entries (`var.secrets_recovery_window_days`, still overridable for a real account)

`make destroy` runs it with `-auto-approve` (plus the CloudTrail-bucket handling above); use the plain `terraform destroy` command if you want the interactive confirmation, but then handle the CloudTrail bucket manually as described above.

Since `argocd.tf`'s `kubectl_manifest` resources are now what created the ArgoCD `Application`/`ApplicationSet` objects, `terraform destroy` also deletes them — and both carry `resources-finalizer.argocd.argoproj.io`, so ArgoCD deletes everything it manages (all of `k8s/overlays/prod` and every `k8s/services/*/overlays/prod`) before the `Application` object itself actually goes away. This happens automatically, in the right order, before `eks-addons`/`eks` get torn down (Terraform destroys in reverse-dependency order).

## Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system-level view: module graph, region layout, secrets flow, GitOps deployment flow
- [`UML.md`](UML.md) — application-layer UML: component/class/ER/sequence diagrams
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — real incidents (OBS-NNN/TF-NNN), symptom/cause/fix
- [`compliance/`](compliance/) — SOC 2/ISO 27001/PCI DSS policy documents
- [`README.md`](../README.md) — tech stack, repo structure, local development, CI/CD overview
