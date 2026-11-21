# Business Continuity and Disaster Recovery Plan

Document owner: [Role: to be assigned]
Review cadence: annually, and after any DR-relevant infrastructure change or actual failover event.

Authority: this plan operates under [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md). It is the recovery half of [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) — that plan covers detection and containment; this one covers what happens once a decision is made to restore service in or from a degraded region.

## 1. Purpose and Scope

This plan describes the current disaster-recovery posture of the Bookstore platform (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md#region-layout)), states honestly what is and is not in place today, and proposes recovery targets pending formal organizational sign-off. It addresses SOC 2 CC7.5/A1 (availability and recovery), ISO/IEC 27001:2022 A.5.29/A.5.30 (business continuity), and, to the extent PCI DSS v4.0 applies, Req 12.10.1's business-continuity planning expectation — noting again that this system has no Cardholder Data Environment (see [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 5), so no PCI-specific CDE-recovery obligation exists.

## 2. Current DR Posture — Stated Honestly

**This system's disaster recovery today is backup-replication only. There is no standby compute in the secondary region, and no live restore test has ever been performed.** Specifically:

| Capability | Status | Source |
|---|---|---|
| Primary region | us-west-1 — all live workloads (EKS, RDS, monitoring EC2, all traffic) | [`../ARCHITECTURE.md`](../ARCHITECTURE.md#region-layout) |
| Secondary region | us-west-2 — backup/replication targets only, **no EKS cluster** | [`../ARCHITECTURE.md`](../ARCHITECTURE.md#region-layout) |
| Cross-region RDS backup replication | Coded (`terraform/dr.tf`, `aws_db_instance_automated_backups_replication`), gated on `var.dr_kms_key_id` being set (empty by default — off) | `terraform/dr.tf` |
| Cross-region ECR image replication | Coded, gated on `var.enable_dr_replication` (default `false` — previously defaulted on unintentionally; fixed under TROUBLESHOOTING OBS-029/OBS-049) | `terraform/main.tf`, `terraform/variables.tf` |
| Route53 failover record | Coded (active-passive), but with no secondary ALB to fail over to until a secondary-region EKS cluster exists | [`../ARCHITECTURE.md`](../ARCHITECTURE.md#region-layout) |
| Secondary-region EKS cluster / standby compute | **Does not exist.** If us-west-1 is unavailable, there is currently no compute to fail traffic over to. | [`../ARCHITECTURE.md`](../ARCHITECTURE.md#region-layout) |
| Live restore test | **Has not been performed.** No AWS credentials were available in the session that authored this document, so nothing in this plan has been exercised against real infrastructure. | This document's own authoring constraint |
| Single NAT gateway (regional SPOF) | Not an issue — `single_nat_gateway` defaults to `false` in `terraform/modules/network`, meaning one NAT gateway per AZ by default, confirmed safe without any change needed this session | [`../ARCHITECTURE.md`](../ARCHITECTURE.md#subnet-layout) |

This is stated plainly because a BCP/DR document that describes DR replication code as "backups are handled" without noting that no compute exists to restore *to*, and that the restore procedure has never been rehearsed, would materially overstate this system's actual recovery capability to an auditor. Everything DR-related in this section is **coded and `terraform validate`'d, not yet applied to a live account or tested end-to-end** — the same honesty constraint applies here as in [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 4.6.

## 3. Proposed Recovery Targets — Pending Sign-Off

No Recovery Time Objective (RTO) or Recovery Point Objective (RPO) has been formally adopted by the organization for this system. The figures below are **proposed starting points for discussion**, based on what the current architecture could plausibly support once the gaps in Section 2 are closed — they are not commitments, and should not be presented to a customer or auditor as agreed targets until [Role: to be assigned] formally signs off on them.

| Target | Proposed value | Basis | Status |
|---|---|---|---|
| RPO (data loss tolerance) | [RPO: proposed 24 hours, pending sign-off] | RDS automated backup retention is 7 days (`backup_retention_period` default in `terraform/modules/rds/variables.tf`) with a daily backup window (`03:00-04:00`); cross-region replication of those backups is coded but off by default | Proposed |
| RTO (time to restore service) | [RTO: proposed 4-8 hours, pending sign-off] | Estimated from the manual RDS-restore procedure in Section 4 below plus standing up compute from Terraform in a region with no live cluster today — this has never been timed against a real restore | Proposed |
| Standby compute in secondary region | [Decision: pending — build a secondary-region EKS cluster, or accept a longer RTO based on standing one up from Terraform during an actual incident] | Not built today | Open |

**Recommendation:** until the organization formally adopts RTO/RPO targets, treat this system's actual recovery capability as "best-effort, untested, likely measured in many hours, not minutes."

## 4. RDS Restore Procedure (Manual, As of Today)

This is the actual, current, step-by-step procedure a human would follow today to restore the database from a snapshot. It has **not been tested end-to-end** — every step is derived from the documented behavior of the AWS CLI commands involved and this project's own Terraform (`terraform/modules/rds/`), not from a rehearsed drill. Before relying on this procedure in a real incident, run it once against a non-production snapshot to confirm timing and catch any environment-specific surprise.

### 4.1 Identify the restore point

```bash
aws rds describe-db-snapshots \
  --db-instance-identifier <db_identifier from terraform.tfvars> \
  --query 'DBSnapshots[].[DBSnapshotIdentifier,SnapshotCreateTime]' \
  --output table
```

Automated backups give point-in-time recovery within the `backup_retention_period` window (7 days by default); for a specific point in time rather than a discrete snapshot, use `restore-db-instance-to-point-in-time` instead of `restore-db-instance-from-db-snapshot` (Section 4.2).

### 4.2 Restore to a new instance

RDS cannot restore in place — a snapshot restore always creates a **new** DB instance:

```bash
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier <db_identifier>-restored \
  --db-snapshot-identifier <snapshot-id-from-4.1> \
  --db-subnet-group-name rds-subnet-group \
  --vpc-security-group-ids <db_security_group_id from terraform state/outputs> \
  --no-publicly-accessible
```

Or, for point-in-time recovery instead of a discrete snapshot:

```bash
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier <db_identifier> \
  --target-db-instance-identifier <db_identifier>-restored \
  --restore-time <ISO-8601 timestamp> \
  --db-subnet-group-name rds-subnet-group \
  --vpc-security-group-ids <db_security_group_id> \
  --no-publicly-accessible
```

### 4.3 Wait for the restored instance to become available

```bash
aws rds wait db-instance-available --db-instance-identifier <db_identifier>-restored
aws rds describe-db-instances --db-instance-identifier <db_identifier>-restored \
  --query 'DBInstances[0].Endpoint.Address' --output text
```

**Use `.Address`, not the combined `.Endpoint` value** — see [`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md#obs-017--rds-address-vs-endpoint-and-backticks-in-an-unquoted-heredoc) (OBS-017): every consumer of the DB hostname in this codebase (the `mysql2` driver, the `mysql` CLI, the private Route53 CNAME) expects a bare hostname, and this exact bug has broken every DB connection in this project's history until fixed. The same care applies to a manually restored instance.

### 4.4 Reconnect the application

1. Update `/bookstore/db-credentials` in Secrets Manager with the restored instance's new endpoint (the `DB_HOST` field — see `terraform/modules/rds/main.tf`'s `aws_secretsmanager_secret_version.db_credentials`):
   ```bash
   aws secretsmanager put-secret-value --secret-id /bookstore/db-credentials \
     --secret-string '{"DB_USERNAME":"...","DB_PASSWORD":"...","DB_HOST":"<restored-instance-address>"}'
   ```
2. Force every microservice to pick up the new secret value: `kubectl rollout restart deployment -n catalog -n user -n order -n notification` (each namespace separately, or scripted across all five).
3. Verify each service's schema is intact and its own DB user still resolves — if the restore predates a schema-init hook's most recent run, the `<service>-schema-init` PreSync hook Job may need to be re-triggered (delete the existing Job so ArgoCD's `BeforeHookCreation` policy re-runs it on the next sync — see [`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md#obs-015--failed-presync-hook-jobs-never-got-cleaned-up), OBS-015).
4. Confirm connectivity end-to-end with the same health checks used post-deploy in [`../DEPLOYMENT.md`](../DEPLOYMENT.md#step-8--watch-all-apps-come-up).
5. Once confirmed stable, decommission the old instance (if it still exists) and rename the restored instance to the canonical identifier, or update `terraform.tfvars`/Terraform state to adopt the new instance going forward — the exact approach depends on whether the incident was a full instance loss (nothing to decommission) or a targeted restore alongside a still-running original (e.g. recovering from an application-level data-corruption event).

### 4.5 What this procedure does not cover

- Cross-region restore (restoring into us-west-2 from a replicated backup) is not detailed here because cross-region backup replication is off by default (`var.dr_kms_key_id` empty) and, even when on, there is no secondary-region EKS cluster to reconnect the restored database to (Section 2). [Action: write a cross-region variant of this procedure once a secondary-region cluster exists.]
- This procedure restores RDS only. It does not cover restoring DynamoDB (`bookstore-rca-reports` — see [`DATA_CLASSIFICATION_RETENTION_POLICY.md`](DATA_CLASSIFICATION_RETENTION_POLICY.md)) or any other stateful component. DynamoDB point-in-time recovery is not currently enabled on that table; [Action: evaluate whether it should be, given the table's TTL-based retention design].

## 5. Full-Region Failure Scenario

If us-west-1 becomes unavailable and no secondary-region compute exists (today's actual state), recovery consists of:

1. Provision a new EKS cluster and its dependent modules in a working region via `terraform apply` against a fresh or repointed backend — effectively re-running most of [`../DEPLOYMENT.md`](../DEPLOYMENT.md)'s stand-up procedure.
2. Restore RDS per Section 4 above, from the most recent available snapshot/backup (cross-region replica if `enable_dr_replication`/`dr_kms_key_id` were on before the outage; otherwise, whatever automated backups already existed in the failed region become inaccessible until that region recovers).
3. Repoint DNS (Route53) once the new ALB is live.

This is a multi-hour, manual-heavy process today, consistent with the "backup-only, no active-active" characterization in Section 2. [Decision: whether to invest in standby compute to shorten this — pending organizational sign-off per Section 3.]

## 6. Testing

No DR test (tabletop or live) has been performed for this system as of this document's authoring. [Action: schedule a tabletop walkthrough of Section 4's restore procedure against a non-production snapshot; cadence to be decided — e.g. annually — once scheduled, record the date and outcome here.]
