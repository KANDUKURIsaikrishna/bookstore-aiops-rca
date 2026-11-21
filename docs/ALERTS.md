# Alerts

Every alert the monitoring EC2 can fire, what it catches, and why it's shaped the way it is. The rules live in `terraform/modules/monitoring-ec2/user-data.sh.tftpl`. Prometheus rules are under `RULES_EOF`; Loki log rules are under `LOKI_RULES_EOF`.

All alerts go to Alertmanager. From there each one goes to the RCA webhook and to email. `critical` alerts repeat every 1h and `warning` alerts every 6h.

## Labels the RCA can rely on

| Label | On which alerts | Notes |
|---|---|---|
| `alertname`, `severity` | all | `severity` is `critical` or `warning` |
| `service` | every service alert; pod and deployment alerts when the pod belongs to a bookstore service; Loki alerts | Values: `api-gateway`, `catalog-service`, `user-service`, `order-service`, `notification-service` |
| `namespace`, `pod` | pod-level alerts | |
| `deployment` | deployment alerts | |
| `node` / `instance`, `nodegroup` | node alerts | `nodegroup` tells the app group from the Spot chaos group |
| `source=loki` | log-based alerts | |
| `fingerprint` | every alert | Stable id. Use it as `alert_id` |

Each affected service, pod or node gets **its own** notification. `group_by` is `alertname, severity, service, namespace, pod, node, instance`. See OBS-083.

## Nodes

| Alert | Severity | Fires when | For |
|---|---|---|---|
| NodeDown | critical | node-exporter on an **app** node unreachable | 3m |
| ChaosNodeDown | warning | same, on a **chaos** (Spot) node | 3m |
| KubeNodeNotReady | critical | app node `Ready=false` | 2m |
| ChaosNodeNotReady | warning | chaos node `Ready=false` | 2m |
| KubeNodeCountDecreased | warning | fewer nodes than at any point in the last 15m. This catches terminated nodes, which vanish from scrape targets instead of going `up=0` | 2m |
| KubeNodePressure | warning | Memory, Disk or PID pressure condition | 2m |
| HighCPUUsage | warning | node CPU > 85% | 10m |
| HighMemoryUsage | warning | node memory > 85% | 10m |
| NodeDiskSpaceLow | warning | root filesystem < 15% free | 10m |
| NodePodCapacityHigh | warning | running pods > 90% of the node's pod limit (t3.medium = 17) | 15m |

## Workloads

| Alert | Severity | Fires when | For |
|---|---|---|---|
| PodCrashLooping | warning | > 3 restarts in 15m | 1m |
| ContainerWaiting | warning | CrashLoopBackOff, ImagePullBackOff, ErrImagePull, CreateContainer*Error or InvalidImageName | 2m |
| ContainerOOMKilled | warning | last termination was OOMKilled and it restarted in the last 10m | — |
| PodNotReady | warning | Running or Pending pod not Ready | 5m |
| DeploymentReplicasUnavailable | warning | any unavailable replica | 5m |
| DeploymentDown | critical | zero available replicas while spec > 0 | 1m |
| HPAMaxedOut | warning | HPA at max replicas | 15m |
| KubeJobFailed | warning | a Job has failed pods, for example a schema-init hook | 1m |
| HighPodCPUUsage | warning | pod > 0.3 cores. This is a **demo** alert for `scripts/simulate_load.py`. It excludes kube-system, argocd and chaos-mesh | 1m |
| PodCPUNearLimit | warning | container CPU > 90% of its limit | 3m |
| CPUThrottlingHigh | warning | > 50% of CFS periods throttled | 10m |
| PodMemoryNearLimit | warning | working set > 90% of the memory limit. This leads up to an OOMKill | 2m |

## Services (from each service's own `/metrics`)

| Alert | Severity | Fires when | For |
|---|---|---|---|
| HighErrorRate | critical | per service, 5xx ratio > 5% **and** > 0.05 req/s of traffic | 1m |
| HighLatencyP95 | warning | per service, p95 > 1s, excluding `/health` and `/metrics`, with the same traffic floor | 3m |
| HighRequestRate | warning | per service, > 3 req/s, excluding health and metrics routes | 1m |
| ServiceTargetDown | critical | a service pod's `/metrics` unreachable (OBS-081) | 3m |
| NotificationDispatchFailures | warning | order-service failed to call notification-service (`dependency=notification-service`) | — |
| EventLoopLagHigh | warning | Node.js event loop p99 lag > 0.5s | 5m |

The histogram buckets top out at 2s, so the measured p95 can't read above 2s.

## Logs (Loki ruler)

These alerts take `namespace` and `service` from the Fluent Bit `filename` field. They cover only the 5 bookstore namespaces.

| Alert | Severity | Fires when | For |
|---|---|---|---|
| DBErrorsInLogs | critical | > 3 lines in 5m matching `db_error`, `ER_*` MySQL codes, `PROTOCOL_CONNECTION_LOST`, `Too many connections` or the RDS hostname (DNS failures) | 1m |
| UpstreamConnectionErrors | warning | > 5 lines in 5m with ECONNREFUSED, ECONNRESET, ETIMEDOUT, `socket hang up`, TimeoutError or proxy errors | 1m |
| AppErrorLogSpike | warning | > 20 error-level JSON log lines in 5m | 2m |

These log alerts are the only database signal. Prometheus has no RDS metrics.

## Monitoring stack itself

| Alert | Severity | Fires when | For |
|---|---|---|---|
| KubeStateMetricsDown | critical | kube-state-metrics unreachable, which leaves every Kubernetes-state alert blind | 5m |
| ScrapeTargetDown | warning | kubelet-cadvisor, Alertmanager or Loki scrape down | 10m |
| AlertmanagerNotificationsFailing | warning | the webhook or email integration is failing. This catches OBS-076-style breakage, and the email route still delivers it | 5m |
| LogIngestionStalled | warning | Loki received zero lines for 10m, meaning the Fluent Bit pipeline is broken | 10m |

## Inhibition

Alertmanager suppresses a symptom while its root alert is firing:
- `DeploymentDown` suppresses `DeploymentReplicasUnavailable`, `PodNotReady` and `ServiceTargetDown` in the same namespace.
- `ContainerWaiting`, `PodCrashLooping` and `ContainerOOMKilled` suppress `PodNotReady` for the same pod.
- Any node down or NotReady alert suppresses `KubeNodeCountDecreased`.
- A `critical` alert suppresses a `warning` with the same alertname and instance.

## Which drill exercises which alert

See [`k8s/chaos/README.md`](../k8s/chaos/README.md). Each experiment lists its expected alerts, and `scripts/chaos_drill.py list` prints them.

## Changing rules

- **User-data is gzip'd and capped at 16 KB compressed.** It's about 13.4 KB now. Keep the rule text compact and put explanations here, not in the template.
- **A rule change replaces the monitoring EC2** (`user_data_replace_on_change`). The Elastic IP and URLs stay, but the private IP changes. Grafana, Prometheus and Loki data reset.
- **Validate before applying.** Every PromQL `expr` can be checked against the live Prometheus API (`/api/v1/query`). LogQL can be checked through Grafana's datasource proxy (`/api/datasources/proxy/uid/loki/loki/api/v1/query`).
