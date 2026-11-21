# Change Management Standard Operating Procedure

Document owner: [Role: to be assigned]
Review cadence: annually, and whenever the CI/CD pipeline's approval gates change.

Authority: this SOP operates under [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) and formalizes controls already enforced mechanically by this repository's CI/CD pipeline and GitHub configuration. See [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) Section 5 for containment actions taken during an incident, which may include the emergency-change path described in Section 3 below.

## 1. Purpose and Scope

This document describes how changes to the Bookstore platform — application code, Terraform infrastructure, Kubernetes manifests, and CI/CD pipeline configuration — are proposed, reviewed, tested, and deployed. It satisfies SOC 2 CC8.1 (change management) and ISO/IEC 27001:2022 A.8.32 (change management). Most of what this document formalizes is not a new process being introduced — it is a written description of controls that already run mechanically on every push to this repository, cited by the specific file and job that enforces each one.

## 2. Standard Change Process

### 2.1 What already enforces this mechanically

| Control | Enforced by | What it prevents |
|---|---|---|
| Mandatory code review | `.github/CODEOWNERS` (repository-wide default rule plus explicit callouts for `*.tf`, `modules/`, `.github/`, `k8s/`, `iam.tf`) | An unreviewed change to any part of the codebase, with the highest-blast-radius paths called out explicitly |
| Secret scanning before any other check | `secret-scan` job (Gitleaks), `.github/workflows/ci-cd.yml` — every other job `needs: secret-scan` | A committed credential ever reaching a later pipeline stage |
| Automated tests + coverage + dependency audit | `test` job (Vitest per service, `npm audit --omit=dev --audit-level=high`, SonarCloud quality gate) | A regression or a known-vulnerable dependency merging silently |
| Lambda tests | `test-lambdas` job (pytest + moto, matrix over `rca-lambda`/`dashboard-read-lambda`) | A regression in the AIOps RCA pipeline's Python code |
| Kubernetes manifest validation | `validate` job (kubeconform against the live cluster's Kubernetes 1.31 schema) | A structurally invalid manifest ever reaching ArgoCD |
| Container vulnerability scanning | Trivy in the `build-and-push` job — CRITICAL/HIGH unfixed CVEs hard-fail the build, per-service SARIF uploaded to the GitHub Security tab | A vulnerable image ever being pushed to ECR |
| Image provenance | cosign keyless signing (GitHub OIDC) immediately after every `docker push` step, six times per pipeline run — one per service plus the frontend | An image running in production that cannot be proven to have come from this CI pipeline |
| **Production deployment gate** | `deploy` job, `.github/workflows/ci-cd.yml`, `environment: production` — requires a human reviewer to approve in GitHub before the job runs | An unreviewed change reaching the live cluster, even after passing every automated check above |

The `deploy` job only ever edits image tags in `k8s/overlays/prod/kustomization.yaml` and each service's own overlay, then commits and pushes — it never runs `kubectl` directly. ArgoCD, polling the repository every three minutes, performs the actual cluster reconciliation (auto-prune, self-heal). This means the `production` environment approval gate is the last human checkpoint before a change reaches the live cluster, and it is a GitHub-native control (an "Environment" with required reviewers), not something expressed in Terraform or application code.

### 2.2 Standard change checklist

A standard change is any change that goes through the process above in full:

1. Open a pull request against the target branch (see [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for scoping guidance — one change per PR).
2. Pipeline runs automatically: secret scan → test/audit/validate → build/scan/push (on `main`/`improvements`/`observability` only) → cosign signing.
3. At least one code owner approves per `.github/CODEOWNERS` (subject to the branch-protection caveat in Section 2.3 below).
4. On merge to `main` (or `observability`), the `deploy` job queues behind the `production` environment's required-reviewer gate.
5. A reviewer approves the deployment in GitHub's Environments UI.
6. `deploy` commits the new image tags; ArgoCD picks up the change within three minutes and reconciles the cluster.
7. The change author or reviewer confirms the rollout succeeded (`kubectl get applications -n argocd`, pod status, and the relevant service's `/health` endpoint — see [`../DEPLOYMENT.md`](../DEPLOYMENT.md#step-8--watch-all-apps-come-up)).

### 2.3 Open dependency: branch protection

CODEOWNERS review is only mandatorily enforced if the target branch's GitHub branch-protection settings have "require review from Code Owners" turned on. That setting lives in GitHub repository configuration, not in this repository's content, so it cannot be verified by reading the codebase — it must be confirmed directly against GitHub (`gh api repos/<owner>/<repo>/branches/<branch>/protection` or the Settings UI) each audit cycle, and any change to it requires the repository owner's explicit sign-off. [Action: capture current branch-protection configuration as a dated evidence artifact; see [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 3.3 for the same open item.]

### 2.4 Automated dependency updates

Dependabot (`.github/dependabot.yml`, added this session) opens weekly pull requests across every ecosystem this project depends on: npm (six directories — `client` and the five services), pip (both Lambdas), Terraform, Docker base images (six Dockerfiles), and GitHub Actions itself. Every Dependabot PR goes through the identical standard change process above — it is not auto-merged, and it does not bypass the `production` environment gate. GitHub Actions in this repository are pinned by commit SHA rather than a floating tag (see `.github/workflows/ci-cd.yml`'s own convention, e.g. `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4`); Dependabot tracks and bumps SHA-pinned actions correctly and keeps the trailing version comment in sync.

## 3. Emergency Change Process

An emergency change is warranted only for an active Sev-1 incident (see [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) Section 3) where following the full standard process in Section 2 would materially extend customer or data-security impact — for example, rotating a leaked credential, or a hotfix to stop active data loss.

### 3.1 What may be shortcut, and what may not

- **May be shortcut:** the wait for full CI (tests/audit/coverage) if the change is narrowly scoped and the IC judges the risk of skipping it lower than the risk of continued impact. `python3 scripts/build_and_push.py <tag>` (manual image build/push, see [`../README.md`](../README.md#building-and-pushing-docker-images)) exists specifically for this kind of hotfix/pre-release scenario.
- **May not be shortcut:** the secret scan (Gitleaks) — never push a change that has not been scanned, emergency or not, since introducing a new leaked credential during an incident compounds the incident. The `production` environment approval gate should still be honored if at all possible; if the IC deploys without it (e.g. via a manual `kubectl`/image-push path that bypasses `deploy`), that deviation itself must be logged as part of the retroactive documentation in Section 3.2.

### 3.2 Retroactive documentation

Every emergency change must be documented within [Timeframe: to be confirmed — e.g. 2 business days] of the incident's resolution, containing at minimum:

```
## Emergency change: <short title>
Date/time:
Authorized by: (IC or delegate — see INCIDENT_RESPONSE_PLAN.md Section 2)
What was changed, and in which file(s)/resource(s):
Why the standard process (Section 2) was not followed in full:
Which standard-process steps were skipped (be specific — e.g. "deployed via
  manual `python3 scripts/build_and_push.py` and a manual kustomize edit,
  bypassing the `production` environment reviewer gate"):
Risk accepted, and by whom:
Follow-up PR bringing the change back through the standard process
  (required even after the fact, so the change is reviewed and the repository
  state matches what actually shipped):
```

The follow-up PR requirement exists because an emergency change made outside the normal `deploy` job flow leaves `k8s/overlays/prod/kustomization.yaml` (and the corresponding git history) out of sync with what ArgoCD is actually running — the follow-up PR is what brings the two back into agreement and gives the change the review it skipped.

## 4. Change Categories Summary

| Category | Approval required | Example |
|---|---|---|
| Standard | Code owner review + automated pipeline + `production` environment reviewer | A new feature, a dependency bump, a Terraform module change |
| Emergency | IC authorization at the time; full retroactive documentation + follow-up PR within [Timeframe: to be confirmed] | Rotating a leaked credential mid-incident, a hotfix stopping active data loss |
| Automated/routine | Dependabot PR, still goes through the full standard process — no separate category in practice | A weekly npm/pip/Terraform/Docker/Actions update PR |

## 5. Related Documents

- [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) — the umbrella policy this SOP operates under
- [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) — defines the Incident Commander role referenced in Section 3
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — day-to-day PR scoping guidance for contributors
- [`../README.md`](../README.md#infrastructure-deploy-and-cicd) — pipeline stage overview
