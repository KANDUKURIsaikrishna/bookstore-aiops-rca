# AIOps Log-Based Root Cause Analysis — Design

## Background

This repo is a fork of `aws_three_tier_archi_observability` (bookstore three-tier app + observability stack). It exists as a separate repo so this work doesn't disrupt the original project.

Current state inherited from the fork:
- 5 Node/Express services (api-gateway, catalog-service, order-service, user-service, notification-service) log only via `morgan` + scattered `console.log`/`console.error` to stdout — nothing structured, nothing shipped anywhere.
- CloudWatch (Logs/CloudTrail/GuardDuty/VPC Flow Logs) was deliberately removed from this project (2026-08-23) — no CloudWatch usage going forward.
- A real log pipeline exists but is broken: Loki + Prometheus + Grafana + Alertmanager run via docker-compose on a monitoring EC2 host; Fluent Bit is meant to ship container logs from EKS nodes to Loki, but its yum repo URL is wrong (`terraform/modules/eks/node-user-data.sh.tftpl`), so install silently fails on every node. Zero logs currently reach Loki — only metrics flow.
- The AWS region this project runs in has a **region-wide EC2 quota limit** — no new EC2 instances can be provisioned. This is a hard constraint on all design choices below.

## Goal

Root-cause analysis (RCA) for incidents: when an alert fires, automatically correlate logs across the app's tiers and produce a human-readable explanation of the likely root cause.

## Phase 1 — Fix Logging

Prerequisite: without working logs, there's nothing for the RCA tool to read.

1. Fix the Fluent Bit yum repo URL bug in `terraform/modules/eks/node-user-data.sh.tftpl` so the daemonset installs successfully on EKS nodes.
2. Verify Loki config/retention in `terraform/modules/monitoring-ec2/user-data.sh.tftpl` (the container is already running; just unreachable today).
3. Add structured JSON logging (winston) to all 5 services, replacing `console.log`/`console.error` and JSON-ifying the morgan access logs, so log lines are parseable (level, service, timestamp, message, request id where available).
4. Verify end-to-end: Fluent Bit ships container logs → Loki ingests them → queryable via Grafana's existing Loki datasource or the Loki HTTP API directly.

No new EC2 instances required — this only fixes an existing daemonset install and existing container config.

## Phase 2 — AIOps RCA Tool

Serverless by design, to respect the EC2 quota constraint — no persistent compute added.

**Trigger**: Prometheus Alertmanager fires → webhook → API Gateway → Lambda (event-triggered RCA, not on-demand or continuous polling).

**RCA Lambda** (Python):
1. Receives alert payload (service, alert name, firing timestamp, labels).
2. Queries Loki via its HTTP API (LogQL) for logs across all 5 services for the incident window (firing time ± buffer, e.g. ±5 min).
3. Builds a prompt containing the alert metadata + relevant log excerpts and calls the Claude API for a fully LLM-driven RCA: likely root cause, which tier it originated in, and a suggested fix. No classical log-parsing/rule-based correlation layer — the LLM does the correlation directly over the raw log excerpts.
4. Writes the RCA report to DynamoDB (alert_id, timestamp, service, narrative, references to the raw log lines used).
5. Sends an SES email to the on-call/team with the RCA summary.

**Dashboard**: static site on S3 + CloudFront, backed by API Gateway + a read Lambda that queries DynamoDB, listing past RCA reports and showing report detail (narrative, log excerpts, timestamps).

## Error Handling

- If the Loki query returns no logs for the incident window, the Lambda still produces a report noting "no logs found for this window" rather than failing silently.
- Transient Claude API failures: retry with backoff inside the Lambda invocation.
- Failed Lambda invocations go to an SQS dead-letter queue for visibility (not silently dropped).
- The Alertmanager webhook endpoint on API Gateway is protected (API key or resource policy) since it's a public HTTP endpoint.

## Testing

- Unit tests (pytest) for the RCA Lambda and the dashboard-read Lambda: mock the Loki HTTP API, mock the Claude API, use `moto` for DynamoDB.
- Manual end-to-end test: fire a test alert through Alertmanager, confirm an RCA email arrives and a corresponding entry appears on the dashboard.
- `terraform plan` review on any infra change to confirm no new EC2 resources are introduced, given the region's EC2 quota constraint.

## Out of Scope (for this spec)

- Anomaly detection, log clustering, and alert-noise reduction (other AIOps directions considered and deferred — RCA is the chosen focus).
- On-demand or continuous-correlation trigger modes (event-triggered via Alertmanager was chosen).
- Classical/hybrid log-parsing pipelines (Drain-style template mining) — the LLM reasons directly over raw log excerpts.
