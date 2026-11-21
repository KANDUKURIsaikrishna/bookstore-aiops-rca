# Drill ledger

`ledger.jsonl` is written by `scripts/chaos_drill.py`, one JSON object per drill. It's the ground truth for scoring RCA output. Commit it so the labels survive.

| Field | Meaning |
|---|---|
| `drill_id` | UUID |
| `name`, `kind`, `category` | which drill, for example `stress-memory-order`, `chaos-mesh/StressChaos`, `memory-leak` |
| `fault_service` | where the fault was injected: a service name, `rds-mysql` or `node` |
| `namespace` | Kubernetes namespace of the fault |
| `root_cause` | the true root cause in one line |
| `expected_alerts` | alert names the drill is designed to trigger |
| `start`, `end` | UTC ISO-8601. The fault was active from `start` to `end` |
| `recovered_at` | when the target Deployment was fully rolled out again (null for node drills or on timeout) |
| `outcome` | `completed`, `aborted` (Ctrl-C) or `failed` |
| `operator`, `notes`, `git_commit`, `kube_context` | provenance |
| `manifest` / `previous_image` / `config` / `instance_id` | drill-specific detail |

To join with RCA reports, match alerts whose `startsAt` falls between `start` and `end` (plus a few minutes for `for:` holds). Then compare the RCA's stated cause against `root_cause` and `fault_service`.

Export everything with `python3 scripts/chaos_drill.py ledger --format json`.
