# Data Classification and Retention Policy

Document owner: [Role: to be assigned]
Review cadence: annually, and whenever a new data store or retention mechanism is added to the system.

Authority: this policy operates under [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md). See [`VENDOR_RISK_MANAGEMENT_POLICY.md`](VENDOR_RISK_MANAGEMENT_POLICY.md) for the third party (Anthropic) that the RCA-report data class below is routed through, and [`INCIDENT_RESPONSE_PLAN.md`](INCIDENT_RESPONSE_PLAN.md) for handling a breach of any class listed here.

## 1. Purpose and Scope

This policy classifies every category of data this system actually stores, states the real retention mechanism for each (citing the specific Terraform resource or application code that implements it), and identifies gaps — most notably, whether a user can request deletion of their own data today. It satisfies SOC 2 CC6.5/CC6.7 (data classification and disposal) and ISO/IEC 27001:2022 A.5.12/A.8.10 (information classification, deletion). This document describes what exists in the codebase as of this writing; it does not describe an aspirational data model.

## 2. PCI Scoping Note

As established in [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 5, this system has **no Cardholder Data Environment**. No table, log stream, or third-party data flow described below contains payment-card data. This classification exercise therefore does not need a PAN-specific class — there isn't one.

## 3. Data Classes

### 3.1 User PII (email, password hash) — RDS `user_db`

**What it is:** the `USERS` table in `user-service`'s RDS schema stores `email` (unique), `password_hash`, `role` (`admin`/`customer`), and `created_at` (see [`../UML.md`](../UML.md), ER diagram). A related `REFRESH_TOKENS` table stores a SHA-256 hash of each refresh token, with a real foreign key to `USERS.id` and `ON DELETE CASCADE`.

**Classification:** Personally Identifiable Information (email address) plus a security credential (password hash — never the plaintext password; `user-service` never stores or logs a plaintext password based on its registration/login route implementation).

**Retention mechanism:** RDS itself has no automatic expiration for this data — a user row persists indefinitely once created, for as long as the row exists in `user_db`. Retention is therefore effectively "until manually deleted," and there is currently no automated retention/expiration policy applied to this table at all.

**Data-deletion-on-request:** `user-service`'s routes are `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, and `GET /users/me` (`services/user-service/app.js`). **There is no delete-account or delete-my-data route in `user-service` today.** This is stated plainly as an open item, not glossed over: if this system is ever subject to a legal right-to-erasure obligation (e.g. GDPR Art. 17, or a similar state law), there is currently no application-level path to fulfill it — deletion would have to be performed directly against the database by an operator, which is itself not a documented, repeatable procedure today.

**Gap:** [Action: decide whether a self-service or operator-initiated data-deletion path is required for this system's actual user base and legal obligations, and if so, build it — `user-service` would need a new authenticated `DELETE /users/me` (or equivalent) route, plus a decision on what happens to that user's `CART_ITEMS`/`ORDERS` rows, which reference `user_id` as a soft (app-level only) reference, not a real foreign key — see [`../UML.md`](../UML.md) Section 3.]

### 3.2 RCA Reports (log excerpts routed through a third-party LLM) — DynamoDB `bookstore-rca-reports`

**What it is:** every Alertmanager-triggered RCA run writes a report to the `bookstore-rca-reports` DynamoDB table (`terraform/modules/aiops-rca/dynamodb.tf`): the alert's metadata (`alert_id`, `alertname`, `service`, `severity`), the Claude-generated `narrative`, and `log_references` — up to `var.max_log_lines_per_service` (12) raw Loki log lines per service, each truncated to `var.max_log_line_chars` (400) characters — the same cap applied to what's sent to Claude, so the stored report reflects exactly the evidence the narrative was generated from (`lambdas/rca-lambda/lambda_function.py`'s `write_report()`).

**Classification: the most sensitive data class in the AIOps pipeline, and worth flagging as such explicitly.** Two properties combine to make this the highest-risk data class in this document:

1. The `log_references` field stores raw application log lines, which may incidentally contain request data, identifiers, or other operational detail depending on what each microservice happened to log around the time of the alert — this is not a sanitized or redacted excerpt.
2. Before being written to DynamoDB, that same raw log content is sent to the configured LLM API (`call_llm()`; Anthropic Claude by default, see [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 4.4 and [`VENDOR_RISK_MANAGEMENT_POLICY.md`](VENDOR_RISK_MANAGEMENT_POLICY.md)) for narrative generation — so this data class is both stored internally *and* disclosed to a named third party as a normal part of its processing, not as an exceptional event.

**Retention mechanism:** a DynamoDB TTL on the `expires_at` attribute (`terraform/modules/aiops-rca/dynamodb.tf`'s `ttl` block, `attribute_name = "expires_at"`, `enabled = true`), populated by `write_report()` as `int(time.time()) + REPORT_RETENTION_DAYS * 86400`, where `REPORT_RETENTION_DAYS` defaults to 400 days (`var.rca_report_retention_days`). DynamoDB TTL deletes expired items automatically, typically within about 48 hours of the stored epoch value — not instantaneously at expiry, which is DynamoDB's documented behavior, not a gap in this implementation. This TTL is explicitly a **data-minimization control**, not merely a cost-cleanup measure, precisely because of the raw-log-excerpt content described above: it bounds how long a data class that has already left the system's own boundary (via the Claude API call) continues to also sit in this system's own storage.

**Why 400 days:** matches the same "roughly one year plus a margin" baseline used for CloudTrail retention (`var.cloudtrail_retention_days`, also 400 by default) — a common audit-evidence retention window, applied here to a data class that also functions as incident-history evidence, not purely as transient debugging output.

**Data-deletion-on-request:** no application-level path exists to delete a specific RCA report on request before its TTL expires (e.g. if a report were found to contain data that should not have been retained). A manual `DeleteItem` against DynamoDB is the only mechanism today. [Action: same gap as Section 3.1 — no documented deletion procedure exists yet for this table either.]

### 3.3 Operational Logs (Loki) — unrelated app-debugging data

**What it is:** structured JSON application/access logs from all five microservices (winston for app/error logs, morgan for HTTP access logs — see [`../README.md`](../README.md#tech-stack)), shipped via Fluent Bit into Loki on the monitoring EC2.

**Classification:** operational/debugging data. May incidentally contain some of the same request-level detail as Section 3.2's log excerpts, but this class is scoped to Loki's own storage and is not, by itself, sent to any third party — it only leaves the system boundary when a subset of it is pulled into an RCA report (Section 3.2).

**Retention mechanism:** Loki's `retention_period: 336h` (14 days — `terraform/modules/monitoring-ec2/user-data.sh.tftpl`), with `retention_enabled: true`. This bound was set as part of "Phase 1" of the AIOps work on this branch, alongside the structured-logging rollout itself (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md#aiops-rca-pipeline)).

**Data-deletion-on-request:** not applicable in practice — 14-day retention means any given log line is gone on its own within two weeks regardless, and no per-user extraction/deletion tooling exists or is needed at this data class's current retention window.

### 3.4 CloudTrail Audit Logs — compliance-evidence class

**What it is:** AWS account-level API activity, captured by the multi-region CloudTrail trail (`terraform/cloudtrail.tf`, new this session).

**Classification:** compliance evidence — this class exists specifically to serve as an immutable audit record for SOC 2/ISO 27001/PCI-style audit and incident-investigation purposes (see [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 4.3), not as operational or customer data.

**Retention mechanism:** S3 Object Lock in **COMPLIANCE mode**, `var.cloudtrail_retention_days` (default 400 days), enforced at the storage layer such that not even the AWS account root can delete or shorten retention on a log object before that period elapses. A lifecycle rule expires objects 35 days after the lock period ends (`terraform/cloudtrail.tf`'s `aws_s3_bucket_lifecycle_configuration`) — S3 defers rather than fails a lifecycle expiration attempted while an object is still locked, so the 35-day buffer avoids the delete and the lock's expiry landing on the same day.

**Data-deletion-on-request:** not applicable — this class is retained specifically *because* it must not be deletable on request during its retention window; that is the point of Object Lock compliance mode. As with the other new controls in this session, this is **coded and `terraform validate`'d, not yet applied to a live account** — see [`INFORMATION_SECURITY_POLICY.md`](INFORMATION_SECURITY_POLICY.md) Section 4.6.

## 4. Summary Table

| Data class | Store | Sensitivity | Retention mechanism | Retention period | Deletion-on-request path exists? |
|---|---|---|---|---|---|
| User PII (email, password hash) | RDS `user_db.USERS` | PII + credential | None — indefinite until manual deletion | Indefinite | **No** — open gap, Section 3.1 |
| RCA reports (log excerpts + LLM narrative) | DynamoDB `bookstore-rca-reports` | **Highest** — raw log content, sent to a third-party LLM | DynamoDB TTL on `expires_at` (`write_report()`, `var.rca_report_retention_days`) | 400 days (default) | **No** — open gap, Section 3.2 |
| Operational logs | Loki (monitoring EC2) | Low — routine app-debugging data | Loki `retention_period` | 14 days | Not applicable (short window) |
| CloudTrail audit logs | S3, Object Lock COMPLIANCE mode | Compliance evidence | Object Lock + lifecycle expiration | 400 days (default) + 35-day buffer | Not applicable by design |

## 5. Known Gaps

- **No user-data-deletion path exists** for either the User PII class (Section 3.1) or the RCA Reports class (Section 3.2). This is the most significant open item in this document. [Action: decide whether this system needs a right-to-erasure path given its actual user base and applicable law, and if so, scope and build it — starting with a new `user-service` route and a decision on how `CART_ITEMS`/`ORDERS` soft references to a deleted `user_id` should be handled.]
- **No DPA or documented data-handling review for the Claude API integration** — see [`VENDOR_RISK_MANAGEMENT_POLICY.md`](VENDOR_RISK_MANAGEMENT_POLICY.md) Section 5, restated here because it directly affects this policy's highest-sensitivity data class (Section 3.2).
- **DynamoDB point-in-time recovery is not enabled** on `bookstore-rca-reports` — noted in [`BUSINESS_CONTINUITY_DR_PLAN.md`](BUSINESS_CONTINUITY_DR_PLAN.md) Section 4.5 as an open item for that table specifically.
