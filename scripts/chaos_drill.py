#!/usr/bin/env python3
"""
chaos_drill.py -- run one labeled failure drill and record its ground truth.

Every drill appends one JSON line to drills/ledger.jsonl: what was broken,
where, when it started and ended, when the service recovered, and the true
root cause. Those records are the labels an RCA engine is scored against.

Drill kinds:
  run <experiment>     Apply a Chaos Mesh experiment (k8s/chaos/experiments/)
                       or drill workload (k8s/chaos/workloads/), wait, delete.
  bad-image <service>  Bad deploy: point the Deployment at a nonexistent image
                       tag (new pods stuck in ImagePullBackOff), then roll back.
  bad-config <service> Config change: overwrite one ConfigMap key, restart the
                       Deployment, then restore the value.
  node-loss            Terminate one Spot instance from the chaos node group
                       (refuses to touch app nodes). The node group replaces it.

  list                 Show available experiments and their labels.
  ledger               Print recorded drills (--format table|json).

Cleanup always runs -- normal exit, Ctrl-C or an error partway through. For
bad-image / bad-config the script pauses ArgoCD auto-sync on that one app for
the drill (selfHeal would otherwise undo the fault within minutes) and always
restores it.

Requires: kubectl (context on the bookstore cluster), plus `aws` for
node-loss and `terraform` (or RDS_ENDPOINT in the environment) for the
experiments that target the database. Chaos Mesh must be installed
(terraform var enable_chaos_mesh = true) for `run` on chaos-mesh.org kinds.

Usage:
  python3 scripts/chaos_drill.py list
  python3 scripts/chaos_drill.py run pod-kill-catalog
  python3 scripts/chaos_drill.py run stress-memory-order --notes "baseline run 1"
  python3 scripts/chaos_drill.py bad-image catalog-service --duration 6m
  python3 scripts/chaos_drill.py bad-config order-service \\
      --key NOTIFICATION_SERVICE_URL \\
      --value http://notification-service.notification.svc.cluster.local:9999
  python3 scripts/chaos_drill.py bad-config catalog-service --key DB_PORT --value 3307
  python3 scripts/chaos_drill.py node-loss --duration 10m
  python3 scripts/chaos_drill.py ledger --format json
  python3 scripts/chaos_drill.py run pod-kill-catalog --dry-run
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHAOS_DIR = REPO_ROOT / "k8s" / "chaos"
EXPERIMENT_DIRS = [CHAOS_DIR / "experiments", CHAOS_DIR / "workloads"]
LEDGER = REPO_ROOT / "drills" / "ledger.jsonl"
TF_DIR = REPO_ROOT / "terraform"
ANNOTATION_PREFIX = "drill.bookstore.io/"

SERVICE_NAMESPACES = {
    "api-gateway": "gateway",
    "catalog-service": "catalog",
    "user-service": "user",
    "order-service": "order",
    "notification-service": "notification",
}

DRY_RUN = False


# ── small helpers ─────────────────────────────────────────────────────────────

def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        die(f"`{name}` not found on PATH -- install it and re-run.")
    return found


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def capture(argv, stdin=None) -> str:
    return subprocess.run(
        argv, check=True, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ).stdout.strip()


def try_capture(argv, stdin=None) -> str:
    try:
        return capture(argv, stdin=stdin)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def act(argv, stdin=None) -> str:
    """Run a state-changing command; in --dry-run only print it."""
    printable = " ".join(argv)
    if DRY_RUN:
        print(f"[dry-run] {printable}")
        return ""
    print(f"$ {printable}")
    try:
        return capture(argv, stdin=stdin)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"`{printable}` failed: {e.stderr.strip() or e.stdout.strip()}") from e


def parse_duration(text: str) -> int:
    """'90s' / '6m' / '1h' / '300' -> seconds."""
    m = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", text or "")
    if not m:
        raise ValueError(f"bad duration {text!r} -- use e.g. 90s, 6m, 1h")
    value, unit = int(m.group(1)), m.group(2) or "s"
    return value * {"s": 1, "m": 60, "h": 3600}[unit]


def parse_annotations(manifest: str) -> dict:
    """Pull drill.bookstore.io/* annotations out of a manifest without a YAML
    dependency. Our experiment files keep each annotation on one line."""
    out = {}
    pattern = rf"^\s+{re.escape(ANNOTATION_PREFIX)}([a-z0-9-]+):\s*(.+?)\s*$"
    for key, raw in re.findall(pattern, manifest, flags=re.M):
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        out[key] = raw
    return out


def manifest_field(manifest: str, field: str) -> str:
    """First top-level-ish `kind:` / `name:` / `namespace:` value."""
    m = re.search(rf"^\s*{field}:\s*(\S+)\s*$", manifest, flags=re.M)
    return m.group(1) if m else ""


def substitute(manifest: str, resolver) -> str:
    """Replace ${VAR} placeholders; resolver(name) returns the value or None."""
    missing = []

    def repl(m):
        value = resolver(m.group(1))
        if not value:
            missing.append(m.group(1))
            return m.group(0)
        return value

    result = re.sub(r"\$\{([A-Z0-9_]+)\}", repl, manifest)
    if missing:
        raise ValueError(f"unresolved placeholder(s): {', '.join(sorted(set(missing)))}")
    return result


def resolve_placeholder(name: str):
    if os.environ.get(name):
        return os.environ[name]
    if name == "RDS_ENDPOINT" and shutil.which("terraform"):
        value = try_capture(["terraform", f"-chdir={TF_DIR}", "output", "-raw", "rds_endpoint"])
        # `.endpoint` can carry :3306; DNS/Network chaos want the bare host.
        return value.split(":")[0] if value else None
    return None


def find_experiment(name: str) -> Path:
    candidate = Path(name)
    if candidate.is_file():
        return candidate.resolve()
    for d in EXPERIMENT_DIRS:
        for suffix in ("", ".yaml", ".yml"):
            p = d / f"{name}{suffix}"
            if p.is_file():
                return p
    die(f"no experiment named {name!r} -- see `chaos_drill.py list`")


def all_experiments():
    for d in EXPERIMENT_DIRS:
        if d.is_dir():
            yield from sorted(d.glob("*.yaml"))


def git_commit() -> str:
    return try_capture(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"])


def write_ledger(record: dict) -> None:
    if DRY_RUN:
        print("[dry-run] ledger record:\n" + json.dumps(record, indent=2))
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"\nrecorded drill {record['drill_id']} -> {LEDGER.relative_to(REPO_ROOT)}")


def new_record(args, **fields) -> dict:
    record = {
        "drill_id": str(uuid.uuid4()),
        "operator": args.operator or getpass.getuser(),
        "notes": args.notes or "",
        "git_commit": git_commit(),
        "kube_context": try_capture(["kubectl", "config", "current-context"]),
        "start": None,
        "end": None,
        "recovered_at": None,
        "outcome": "failed",
    }
    record.update(fields)
    return record


def wait_out(seconds: int, label: str) -> bool:
    """Sleep for the drill window. Returns False if cut short by Ctrl-C."""
    if DRY_RUN:
        print(f"[dry-run] would hold fault for {seconds}s")
        return True
    end = time.monotonic() + seconds
    print(f"holding {label} for {seconds}s (Ctrl-C ends early, cleanup still runs)")
    try:
        while (left := end - time.monotonic()) > 0:
            print(f"  {int(left):4d}s left", end="\r", flush=True)
            time.sleep(min(5, left))
        print()
        return True
    except KeyboardInterrupt:
        print("\ninterrupted -- cleaning up")
        return False


def wait_recovered(namespace: str, deployment: str, timeout: int = 600):
    """Block until the Deployment is fully rolled out again; return the time."""
    if DRY_RUN:
        return None
    print(f"waiting for {namespace}/{deployment} to recover (up to {timeout}s)...")
    try:
        capture(["kubectl", "rollout", "status", f"deployment/{deployment}",
                 "-n", namespace, f"--timeout={timeout}s"])
        return now_iso()
    except subprocess.CalledProcessError:
        print(f"warning: {namespace}/{deployment} not recovered within {timeout}s")
        return None


def require_service(service: str) -> str:
    if service not in SERVICE_NAMESPACES:
        die(f"unknown service {service!r}; one of: {', '.join(SERVICE_NAMESPACES)}")
    return SERVICE_NAMESPACES[service]


# ── ArgoCD auto-sync pause/resume (bad-image / bad-config) ────────────────────

def pause_autosync(app: str):
    raw = try_capture(["kubectl", "get", "application", app, "-n", "argocd", "-o", "json"])
    if not raw:
        print(f"note: no ArgoCD Application {app!r} found -- nothing to pause")
        return None
    automated = json.loads(raw).get("spec", {}).get("syncPolicy", {}).get("automated")
    if automated is not None:
        act(["kubectl", "patch", "application", app, "-n", "argocd", "--type", "json",
             "-p", json.dumps([{"op": "remove", "path": "/spec/syncPolicy/automated"}])])
    return automated


def resume_autosync(app: str, automated) -> None:
    if automated is None:
        return
    act(["kubectl", "patch", "application", app, "-n", "argocd", "--type", "merge",
         "-p", json.dumps({"spec": {"syncPolicy": {"automated": automated}}})])


# ── subcommands ───────────────────────────────────────────────────────────────

def cmd_list(_args) -> None:
    for path in all_experiments():
        a = parse_annotations(path.read_text())
        print(f"{path.stem}")
        print(f"    fault:    {a.get('fault-service', '?')} ({a.get('category', '?')}), ~{a.get('duration', '?')}")
        print(f"    cause:    {a.get('root-cause', '?')}")
        print(f"    alerts:   {a.get('expected-alerts', '?')}")
    print("\nbuilt-in: bad-image <service>, bad-config <service> --key K --value V, node-loss")


def cmd_ledger(args) -> None:
    if not LEDGER.exists():
        print("no drills recorded yet")
        return
    rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    if args.format == "json":
        print(json.dumps(rows, indent=2))
        return
    for r in rows:
        print(f"{r['start']}  {r['end'] or '-':20}  {r['outcome']:9}  {r['name']:38}  {r.get('fault_service', '')}")


def cmd_run(args) -> None:
    tool("kubectl")
    path = find_experiment(args.experiment)
    text = path.read_text()
    labels = parse_annotations(text)
    try:
        manifest = substitute(text, resolve_placeholder)
    except ValueError as e:
        die(f"{path.name}: {e} (set it in the environment)")

    kind = manifest_field(manifest, "kind")
    api = manifest_field(manifest, "apiVersion")
    namespace = manifest_field(manifest, "namespace")
    duration = parse_duration(args.duration or labels.get("duration", "5m"))

    if api.startswith("chaos-mesh.org/") and not DRY_RUN:
        if not try_capture(["kubectl", "get", "crd", f"{kind.lower()}.chaos-mesh.org"]) and \
           not try_capture(["kubectl", "get", "crd", f"{kind.lower()}s.chaos-mesh.org"]):
            die("Chaos Mesh CRDs not found -- set enable_chaos_mesh = true and terraform apply first.")
    if labels.get("requires") == "chaos-node" and not DRY_RUN:
        if not try_capture(["kubectl", "get", "nodes", "-l", "role=chaos", "-o", "name"]):
            die("no chaos nodes running -- set chaos_node_desired_size >= 1 first.")
    if namespace == "chaos-testing":
        act(["kubectl", "apply", "-f", str(CHAOS_DIR / "namespace.yaml")])

    fault_service = labels.get("fault-service", "")
    record = new_record(
        args,
        name=path.stem,
        kind=f"chaos-mesh/{kind}" if api.startswith("chaos-mesh.org/") else kind,
        category=labels.get("category", ""),
        fault_service=fault_service,
        namespace=namespace,
        root_cause=labels.get("root-cause", ""),
        expected_alerts=[x.strip() for x in labels.get("expected-alerts", "").split(",") if x.strip()],
        manifest=str(path.relative_to(REPO_ROOT)),
    )

    applied = False
    try:
        record["start"] = now_iso()
        act(["kubectl", "apply", "-f", "-"], stdin=manifest)
        applied = True
        if labels.get("restart-target"):
            ns, deploy = labels["restart-target"].split("/", 1)
            act(["kubectl", "rollout", "restart", f"deployment/{deploy}", "-n", ns])
        completed = wait_out(duration, path.stem)
        record["outcome"] = "completed" if completed else "aborted"
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        record["notes"] = (record["notes"] + f" | error: {e}").strip(" |")
    finally:
        if applied:
            try:
                act(["kubectl", "delete", "-f", "-", "--ignore-not-found"], stdin=manifest)
            except RuntimeError as e:
                print(f"error: cleanup failed, delete it by hand: {e}", file=sys.stderr)
        record["end"] = now_iso()
        if fault_service in SERVICE_NAMESPACES:
            record["recovered_at"] = wait_recovered(SERVICE_NAMESPACES[fault_service], fault_service)
        write_ledger(record)


def cmd_bad_image(args) -> None:
    tool("kubectl")
    service = args.service
    ns = require_service(service)
    duration = parse_duration(args.duration)
    image = try_capture(["kubectl", "get", "deployment", service, "-n", ns,
                         "-o", "jsonpath={.spec.template.spec.containers[0].image}"])
    if not image and not DRY_RUN:
        die(f"deployment {ns}/{service} not found")
    repo = image.rsplit(":", 1)[0] if image else f"<repo>/{service}"
    bad = f"{repo}:drill-does-not-exist"

    record = new_record(
        args,
        name=f"bad-image-{service}",
        kind="bad-deploy",
        category="bad-deploy",
        fault_service=service,
        namespace=ns,
        root_cause=f"bad deploy: {service} image changed to nonexistent tag {bad.split(':')[-1]}; "
                   f"new pods stuck in ImagePullBackOff, old ReplicaSet keeps serving",
        expected_alerts=["ContainerWaiting", "DeploymentReplicasUnavailable"],
        previous_image=image,
    )
    automated = None
    changed = False
    try:
        automated = pause_autosync(service)
        record["start"] = now_iso()
        act(["kubectl", "set", "image", f"deployment/{service}", "-n", ns, f"{service}={bad}"])
        changed = True
        record["outcome"] = "completed" if wait_out(duration, f"bad image on {service}") else "aborted"
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
    finally:
        if changed:
            try:
                act(["kubectl", "rollout", "undo", f"deployment/{service}", "-n", ns])
            except RuntimeError as e:
                print(f"error: rollback failed: {e}", file=sys.stderr)
        resume_autosync(service, automated)
        record["end"] = now_iso()
        record["recovered_at"] = wait_recovered(ns, service)
        write_ledger(record)


def configmap_for(service: str, ns: str, key: str) -> str:
    raw = try_capture(["kubectl", "get", "deployment", service, "-n", ns, "-o", "json"])
    if not raw:
        return ""
    for c in json.loads(raw)["spec"]["template"]["spec"]["containers"]:
        for env in c.get("env", []):
            ref = env.get("valueFrom", {}).get("configMapKeyRef")
            if ref and ref.get("key") == key:
                return ref["name"]
    return ""


def cmd_bad_config(args) -> None:
    tool("kubectl")
    service = args.service
    ns = require_service(service)
    duration = parse_duration(args.duration)
    cm = args.configmap or configmap_for(service, ns, args.key)
    if not cm:
        if DRY_RUN:
            cm = "<configmap>"
        else:
            die(f"no ConfigMap key {args.key!r} referenced by {ns}/{service} -- pass --configmap")
    original = try_capture(["kubectl", "get", "configmap", cm, "-n", ns,
                            "-o", f"jsonpath={{.data.{args.key}}}"])

    record = new_record(
        args,
        name=f"bad-config-{service}-{args.key.lower()}",
        kind="config-change",
        category="config-change",
        fault_service=service,
        namespace=ns,
        root_cause=f"config change: ConfigMap {ns}/{cm} key {args.key} changed "
                   f"from {original!r} to {args.value!r}, {service} restarted with it",
        expected_alerts=[x for x in (args.expect or "HighErrorRate").split(",") if x],
        config={"configmap": cm, "key": args.key, "original": original, "injected": args.value},
    )
    automated = None
    changed = False
    try:
        automated = pause_autosync(service)
        record["start"] = now_iso()
        act(["kubectl", "patch", "configmap", cm, "-n", ns, "--type", "merge",
             "-p", json.dumps({"data": {args.key: args.value}})])
        changed = True
        act(["kubectl", "rollout", "restart", f"deployment/{service}", "-n", ns])
        record["outcome"] = "completed" if wait_out(duration, f"{args.key}={args.value}") else "aborted"
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
    finally:
        if changed:
            try:
                act(["kubectl", "patch", "configmap", cm, "-n", ns, "--type", "merge",
                     "-p", json.dumps({"data": {args.key: original}})])
                act(["kubectl", "rollout", "restart", f"deployment/{service}", "-n", ns])
            except RuntimeError as e:
                print(f"error: restore failed: {e}", file=sys.stderr)
        resume_autosync(service, automated)
        record["end"] = now_iso()
        record["recovered_at"] = wait_recovered(ns, service)
        write_ledger(record)


def cmd_node_loss(args) -> None:
    tool("kubectl")
    tool("aws")
    duration = parse_duration(args.duration)
    raw = try_capture(["kubectl", "get", "nodes", "-l", "role=chaos", "-o", "json"])
    nodes = json.loads(raw)["items"] if raw else []
    if not nodes:
        die("no chaos nodes running -- set chaos_node_desired_size >= 1 first. "
            "node-loss never touches app nodes.")
    node = nodes[0]
    node_name = node["metadata"]["name"]
    instance_id = node["spec"]["providerID"].rsplit("/", 1)[-1]
    region = node["metadata"]["labels"].get("topology.kubernetes.io/region", "")
    nodegroup = try_capture([
        "aws", "ec2", "describe-instances", "--region", region, "--instance-ids", instance_id,
        "--query", "Reservations[0].Instances[0].Tags[?Key=='eks:nodegroup-name']|[0].Value",
        "--output", "text"])
    if "chaos" not in nodegroup:
        die(f"{instance_id} is in node group {nodegroup!r}, not a chaos group -- refusing.")

    record = new_record(
        args,
        name="node-loss-chaos",
        kind="node-loss",
        category="node-loss",
        fault_service="node",
        namespace="",
        root_cause=f"EKS node {node_name} ({instance_id}, Spot, {nodegroup}) terminated; "
                   f"pods on it rescheduled, node group launches a replacement",
        expected_alerts=["ChaosNodeDown", "KubeNodeNotReady", "KubeNodeCountDecreased"],
        instance_id=instance_id,
    )
    try:
        record["start"] = now_iso()
        act(["aws", "ec2", "terminate-instances", "--region", region, "--instance-ids", instance_id])
        record["outcome"] = "completed" if wait_out(duration, f"loss of {node_name}") else "aborted"
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
    finally:
        record["end"] = now_iso()
        write_ledger(record)


def main(argv=None) -> None:
    global DRY_RUN
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="print actions, change nothing")
    common.add_argument("--operator", help="who ran the drill (default: OS user)")
    common.add_argument("--notes", help="free-text note stored with the record")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common]).set_defaults(func=cmd_list)

    p = sub.add_parser("ledger", parents=[common])
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.set_defaults(func=cmd_ledger)

    p = sub.add_parser("run", parents=[common])
    p.add_argument("experiment", help="name in k8s/chaos/{experiments,workloads}/ or a path")
    p.add_argument("--duration", help="override the experiment's drill.bookstore.io/duration")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("bad-image", parents=[common])
    p.add_argument("service", choices=sorted(SERVICE_NAMESPACES))
    p.add_argument("--duration", default="6m")
    p.set_defaults(func=cmd_bad_image)

    p = sub.add_parser("bad-config", parents=[common])
    p.add_argument("service", choices=sorted(SERVICE_NAMESPACES))
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--configmap", help="default: the ConfigMap the Deployment reads --key from")
    p.add_argument("--expect", help="comma-separated expected alerts (default HighErrorRate)")
    p.add_argument("--duration", default="6m")
    p.set_defaults(func=cmd_bad_config)

    p = sub.add_parser("node-loss", parents=[common])
    p.add_argument("--duration", default="10m")
    p.set_defaults(func=cmd_node_loss)

    args = parser.parse_args(argv)
    DRY_RUN = args.dry_run
    args.func(args)


if __name__ == "__main__":
    main()
