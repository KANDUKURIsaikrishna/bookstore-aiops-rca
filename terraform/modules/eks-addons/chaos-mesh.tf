# ── Chaos Mesh (opt-in, var.enable_chaos_mesh) ────────────────────────────────
# Fault injection for RCA calibration drills. Experiment manifests live in
# k8s/chaos/experiments/ and are run on demand via scripts/chaos_drill.py,
# which records ground-truth labels (start/end/fault/root cause) for scoring.
# Nothing under k8s/chaos/ is synced by ArgoCD -- experiments never run
# unless someone runs them deliberately.
#
# Safety rails:
#   - controllerManager.enableFilterNamespace: Chaos Mesh only acts on
#     namespaces annotated chaos-mesh.org/inject=enabled (the 5 bookstore
#     service namespaces, the frontend namespace and chaos-testing). kube-system,
#     argocd, external-secrets and the controller's own namespace can't be
#     targeted even by a mistyped selector.
#   - dashboard.create=false: the dashboard is an unauthenticated
#     cluster-wide fault-injection UI by default. Drive it with kubectl only.
#
# Placement: chaos-daemon must run on every node a target pod can land on
# (it injects faults from the node side), so it tolerates every taint,
# including the chaos node group's chaos=true:NoSchedule. The
# controller-manager prefers the chaos node group but falls back to app nodes
# when that group is scaled to 0 -- a hard nodeSelector would leave it
# Pending and fail this release's wait.
#
# Pod budget: +1 chaos-daemon per node, +1 controller-manager, +1 dns-server.
# On the 3 app nodes (17 pods each, ~36/51 used) that's +5 at most.

resource "helm_release" "chaos_mesh" {
  count = var.enable_chaos_mesh ? 1 : 0

  name             = "chaos-mesh"
  repository       = "https://charts.chaos-mesh.org"
  chart            = "chaos-mesh"
  version          = var.chaos_mesh_chart_version
  namespace        = "chaos-mesh"
  create_namespace = true
  wait             = true
  timeout          = 600

  values = [yamlencode({
    controllerManager = {
      replicaCount          = 1
      enableFilterNamespace = true
      tolerations = [{
        key      = "chaos"
        operator = "Equal"
        value    = "true"
        effect   = "NoSchedule"
      }]
      affinity = {
        nodeAffinity = {
          preferredDuringSchedulingIgnoredDuringExecution = [{
            weight = 100
            preference = {
              matchExpressions = [{ key = "role", operator = "In", values = ["chaos"] }]
            }
          }]
        }
      }
    }
    chaosDaemon = {
      # EKS AL2 nodes run containerd, not Docker.
      runtime     = "containerd"
      socketPath  = "/run/containerd/containerd.sock"
      tolerations = [{ operator = "Exists" }]
    }
    dashboard = {
      create = false
    }
    # Needed for DNSChaos (k8s/chaos/experiments/dns-error-rds.yaml).
    dnsServer = {
      create = true
    }
  })]
}
