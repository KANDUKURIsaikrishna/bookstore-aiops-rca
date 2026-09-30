# Troubleshooting

Real incidents from this project's build-out, each assigned an ID (`OBS-NNN` for observability/app/Kubernetes issues, `TF-NNN` for Terraform-graph/provisioning-mechanics issues). Comments throughout the codebase reference these IDs (`see docs/TROUBLESHOOTING.md OBS-030`, or just `OBS-030`) instead of re-explaining the incident inline — this doc is where that explanation actually lives. Each entry: what broke, why, and what the code now does about it (the current code IS the fix).

IDs are **not sequential or complete**. Gaps in the numbering (e.g. no OBS-001/002/004-007) are real — those numbers were either issues resolved without leaving a durable code comment, or belong to a private issue tracker this doc doesn't otherwise mirror. Don't read a gap as a missing writeup; there's nothing to reconstruct there.

## Contents

**OBS**
- [OBS-003 — schema-init heredoc must stay unquoted for password expansion](#obs-003--schema-init-heredoc-must-stay-unquoted-for-password-expansion)
- [OBS-008 — Route53 alias records can't gate on an unknown-at-plan-time value](#obs-008--route53-alias-records-cant-gate-on-an-unknown-at-plan-time-value)
- [OBS-012 — unknown CRD type fails the entire ArgoCD sync batch](#obs-012--unknown-crd-type-fails-the-entire-argocd-sync-batch)
- [OBS-013 — ExternalSecret sync-wave ordering vs. the schema-init Job](#obs-013--externalsecret-sync-wave-ordering-vs-the-schema-init-job)
- [OBS-015 — failed PreSync hook Jobs never got cleaned up](#obs-015--failed-presync-hook-jobs-never-got-cleaned-up)
- [OBS-017 — RDS `.address` vs `.endpoint`, and backticks in an unquoted heredoc](#obs-017--rds-address-vs-endpoint-and-backticks-in-an-unquoted-heredoc)
- [OBS-025 — the app-serving hostnames had no Route53 records at all](#obs-025--the-app-serving-hostnames-had-no-route53-records-at-all)
- [OBS-027 — monitoring EC2 had no SSH access, at all, ever](#obs-027--monitoring-ec2-had-no-ssh-access-at-all-ever)
- [OBS-029 — 6 orphaned ECR repos in us-west-2 post-destroy](#obs-029--6-orphaned-ecr-repos-in-us-west-2-post-destroy)
- [OBS-030 — ExternalSecrets IRSA + ClusterSecretStore PreSync-hook ordering](#obs-030--externalsecrets-irsa--clustersecretstore-presync-hook-ordering)
- [OBS-032 — hardcoded EKS API server IP went stale on cluster recreate](#obs-032--hardcoded-eks-api-server-ip-went-stale-on-cluster-recreate)
- [OBS-033 — monitoring EC2 container tooling: apt repo and kube dir permissions](#obs-033--monitoring-ec2-container-tooling-apt-repo-and-kube-dir-permissions)
- [OBS-034 — kube-state-metrics couldn't reach the EKS API server at all](#obs-034--kube-state-metrics-couldnt-reach-the-eks-api-server-at-all)
- [OBS-040 — Grafana dashboard import blew the exec arg-length limit](#obs-040--grafana-dashboard-import-blew-the-exec-arg-length-limit)
- [OBS-041 — Docker's own apt repo doesn't add the user to the `docker` group](#obs-041--dockers-own-apt-repo-doesnt-add-the-user-to-the-docker-group)
- [OBS-042 — kube-state-metrics never picked up a refreshed token](#obs-042--kube-state-metrics-never-picked-up-a-refreshed-token)
- [OBS-044 — no real per-pod CPU/memory usage, only requests/limits/status](#obs-044--no-real-per-pod-cpumemory-usage-only-requestslimitsstatus)
- [OBS-045 — app-level `/metrics` unreachable from outside the cluster network](#obs-045--app-level-metrics-unreachable-from-outside-the-cluster-network)
- [OBS-049 — RDS SG blast radius, NetworkPolicy egress port, PDB shape, DR auto-on](#obs-049--rds-sg-blast-radius-networkpolicy-egress-port-pdb-shape-dr-auto-on)
- [OBS-050 — Grafana/Prometheus/Alertmanager secret fetch raced IAM propagation](#obs-050--grafanaprometheusalertmanager-secret-fetch-raced-iam-propagation)
- [OBS-056/057 — wrong load balancer type broke DNS resolution, twice](#obs-056057--wrong-load-balancer-type-broke-dns-resolution-twice)
- [OBS-058 — ArgoCD AppProject missing on a from-scratch rebuild](#obs-058--argocd-appproject-missing-on-a-from-scratch-rebuild)
- [OBS-059 — ACM wildcard cert didn't cover the two-label-deep API host](#obs-059--acm-wildcard-cert-didnt-cover-the-two-label-deep-api-host)
- [OBS-063 — Prometheus and Alertmanager shipped with zero authentication](#obs-063--prometheus-and-alertmanager-shipped-with-zero-authentication)
- [OBS-071 — root-owned basic-auth password file silenced every alert](#obs-071--root-owned-basic-auth-password-file-silenced-every-alert)
- [OBS-072 — literal `${this}` in an output description parsed as HCL interpolation](#obs-072--literal-this-in-an-output-description-parsed-as-hcl-interpolation)
- [OBS-073 — Lambda test suites: pytest import path and moto region mismatch](#obs-073--lambda-test-suites-pytest-import-path-and-moto-region-mismatch)
- [OBS-074 — `terraform destroy` can never fully delete the CloudTrail bucket](#obs-074--terraform-destroy-can-never-fully-delete-the-cloudtrail-bucket)

**TF**
- [TF-001 — concurrent Helm installs on a single-node cluster](#tf-001--concurrent-helm-installs-on-a-single-node-cluster)
- [TF-006 — depends_on serialization as the fix for TF-001](#tf-006--depends_on-serialization-as-the-fix-for-tf-001)
- [TF-012 — Secrets Manager's default recovery window blocks destroy/recreate cycles](#tf-012--secrets-managers-default-recovery-window-blocks-destroyrecreate-cycles)
- [TF-013 — `bootstrap_cluster_creator_admin_permissions` doesn't survive re-apply](#tf-013--bootstrap_cluster_creator_admin_permissions-doesnt-survive-re-apply)
- [TF-014 — node group resized from 1 to 3 once all services actually deployed](#tf-014--node-group-resized-from-1-to-3-once-all-services-actually-deployed)

---

## OBS entries

### OBS-003 — schema-init heredoc must stay unquoted for password expansion

**Symptom:** would fail with `mysql: unknown user or password` (or an outright empty `CREATE USER ... IDENTIFIED BY ''`) if the shell heredoc feeding the SQL into `mysql` were quoted (`<<'SQL'`) — a quoted heredoc suppresses all shell expansion, so `$CATALOG_DB_PASSWORD` would be passed to MySQL as the literal 21-character string `$CATALOG_DB_PASSWORD` instead of the actual secret value.

**Root cause:** the schema-init Job needs `$CATALOG_DB_PASSWORD` (and `$ADMIN_DB_*`) to expand inside the inline SQL, which only unquoted heredocs (`<<SQL`) do.

**Fix:** `k8s/services/catalog-service/base/schema-init-job.yaml:66-78` keeps the heredoc unquoted specifically so the env-var substitution works — see OBS-017 immediately below for the sharp edge that decision then created (bare backticks in an unquoted heredoc are shell command substitution, not literal characters).

### OBS-008 — Route53 alias records can't gate on an unknown-at-plan-time value

**Symptom:** would fail `terraform plan` with `Invalid count argument: ... value depends on resource attributes that cannot be determined until apply` if `count` on `aws_route53_record.primary` were written as `var.primary_alb_dns != "" ? 1 : 0`.

**Root cause:** `local.primary_alb_dns` is sourced from `argocd.tf`'s `data.kubernetes_ingress_v1` read (gated on `module.eks_addons`), so its value is unknown until apply, not plan — Terraform can't evaluate a `count` expression against an unknown value.

**Fix:** `terraform/modules/route53/main.tf:80-90` gates `count` on `var.enable_cloudfront` alone (a plain bool, always known at plan time) instead. The upstream `null_resource.wait_for_alb_hostname` in `argocd.tf` already hard-fails the apply if the ALB hostname never shows up, so by the time this resource actually applies, `primary_alb_dns` is guaranteed non-empty. Same reasoning is reused for `aws_route53_record.frontend`/`.api` (`terraform/modules/route53/main.tf:121-143`).

### OBS-012 — unknown CRD type fails the entire ArgoCD sync batch

**Symptom:** `ComparisonError`/`SyncFailed` on the whole `bookstore` Application — not just the offending resource — the moment a manifest of an unregistered CRD type (`monitoring.coreos.com/v1` `ServiceMonitor`/`PrometheusRule`) was included in a sync.

**Root cause:** `k8s/base/monitoring/` (`servicemonitor.yaml` + `prometheus-rules.yaml`) assumed the Prometheus Operator, which this cluster never runs — monitoring lives on the standalone `monitoring-ec2` instance instead (static `file_sd_configs`, not `ServiceMonitor` discovery; see `docs/ARCHITECTURE.md`'s "Why monitoring runs on EC2" section). ArgoCD doesn't skip a resource of an unknown kind — it fails the entire sync batch for that Application.

**Fix:** `k8s/base/monitoring/` was deleted outright (commit `cdbd5cc`, 2026-08-29) and never re-added to `k8s/base/kustomization.yaml`'s resource list. The comment at `k8s/base/kustomization.yaml:21` and the equivalent note in `docs/ARCHITECTURE.md:84` both exist specifically to stop this from being reintroduced.

### OBS-013 — ExternalSecret sync-wave ordering vs. the schema-init Job

**Symptom:** would fail with `CreateContainerConfigError: secret "catalog-db-secret" not found`, retried for 15 minutes, never self-resolved.

**Root cause:** the per-service `ExternalSecret` (`catalog-db-secret`) and the schema-init `Job` both live in `k8s/services/catalog-service/base/`, adjacent in the file list — but ArgoCD sync order is governed by `sync-wave`, not file position. Without an explicit wave on the `ExternalSecret`, the Job's pod could start before the K8s `Secret` ESO materializes from it even exists.

**Fix:** `k8s/services/catalog-service/base/external-secret.yaml:15` and `admin-db-secret.yaml:22` carry `argocd.argoproj.io/hook: PreSync`, `sync-wave: "-1"` — one wave before `schema-init-job.yaml`'s implicit wave `0`. All PreSync hooks run, in ascending wave order, strictly before any Sync-phase resource.

### OBS-015 — failed PreSync hook Jobs never got cleaned up

**Symptom:** would hang forever on any sync after a failed schema-init Job — every subsequent ArgoCD sync just keeps waiting on the same permanently-broken, un-retried Job, since a same-name hook resource already exists.

**Root cause:** Kubernetes `Job`s are immutable once created, and ArgoCD's `hook-delete-policy: HookSucceeded` only fires on success — a Job that *fails* is never cleaned up by that policy alone.

**Fix:** `k8s/services/catalog-service/base/schema-init-job.yaml:34` sets `hook-delete-policy: BeforeHookCreation,HookSucceeded` — `BeforeHookCreation` deletes the previous hook resource, success or failure, before creating the next one on every sync attempt. Matches the SQL's own idempotent design (`CREATE ... IF NOT EXISTS`, guarded seed-count check), so re-running it on every sync is safe.

### OBS-017 — RDS `.address` vs `.endpoint`, and backticks in an unquoted heredoc

This ID is used for two unrelated fixes referenced from different parts of the codebase — noted here honestly rather than forced into one narrative.

- **RDS hostname format.** *Symptom:* would fail with something like `mysql -h "host:3306"` → `ERROR 2005: Unknown MySQL server host` — DNS resolution chokes on the embedded colon. *Root cause:* `aws_db_instance.db.endpoint` returns `"host:port"` combined; every consumer of `DB_HOST` (the `mysql2` driver's `host:` param, the `mysql` CLI's `-h` flag, the private Route53 CNAME target) expects a bare hostname. Per the code's own comment, no DB connection in this project's history ever actually worked until this was fixed — it was never exercised end-to-end before. *Fix:* `terraform/modules/rds/main.tf:31` and `terraform/modules/rds/outputs.tf:25` both use `aws_db_instance.db.address` instead of `.endpoint`, fixed independently in each place since neither goes through the other.
- **Backticks in the schema-init heredoc.** *Symptom:* would silently strip the `` `desc` `` column identifier from every SQL statement that used it — an unquoted heredoc (needed for `$CATALOG_DB_PASSWORD` expansion, see OBS-003) also treats bare backticks as shell command substitution, so unescaped `` `desc` `` ran as the shell command `desc` three times instead of staying literal text. *Fix:* every backtick in `k8s/services/catalog-service/base/schema-init-job.yaml:84,105,112` is escaped as `` \` `` — a literal backtick to the shell, which MySQL still sees as a quoted identifier.

### OBS-025 — the app-serving hostnames had no Route53 records at all

**Symptom:** the site was never actually reachable by name — the ALB's default rule 404s on anything that doesn't match a configured Ingress host, and every Route53 record that existed only ever covered the bare domain apex.

**Root cause:** `k8s/base/ingress/ingress.yaml` and `k8s/services/api-gateway/base/ingress.yaml` route on `bookstore.<domain>` and `api.bookstore.<domain>` specifically, neither of which is the apex — but no Route53 record for either hostname existed in any hosted zone this project has used.

**Fix:** `terraform/modules/route53/main.tf:121-143` adds `aws_route53_record.frontend` (`bookstore.<domain>`) and `aws_route53_record.api` (`api.bookstore.<domain>`), both ALIAS records against the ALB, same pattern as the apex `primary` record.

### OBS-027 — monitoring EC2 had no SSH access, at all, ever

**Symptom:** `make monitoring-status`/`make monitoring-logs` (both plain `ssh ubuntu@...`) would hang to a full connection timeout, not an auth failure.

**Root cause:** two separate missing pieces, both fixed together — the security group (`terraform/modules/monitoring-ec2/main.tf:29-40`) never had a port-22 ingress rule at all, and the instance itself had no `key_name`, so even with a port open, Ubuntu's cloud-init had no EC2 key pair to seed `authorized_keys` from.

**Fix:** `terraform/modules/monitoring-ec2/main.tf:29-40` adds the SSH ingress rule (scoped to `var.admin_cidr_blocks`, same as the other admin UI ports); `terraform/modules/monitoring-ec2/main.tf:219-227` adds an auto-generated `tls_private_key`/`aws_key_pair`, consistent with this project's automate-everything posture elsewhere (ALB discovery, schema-init hooks). `make monitoring-key` fetches the private key from Terraform state.

### OBS-029 — 6 orphaned ECR repos in us-west-2 post-destroy

**Symptom:** 6 ECR repositories left behind in `us-west-2` after a `terraform destroy`, with no DR ever having been explicitly requested for that run.

**Root cause:** `module.ecr`'s `secondary_region` was passed `var.secondary_region` directly — which always had a non-empty default (`us-west-2`) — so every apply silently replicated every `bookstore-*` repo cross-region regardless of DR intent.

**Fix:** `terraform/main.tf:135` gates it on `var.enable_dr_replication ? var.secondary_region : ""` instead — an explicit off-by-default flag, not just "is a region string configured." Same pattern applied to `module.rds`'s `secondary_region` at `terraform/main.tf:43` (see OBS-049).

### OBS-030 — ExternalSecrets IRSA + ClusterSecretStore PreSync-hook ordering

Two related failures in the same secrets pipeline (`docs/ARCHITECTURE.md`'s "Secrets flow" section covers this in full):

- **Missing IRSA annotation.** *Symptom:* no `ExternalSecret` anywhere in the cluster — old or new — could actually pull from Secrets Manager. *Root cause:* the External Secrets Operator's Helm release created a ServiceAccount with no IRSA role and no annotation, even though the `ClusterSecretStore` already expected one named exactly `external-secrets-sa`. *Fix:* `terraform/modules/eks-addons/external-secrets.tf` adds the IRSA role + trust policy and has the Helm release name/annotate the ServiceAccount correctly.
- **PreSync hook ordering.** *Symptom:* would fail every `ExternalSecret`'s first reconcile with `could not get ClusterSecretStore ... not found`, deterministically, not as a race. *Root cause:* `k8s/base/secrets/external-secret.yaml:35` (`aws-secretsmanager` `ClusterSecretStore`) must exist before any `ExternalSecret`'s own PreSync hook runs; without the `argocd.argoproj.io/hook: PreSync`, `sync-wave: "-2"` annotation, it would be a plain Sync-phase resource, and ArgoCD always applies Sync-phase resources *after* every PreSync hook. *Fix:* the wave/hook annotation at `k8s/base/secrets/external-secret.yaml:35`, one wave ahead of every service's own `sync-wave: "-1"` `ExternalSecret`.

### OBS-032 — hardcoded EKS API server IP went stale on cluster recreate

**Symptom:** would fail Prometheus's `app-metrics`/`kubelet-cadvisor` scrape jobs after any cluster recreate — a hardcoded literal IP for the API server endpoint doesn't survive a new cluster getting a new endpoint.

**Root cause:** the EKS API server endpoint used by `kubernetes_sd_configs` for pod discovery was a literal, not templated.

**Fix:** `terraform/modules/monitoring-ec2/variables.tf:37` templates `eks_api_server` from `module.eks.cluster_endpoint` instead — a fresh endpoint is picked up automatically on every apply.

### OBS-033 — monitoring EC2 container tooling: apt repo and kube dir permissions

Two related container-tooling fixes on the monitoring EC2, from the same setup pass:

- **`docker-compose-plugin` isn't in Ubuntu's default repos.** *Fix:* `terraform/modules/monitoring-ec2/user-data.sh.tftpl:12-28` adds Docker's own apt repository (GPG key + `sources.list.d` entry) before `apt-get install docker-ce docker-compose-plugin` — Ubuntu's `docker.io` package doesn't ship the plugin.
- **kube-state-metrics config directory.** *Fix:* `terraform/modules/monitoring-ec2/user-data.sh.tftpl:118-123` writes the container's kubeconfig to `/opt/monitoring/kube`, not `/root/.kube` — `/root/.kube` is `0700`, which blocks the container's non-root user from reading it.

### OBS-034 — kube-state-metrics couldn't reach the EKS API server at all

**Symptom:** kube-state-metrics resolves the API server's private-endpoint IPs fine but every connection attempt times out at the security-group layer, crash-looping forever — confirmed on a cluster that had been through several destroy/recreate cycles with this apparently never having worked.

**Root cause:** no security group rule allowed the monitoring EC2 to reach the EKS cluster's own security group on 443.

**Fix:** `terraform/modules/monitoring-ec2/main.tf:108-116` (`aws_security_group_rule.monitoring_scrape_eks_api`) opens 443 from the monitoring SG to `var.eks_cluster_sg_id`.

### OBS-040 — Grafana dashboard import blew the exec arg-length limit

**Symptom:** would fail on large dashboard JSON (e.g. community dashboard #1860, ~683KB) if passed inline to `curl -d` — Linux's `exec` has a 128KB per-argument limit.

**Root cause:** `curl -d "$(cat file)"` expands the whole payload into a single shell argument.

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:682` uses `curl -d @file` instead, streaming the payload from disk rather than passing it as an argument.

### OBS-041 — Docker's own apt repo doesn't add the user to the `docker` group

**Symptom:** would fail every subsequent `docker`/`docker compose` command run as the `ubuntu` user with a permission-denied error against the Docker socket.

**Root cause:** unlike Ubuntu's default `docker.io` package, installing from Docker's own apt repository doesn't automatically add any user to the `docker` group.

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:32` runs `usermod -aG docker ubuntu` explicitly after install.

### OBS-042 — kube-state-metrics never picked up a refreshed token

**Symptom:** would silently start serving from a stale, expired bearer token indefinitely — the container loads its kubeconfig once at process start and never re-reads it, so a cron-refreshed token file on disk has no effect on the running container.

**Root cause:** `refresh-kube-token.sh` (run every 10 minutes via cron) rewrote the kubeconfig file but never told the already-running container to reload it.

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:120-122` restarts the `kube-state-metrics` container on every token refresh (`|| true` guards the very first cron run, which predates the container existing).

### OBS-044 — no real per-pod CPU/memory usage, only requests/limits/status

**Symptom:** every dashboard panel meant to show actual pod resource usage instead only ever showed configured requests/limits and pod phase — `kube-state-metrics` doesn't expose real usage at all.

**Root cause:** real per-pod CPU/memory usage lives in kubelet's cAdvisor endpoint (`/metrics/cadvisor`), which nothing was scraping.

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:260-268` adds the `kubelet-cadvisor` Prometheus scrape job (bearer-token auth, `file_sd_configs` target list refreshed every 5 min by `update-prom-targets.sh`), and `terraform/modules/monitoring-ec2/main.tf:124-132` opens the matching security-group rule (10250) from the monitoring EC2 to the EKS cluster SG.

### OBS-045 — app-level `/metrics` unreachable from outside the cluster network

**Symptom:** would fail to scrape any of the 5 microservices' own `http_requests_total`/etc. prom-client counters — pod IPs aren't routable from outside the cluster network, so a direct Prometheus target never resolves.

**Root cause:** the monitoring EC2 lives outside the VPC's pod network entirely; there's no route to a pod IP from there.

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:276-304` (`app-metrics` scrape job) uses `kubernetes_sd_configs` with `role: pod` and routes every scrape through the EKS API server's pod-proxy endpoint (`/api/v1/namespaces/.../pods/...:.../proxy/metrics`) instead of a direct pod IP — the same 443 route already open for `kube-state-metrics` (OBS-034), no new SG rule needed. `relabel_configs` keep only pods annotated `prometheus.io/scrape: "true"`.

### OBS-049 — RDS SG blast radius, NetworkPolicy egress port, PDB shape, DR auto-on

One troubleshooting session, several related fixes, all tracked under this single ID:

- **RDS security group opened to the whole VPC.** *Symptom:* RDS was reachable on 3306 from every subnet in the VPC — public subnets, RDS's own subnets, everything — despite the security group's own description claiming EKS-nodes-only. *Fix:* `terraform/modules/security/main.tf:8-16` scopes the ingress rule to `var.eks_node_cidr_blocks` — just the 4 EKS-node subnet CIDRs (`terraform/locals.tf:26-29`), not the whole `170.20.0.0/16`.
- **NetworkPolicy egress port mismatch.** *Symptom:* would silently block all `api-gateway → microservice` traffic the instant a NetworkPolicy-enforcing CNI was turned on. *Root cause:* an earlier version matched egress on port 80 (the K8s `Service` port) instead of 3000 (the pod's real `containerPort`) — NetworkPolicy egress matches the actual destination pod port *after* `kube-proxy`'s Service DNAT, not the Service's advertised port. *Fix:* `k8s/services/api-gateway/base/network-policy.yaml:43` matches port 3000.
- **PDB shape for `replicas: 1` services.** *Symptom:* `kubectl drain`, an EKS managed-node-group upgrade, or Cluster Autoscaler consolidation would hang indefinitely on any single-replica service using `minAvailable: 1` — equal to total replica count, so Kubernetes could never permit even a voluntary eviction. *Fix:* `k8s/services/catalog-service/base/pdb.yaml:14` (and the matching files for `notification-service`, `order-service`, `user-service`) use `maxUnavailable: 1` instead — still creates a real PDB object, but doesn't block routine single-node maintenance.
- **`enable_dr_replication` silently defaulting on.** *Symptom:* cross-region Secrets Manager and ECR replicas created on every apply regardless of DR intent, just because `secondary_region` had a non-empty default. *Fix:* `terraform/variables.tf:61` and its use at `terraform/main.tf:42-43` gate replication on an explicit, off-by-default `enable_dr_replication` flag instead of `secondary_region != ""` (see also OBS-029).

### OBS-050 — Grafana/Prometheus/Alertmanager secret fetch raced IAM propagation

**Symptom:** confirmed live — `AccessDeniedException` on `secretsmanager:GetSecretValue` immediately after boot, with the identical call succeeding seconds later and zero config changes in between. Under `set -e` this killed the rest of user-data (and so the entire monitoring stack — nothing after that line ever ran), with no automatic retry since user-data only runs once per instance.

**Root cause:** the monitoring EC2's IAM role policy is created in the *same* `apply` as the instance itself, and IAM changes aren't instantly consistent — the very first boot can hit the secret fetch before the policy has finished propagating.

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:52-65` (and the matching blocks for the basic-auth and SMTP secrets) retry the `aws secretsmanager get-secret-value` call up to 20 times, 6 seconds apart, before failing hard. The Loki-discovery equivalent of this same class of problem — nodes needing to look up the monitoring EC2's IP at boot without a circular module dependency between `module.eks` and `module.monitoring_ec2` — is handled the same way in `terraform/modules/eks/iam.tf:75-94` and `terraform/modules/eks/node-user-data.sh.tftpl`.

### OBS-056/057 — wrong load balancer type broke DNS resolution, twice

**Symptom:** DNS resolution to the ingress broke twice across this project's history, before ever reaching the AWS Load Balancer Controller.

**Root cause:** `data.aws_lb_hosted_zone_id.ingress_lb` (`terraform/modules/route53/main.tf:72-74`) has had to track three different load balancer types as the ingress layer evolved: (1) Classic ELB, ingress-nginx's accidental default; (2) NLB, coded via a Service annotation on ingress-nginx but never actually applied before ingress-nginx itself was replaced; (3) ALB, the AWS Load Balancer Controller's own type, and the one live today. Each transition required matching `load_balancer_type` to whatever was actually provisioned — using the wrong one of the three is exactly what broke DNS resolution before landing on the current, correct value.

**Fix:** `load_balancer_type = "application"` at `terraform/modules/route53/main.tf:73`, matching the AWS Load Balancer Controller's ALB. The comment at that call site records the full history specifically so a future change doesn't repeat the same mistake — check `aws elbv2 describe-load-balancers` against the real, live load balancer before ever touching this again.

### OBS-058 — ArgoCD AppProject missing on a from-scratch rebuild

**Symptom:** confirmed live — on a from-scratch `terraform destroy` + `apply` cycle, the `Application`/`ApplicationSet` objects came up referencing a project (`AppProject/bookstore`) that no longer existed.

**Root cause:** `DEPLOYMENT.md` used to instruct applying `k8s/argocd/appproject.yaml`, `application.yaml`, and `applicationset-microservices.yaml` by hand via `kubectl apply -f`. Only the latter two ever got wired into Terraform — `appproject.yaml` was genuinely missed, invisible on a long-lived cluster where the AppProject just sits there once created, and only surfaced as a real outage on the next full rebuild.

**Fix:** `terraform/argocd.tf` applies all three via `kubectl_manifest`, with `kubectl_manifest.argocd_appproject` explicit in the `depends_on` chain of the `Application`/`ApplicationSet` resources so the AppProject is guaranteed to exist first (ArgoCD itself rejects an Application naming a nonexistent project at admission). The same "don't let something whose identity/state churns on destroy+recreate be silently skipped or Terraform-owned" lesson is cited again at `terraform/modules/route53/main.tf:20-29` and `scripts/init_domain.py:1-12` for why the public Route53 zone is a `data` lookup, never a Terraform-managed `resource` — recreating it would hand out brand-new nameservers every cycle and break the domain registrar delegation.

### OBS-059 — ACM wildcard cert didn't cover the two-label-deep API host

**Symptom:** would fail with "no certificate found for host" — the AWS Load Balancer Controller couldn't find a matching issued ACM cert for `api-gateway`'s Ingress.

**Root cause:** `*.${var.domain}` covers exactly one label deep (`bookstore.<domain>`), not two (`api.bookstore.<domain>`).

**Fix:** `terraform/ingress-cert.tf:31` adds a second SAN, `*.bookstore.${var.domain}`, to the same certificate's `subject_alternative_names`.

### OBS-063 — Prometheus and Alertmanager shipped with zero authentication

**Symptom:** would leave both tools fully open to anyone in `monitoring_admin_cidr` with no login at all — able to run arbitrary PromQL against full cluster telemetry, or silence firing alerts via Alertmanager's API directly. Unlike Grafana, neither ships with built-in auth of its own.

**Root cause:** no credential existed for either service at all.

**Fix:** a shared basic-auth credential (`terraform/modules/eks-addons/monitoring-basic-auth-secret.tf`, `/bookstore/monitoring-basic-auth`) is fetched by `user-data.sh.tftpl`, bcrypt-hashed via `htpasswd` into each tool's `--web.config.file` (`terraform/modules/monitoring-ec2/user-data.sh.tftpl:204-219,306-312,476`), and also handed to Grafana's own datasource config in plaintext (`secureJsonData`) so its dashboard queries — which now go over this same auth, not the public internet — keep working. The background dashboard-import script re-reads the same password file independently (`user-data.sh.tftpl:669-672`), since it runs as its own process.

### OBS-071 — root-owned basic-auth password file silenced every alert

**Symptom:** the Prometheus self-scrape target sat permanently `DOWN`, and no alert of any kind ever reached Alertmanager — so no notification email was ever sent.

**Root cause:** the basic-auth password file Prometheus reads (for both its own self-scrape auth and the `Authorization` header on every alert POST to Alertmanager) was written `600`, root-owned — but Prometheus runs as non-root (`nobody`) inside its container, so every read hit "permission denied."

**Fix:** `terraform/modules/monitoring-ec2/user-data.sh.tftpl:210-219` writes the file `644` instead — the value is already sitting in Secrets Manager and needed to hit any monitoring URL, so host-readable is an acceptable tradeoff on this single-tenant private box.

### OBS-072 — literal `${this}` in an output description parsed as HCL interpolation

**Symptom:** `terraform init -backend=false` failed with an "Unsuitable value type" / "Variables not allowed" error — `this` isn't a valid identifier in scope at the point HCL tries to evaluate it.

**Root cause:** `terraform/modules/aiops-rca/outputs.tf`'s `dashboard_read_api_url` output had a description string containing the plain-English fragment `` the static dashboard's script.js calls ${this}/reports `` — meant as prose describing the dashboard's own JS convention, but HCL parses `${...}` inside any string as a real interpolation expression regardless of context, and there's no `this` variable/resource in scope to resolve it against.

**Fix:** `terraform/modules/aiops-rca/outputs.tf:32` escapes it as `$${this}` — a doubled `$` is HCL's own escape for a literal `${` sequence, so the description now renders as the intended plain text instead of being evaluated.

### OBS-073 — Lambda test suites: pytest import path and moto region mismatch

Two independent, sequential failures hit while getting `lambdas/rca-lambda/tests/test_lambda_function.py` and `lambdas/dashboard-read-lambda/tests/test_lambda_function.py` running locally:

- **Import path.** *Symptom:* running `pytest tests/test_lambda_function.py -v` directly from a lambda's own directory failed with `ModuleNotFoundError: No module named 'lambda_function'`. *Root cause:* pytest's default "prepend" import mode only adds the `tests/` directory itself to `sys.path`, not its parent (where `lambda_function.py` actually lives) — and this repo has no `conftest.py` to establish that path another way. *Fix:* invoke `python -m pytest tests/test_lambda_function.py -v` instead — `-m` adds the current working directory to `sys.path[0]`, which resolves the import. `.github/workflows/ci-cd.yml:178` (the `test-lambdas` job) was written using exactly this `python -m pytest` form from the start, so CI was never affected.
- **Region mismatch under moto.** *Symptom:* after fixing the import, tests failed on `PutItem` with `botocore.errorfactory.ResourceNotFoundException`. *Root cause:* the local environment's default AWS CLI region didn't match the region moto's `dynamodb_table` fixture explicitly creates its mocked table in (`us-west-1`) — both `lambda_function.py` modules call `boto3.resource("dynamodb")` at module level with no explicit region, so under moto's region-scoped mocking the client resolved against whatever region was ambient at import time, not the fixture's. *Fix:* `lambdas/rca-lambda/tests/test_lambda_function.py:9` and `lambdas/dashboard-read-lambda/tests/test_lambda_function.py:8` both set `os.environ["AWS_DEFAULT_REGION"] = "us-west-1"` as the first line of environment setup, before `lambda_function` is imported — so the module-level `boto3.resource` call resolves to the same region the fixture uses.

### OBS-074 — `terraform destroy` can never fully delete the CloudTrail bucket

**Symptom:** confirmed live 2026-09-17, first-ever full apply/destroy validation cycle against a real account — `terraform destroy` on the complete 181-resource stack tore down every single resource cleanly except one: `aws_s3_bucket.cloudtrail` failed with `api error BucketNotEmpty: The bucket you tried to delete is not empty. You must delete all versions in the bucket.` A subsequent `terraform plan`/`apply` would then also fail, trying to create a bucket that still exists (`BucketAlreadyOwnedByYou`), unless the bucket is first removed from state.

**Root cause:** not a bug — this is S3 Object Lock in COMPLIANCE mode (`terraform/cloudtrail.tf`, added for the SOC 2/ISO 27001/PCI DSS compliance-hardening pass) working exactly as designed. While any object in the bucket is still inside its retention window (`var.cloudtrail_retention_days`, default 400 days), **nothing** — not `force_destroy`, not the bucket owner, not the AWS account root — can delete it. CloudTrail starts writing to the bucket within seconds of the trail being created, so by the time anyone runs `terraform destroy`, the bucket already has at least one locked object and the whole-stack teardown this project's workflow depends on (see TF-012's same underlying "gets destroyed and recreated often" design constraint) can never fully complete while that log data exists.

**Fix:** two-layered, because the underlying tension (immutable audit trail vs. a stack that gets destroyed and recreated often) doesn't fully resolve either way:

- **`var.enable_cloudtrail_object_lock` now defaults to `false`** (`terraform/variables.tf`), and `aws_s3_bucket.cloudtrail` carries `force_destroy = true` (`terraform/cloudtrail.tf`) — for this project's normal dev-cycle workflow, the bucket destroys cleanly every time now, no orphan, no manual steps. This is the actual fix for "I don't want to see this every cycle."
- **If `enable_cloudtrail_object_lock` is turned on** (a real audit-scoped deployment, where the point is that logs can't be deleted), the original problem is back by design, and stays handled defensively: `Makefile`'s `destroy` target runs `terraform destroy -auto-approve`, then unconditionally `terraform state rm aws_s3_bucket.cloudtrail` (idempotent — safe whether or not the destroy actually reached that resource), so the surviving bucket can't block the next `plan`/`apply`. `Makefile`'s `import` target (which `make apply` runs automatically) re-adopts the same bucket on the next cycle. Anyone running `terraform destroy` directly instead of `make destroy` with the lock on needs to run the `state rm` step by hand afterward.

One irreversible side effect from discovering this live: the account's default bucket name, `bookstore-cloudtrail-<account_id>` (no suffix), got a real object locked into it during the very apply/destroy cycle that surfaced this bug, and is now permanently orphaned — nothing can ever delete that specific bucket before 2027-10-22. `terraform/cloudtrail.tf` now names the Terraform-managed bucket `bookstore-cloudtrail-<account_id>-v2` to avoid colliding with it forever. The orphaned original costs pennies/month and needs no action — its own lifecycle rule expires it once its lock clears.

---

## TF entries

### TF-001 — concurrent Helm installs on a single-node cluster

**Symptom:** would fail or time out under real resource contention when every Helm chart in `eks-addons` (External Secrets Operator, AWS Load Balancer Controller, ArgoCD, Argo Rollouts) installed concurrently on a cluster with too little node capacity to schedule them all at once.

**Root cause:** single-node resource contention — before `node_desired_size` was raised (see TF-014), the cluster didn't have headroom for every addon's pods to come up in parallel.

**Fix:** see TF-006 — an explicit `depends_on` chain serializing the installs was the fix at the time.

### TF-006 — depends_on serialization as the fix for TF-001

**Symptom/context:** same underlying resource-contention problem as TF-001.

**Fix (since removed):** `terraform/modules/eks-addons/gitops.tf:1-11` used to serialize `helm_release.argocd` after ingress-nginx (predating the AWS Load Balancer Controller migration) and `helm_release.argo_rollouts` after `argocd`, via explicit `depends_on`, purely to avoid every chart fighting for scheduling slots on one small node. Once `node_desired_size` went to 2 (and later 3, see TF-014), this serialization was removed — every chart in `eks-addons` now installs concurrently, which shortened apply time. The comment at `gitops.tf:1-11` is explicit that if a real apply on this node size starts timing out again with TF-001-shaped failures, re-adding the `depends_on` chain is the fix to reach for — not raising node count indefinitely.

### TF-012 — Secrets Manager's default recovery window blocks destroy/recreate cycles

**Symptom:** would fail a subsequent `terraform apply` trying to recreate a Secrets Manager secret (e.g. `/bookstore/db-credentials`, `/bookstore/grafana-admin`, `/bookstore/monitoring-basic-auth`) with a name-already-in-use-style error — AWS Secrets Manager's default behavior schedules a deleted secret for a 30-day recovery window rather than deleting it immediately, and refuses to create a new secret under a name still inside that window. This is inferred from AWS's documented Secrets Manager deletion behavior plus the code's own comments and `docs/DEPLOYMENT.md`'s framing, not from a captured error string.

**Root cause:** this project's Terraform gets destroyed and recreated often during development (`docs/DEPLOYMENT.md`'s "Tearing it down" section calls this out explicitly as a deliberate design constraint the destroy-safety automation is built around) — the default 30-day recovery window would block almost every subsequent apply.

**Fix:** every `aws_secretsmanager_secret` resource in this project sets `recovery_window_in_days = 0` — `terraform/modules/rds/main.tf:9`, `terraform/modules/eks-addons/grafana-secret.tf:8`, `terraform/modules/eks-addons/monitoring-basic-auth-secret.tf:19`, and the per-service credentials/JWT/SMTP secrets in `terraform/main.tf:72,98,248` — forcing an immediate, unrecoverable delete on destroy instead of a soft-delete window.

### TF-013 — `bootstrap_cluster_creator_admin_permissions` doesn't survive re-apply

**Symptom:** would leave a later `terraform apply` (run by a different principal, or after a module refactor that state-moves the cluster resource) with no cluster-admin access, despite EKS's own `bootstrap_cluster_creator_admin_permissions` (default `true`) suggesting otherwise.

**Root cause:** that setting only fires once, at the literal `CreateCluster` API call — it doesn't retroactively grant access to whoever runs `terraform apply` later, and doesn't survive a state move that doesn't recreate the cluster.

**Fix:** `terraform/modules/eks/main.tf:66-84` (`aws_eks_access_entry`/`aws_eks_access_policy_association`, both `for_each` over `var.admin_principal_arns`) are the persistent, re-appliable equivalent. `terraform/main.tf:197-200` always includes `data.aws_caller_identity.current.arn` in that list, so whoever is currently running Terraform always has cluster-admin, regardless of who created the cluster originally.

### TF-014 — node group resized from 1 to 3 once all services actually deployed

**Symptom:** pods stuck `Pending` (ENI IP exhaustion, not CPU/memory pressure) once every workload was actually live.

**Root cause:** a `t3.medium` node caps at 17 pods due to the ENI IP limit, not compute — 2 nodes (34 slots) filled up once all 5 microservices plus `api-gateway`'s 2 replicas joined the cluster (this only started actually happening once OBS-030's ExternalSecrets IRSA fix let every service's schema-init Job and Deployment succeed — before that fix, most services never got far enough to consume pod slots).

**Fix:** `terraform/main.tf:189-192` sets `node_desired_size = 3` (module default is lower, overridden at the call site). `terraform/modules/eks/main.tf:172-185` currently carries `lifecycle { ignore_changes = [launch_template[0].version] }` — a pending Fluent Bit/Loki launch-template rollout (OBS-050's fix) is paused behind an EC2 vCPU quota increase (8→16 requested, not yet cleared) rather than shrinking node count into an already-tight pod-capacity margin.
