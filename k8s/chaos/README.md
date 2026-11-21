# Chaos drills

Labeled failure drills for calibrating the RCA. Each drill injects one known fault, holds it, cleans up, and appends the ground truth to `drills/ledger.jsonl`. Nothing in this directory is synced by ArgoCD. Drills only run when someone runs them.

## One-time setup

In `terraform.tfvars`, or through `config.env` and `scripts/configure.py`:

```hcl
enable_chaos_mesh       = true   # Chaos Mesh in namespace chaos-mesh
enable_chaos_node_group = true   # Spot node group (role=chaos, taint chaos=true:NoSchedule)
chaos_node_desired_size = 1      # only needed for node-loss / cpu-burner drills; back to 0 after
```

Then run `terraform apply`. Chaos Mesh can act only on namespaces annotated `chaos-mesh.org/inject: enabled`: the 5 service namespaces, `bookstore` and `chaos-testing`. The dashboard is disabled, so drive it with kubectl.

## Running drills

```bash
python3 scripts/chaos_drill.py list                         # experiments + expected alerts
python3 scripts/chaos_drill.py run pod-kill-catalog
python3 scripts/chaos_drill.py run stress-memory-order --notes "round 2"
python3 scripts/chaos_drill.py bad-image catalog-service --duration 6m
python3 scripts/chaos_drill.py bad-config order-service \
    --key NOTIFICATION_SERVICE_URL \
    --value http://notification-service.notification.svc.cluster.local:9999 \
    --expect NotificationDispatchFailures
python3 scripts/chaos_drill.py bad-config catalog-service --key DB_PORT --value 3307 \
    --expect DBErrorsInLogs,HighErrorRate
python3 scripts/chaos_drill.py node-loss                     # chaos nodes only, never app nodes
python3 scripts/chaos_drill.py ledger --format json          # export labels for scoring
```

Add `--dry-run` to any command to print what it would do. Cleanup always runs, including after Ctrl-C.

- **bad-image and bad-config pause ArgoCD auto-sync** on that one app during the drill and restore it afterward. `ignoreApplicationDifferences` in `k8s/argocd/applicationset-microservices.yaml` allows this.
- **Most drills need traffic.** Error-rate and latency alerts have a traffic floor, so run `python3 scripts/simulate_load.py --traffic-only --duration 600` alongside them. The partition drill needs `POST /orders` traffic specifically.

## Drill catalogue

| Drill | Fault | True root cause | Expected alerts |
|---|---|---|---|
| pod-kill-catalog | PodChaos kill | catalog-service pod killed (1 replica) | DeploymentReplicasUnavailable, HighErrorRate |
| pod-failure-order | PodChaos failure 5m | order-service container replaced by pause image | PodNotReady, DeploymentDown, ServiceTargetDown, HighErrorRate |
| crashloop-user | container-kill every 30s | user-service CrashLoopBackOff | PodCrashLooping, ContainerWaiting, PodNotReady, HighErrorRate |
| network-delay-order-rds | +2s latency to RDS | slow DB from order-service | HighLatencyP95 |
| network-partition-order-notification | partition | order → notification unreachable | NotificationDispatchFailures |
| network-loss-catalog | 40% packet loss | catalog-service network degraded | HighLatencyP95, HighErrorRate |
| stress-cpu-catalog | CPU stress in container | catalog-service CPU saturated at limit | PodCPUNearLimit, CPUThrottlingHigh, HighLatencyP95 |
| stress-memory-order | 200MB stress vs 128Mi limit | order-service OOMKilled | PodMemoryNearLimit, ContainerOOMKilled, PodCrashLooping |
| dns-error-rds | DNSChaos on RDS hostname | catalog-service can't resolve DB | DBErrorsInLogs, HighErrorRate |
| http-abort-catalog | HTTP 503 on `GET /books` | catalog-service, but the 5xx only shows in api-gateway metrics | HighErrorRate (api-gateway) |
| http-delay-user | +3s on `/auth` | user-service slow | HighLatencyP95 |
| time-skew-user | clock −2h | JWTs issued already expired, so 401s | none (silent failure) |
| pod-kill-gateway-control | kill 1 of 2 gateway pods | none: negative control | none expected |
| cpu-burner-chaos-node | busy-loop pods on chaos node | node CPU saturation | HighCPUUsage |
| bad-image `<svc>` | nonexistent image tag | bad deploy | ContainerWaiting, DeploymentReplicasUnavailable |
| bad-config `<svc>` | ConfigMap key changed | config change | depends on key (`--expect`) |
| node-loss | terminate Spot chaos node | node loss | ChaosNodeDown, KubeNodeNotReady, KubeNodeCountDecreased |

That's 17 drill types. Repeating drills on different services and on different days gets past the 20+ labeled incidents needed for calibration.

## Adding an experiment

Drop a manifest in `experiments/` or `workloads/` with these annotations:

```yaml
metadata:
  annotations:
    drill.bookstore.io/fault-service: catalog-service
    drill.bookstore.io/category: pod-loss
    drill.bookstore.io/root-cause: "one-line true root cause, written for a scorer"
    drill.bookstore.io/expected-alerts: "AlertA,AlertB"
    drill.bookstore.io/duration: "6m"
```

Optional annotations:
- `drill.bookstore.io/restart-target: <ns>/<deployment>` restarts the deployment after injecting the fault.
- `drill.bookstore.io/requires: chaos-node` makes the drill refuse to run without a chaos node.

Use `${RDS_ENDPOINT}` for the database host. It comes from the environment or from `terraform output rds_endpoint`. `scripts/tests/test_chaos_drill.py` fails CI if an experiment is missing a label or targets a namespace that isn't opted in.
