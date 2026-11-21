# ─────────────────────────────────────────────────────────────────────────────
# RBAC for the monitoring EC2's Prometheus to scrape:
#   1. kubelet's /metrics/cadvisor directly on each node (real per-pod
#      CPU/memory usage — kube-state-metrics only exposes requests/limits/
#      status, never actual usage) -- needs "nodes/proxy" et al.
#   2. each app pod's own /metrics (prom-client, HTTP request counters/
#      histograms) via the API server's pod-proxy endpoint, since ClusterIP
#      Services and pod IPs aren't reachable from outside the cluster's pod
#      network the way node-hosted processes are -- needs "pods/proxy".
#
# Both are authorized via a SubjectAccessReview against the API server (get
# on the given subresource) — the standard shape for any out-of-cluster
# Prometheus scrape of either kind. AmazonEKSViewPolicy (already associated
# with the monitoring EC2's access entry) doesn't cover either, so a
# dedicated ClusterRole/ClusterRoleBinding is needed, bound to the stable
# "monitoring-metrics-readers" group set on that access entry rather than the
# principal ARN directly (see the access entry's own comment for why).
# ─────────────────────────────────────────────────────────────────────────────

resource "kubectl_manifest" "monitoring_kubelet_reader_role" {
  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
      name: monitoring-kubelet-reader
    rules:
      - apiGroups: [""]
        resources: ["nodes/proxy", "nodes/metrics", "nodes/stats", "pods/proxy"]
        verbs: ["get"]
  YAML

  depends_on = [module.eks]
}

resource "kubectl_manifest" "monitoring_kubelet_reader_binding" {
  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: monitoring-kubelet-reader
    subjects:
      - kind: Group
        name: monitoring-metrics-readers
        apiGroup: rbac.authorization.k8s.io
    roleRef:
      kind: ClusterRole
      name: monitoring-kubelet-reader
      apiGroup: rbac.authorization.k8s.io
  YAML

  depends_on = [
    module.eks,
    module.monitoring_ec2,
    kubectl_manifest.monitoring_kubelet_reader_role,
  ]
}

# ─────────────────────────────────────────────────────────────────────────────
# RBAC for kube-state-metrics (runs as a plain Docker container on the
# monitoring EC2 -- see modules/monitoring-ec2 -- not deployed via its
# official Helm chart, which would normally ship this ClusterRole/Binding
# bundled in). Without it, kube-state-metrics has zero grants and every
# list/watch it makes is denied -- confirmed live (OBS-XXX, chaos/RCA
# validation drill, 2026-10-01): PodCrashLooping and every other
# kube-state-metrics-derived alert (HighPodCPUUsage, HighPodMemoryUsage,
# KubeStateMetricsDown's own `up` metric is unaffected, but everything
# downstream of kube_pod_*/kube_node_* series never has data to alert on)
# silently never fires, no matter how long the underlying condition holds.
# Resource list matches kube-state-metrics' own upstream ClusterRole
# (https://github.com/kubernetes/kube-state-metrics's examples/standard
# manifests) so every --resources it tries by default is covered, not just
# the ones this project's current alert rules happen to use today.
# ─────────────────────────────────────────────────────────────────────────────

resource "kubectl_manifest" "kube_state_metrics_role" {
  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRole
    metadata:
      name: kube-state-metrics
    rules:
      - apiGroups: [""]
        resources:
          - configmaps
          - secrets
          - nodes
          - pods
          - services
          - serviceaccounts
          - resourcequotas
          - replicationcontrollers
          - limitranges
          - persistentvolumeclaims
          - persistentvolumes
          - namespaces
          - endpoints
        verbs: ["list", "watch"]
      - apiGroups: ["apps"]
        resources: ["statefulsets", "daemonsets", "deployments", "replicasets"]
        verbs: ["list", "watch"]
      - apiGroups: ["batch"]
        resources: ["cronjobs", "jobs"]
        verbs: ["list", "watch"]
      - apiGroups: ["autoscaling"]
        resources: ["horizontalpodautoscalers"]
        verbs: ["list", "watch"]
      - apiGroups: ["policy"]
        resources: ["poddisruptionbudgets"]
        verbs: ["list", "watch"]
      - apiGroups: ["certificates.k8s.io"]
        resources: ["certificatesigningrequests"]
        verbs: ["list", "watch"]
      - apiGroups: ["discovery.k8s.io"]
        resources: ["endpointslices"]
        verbs: ["list", "watch"]
      - apiGroups: ["storage.k8s.io"]
        resources: ["storageclasses", "volumeattachments"]
        verbs: ["list", "watch"]
      - apiGroups: ["admissionregistration.k8s.io"]
        resources: ["mutatingwebhookconfigurations", "validatingwebhookconfigurations"]
        verbs: ["list", "watch"]
      - apiGroups: ["networking.k8s.io"]
        resources: ["networkpolicies", "ingressclasses", "ingresses"]
        verbs: ["list", "watch"]
      - apiGroups: ["coordination.k8s.io"]
        resources: ["leases"]
        verbs: ["list", "watch"]
  YAML

  depends_on = [module.eks]
}

resource "kubectl_manifest" "kube_state_metrics_binding" {
  yaml_body = <<-YAML
    apiVersion: rbac.authorization.k8s.io/v1
    kind: ClusterRoleBinding
    metadata:
      name: kube-state-metrics
    subjects:
      - kind: Group
        name: monitoring-metrics-readers
        apiGroup: rbac.authorization.k8s.io
    roleRef:
      kind: ClusterRole
      name: kube-state-metrics
      apiGroup: rbac.authorization.k8s.io
  YAML

  depends_on = [
    module.eks,
    module.monitoring_ec2,
    kubectl_manifest.kube_state_metrics_role,
  ]
}
