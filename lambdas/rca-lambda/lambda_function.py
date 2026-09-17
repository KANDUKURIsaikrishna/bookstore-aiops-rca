import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
CLAUDE_API_KEY_SECRET_ARN = os.environ["CLAUDE_API_KEY_SECRET_ARN"]
SES_FROM_EMAIL = os.environ["SES_FROM_EMAIL"]
SES_TO_EMAIL = os.environ["SES_TO_EMAIL"]
# Haiku by default, not Sonnet -- this is a structured summarization task
# (read N log lines, name a root cause, cite the lines), not deep multi-step
# reasoning, and Haiku is dramatically cheaper per token. Override via
# var.claude_model / CLAUDE_MODEL for a harder incident that genuinely needs
# Sonnet's reasoning depth.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
LOG_WINDOW_MINUTES = int(os.environ.get("LOG_WINDOW_MINUTES", "5"))
REPORT_RETENTION_DAYS = int(os.environ.get("REPORT_RETENTION_DAYS", "400"))
# Token-cost controls -- the dominant cost driver here is input tokens: up to
# 5 services' worth of raw log lines get pasted into one prompt. Capping
# lines-per-service and per-line length bounds worst case regardless of how
# noisy a service's logging gets, without losing the actual point of querying
# all 5 services (cross-service root-cause correlation).
MAX_LOG_LINES_PER_SERVICE = int(os.environ.get("MAX_LOG_LINES_PER_SERVICE", "12"))
MAX_LOG_LINE_CHARS = int(os.environ.get("MAX_LOG_LINE_CHARS", "400"))
CLAUDE_MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "700"))

# Matches the tag Fluent Bit's own LOKI_HOST discovery loop filters on --
# see terraform/modules/eks/node-user-data.sh.tftpl:73 and the monitoring
# EC2's own tags at terraform/modules/monitoring-ec2/main.tf:267.
MONITORING_INSTANCE_TAG_NAME = "bookstore-monitoring"
LOKI_PORT = 3100

SERVICES = ["api-gateway", "catalog-service", "order-service", "user-service", "notification-service"]

_secrets_client = boto3.client("secretsmanager")
_dynamodb = boto3.resource("dynamodb")
_ses = boto3.client("ses")
_ec2 = boto3.client("ec2")

_claude_api_key_cache = None
_loki_host_cache = None


def get_claude_api_key():
    global _claude_api_key_cache
    if _claude_api_key_cache is None:
        response = _secrets_client.get_secret_value(SecretId=CLAUDE_API_KEY_SECRET_ARN)
        _claude_api_key_cache = response["SecretString"]
    return _claude_api_key_cache


def get_loki_host():
    global _loki_host_cache
    if _loki_host_cache is None:
        response = _ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": [MONITORING_INSTANCE_TAG_NAME]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
        _loki_host_cache = response["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    return _loki_host_cache


def query_loki(service, start_ns, end_ns):
    query = f'{{job="eks-containers"}} |= "{service}"'
    # Loki-side fetch cap -- build_prompt only ever uses MAX_LOG_LINES_PER_SERVICE
    # of whatever comes back, so there's no point pulling far more than that
    # (or than write_report's log_references) over the network just to discard it.
    params = urllib.parse.urlencode({"query": query, "start": start_ns, "end": end_ns, "limit": "50"})
    url = f"http://{get_loki_host()}:{LOKI_PORT}/loki/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        body = json.loads(resp.read())
    lines = []
    for stream in body.get("data", {}).get("result", []):
        for _, line in stream.get("values", []):
            lines.append(line)
    return lines


def _truncate_line(line):
    if len(line) <= MAX_LOG_LINE_CHARS:
        return line
    return line[:MAX_LOG_LINE_CHARS] + "...[truncated]"


def build_prompt(alert, logs_by_service):
    sections = [
        f"Alert: {alert['alertname']} on {alert['service']} "
        f"(severity={alert.get('severity', 'unknown')}), fired at {alert['firing_timestamp']}"
    ]
    for service, lines in logs_by_service.items():
        sections.append(f"\n--- {service} logs ---")
        capped = lines[:MAX_LOG_LINES_PER_SERVICE]
        sections.extend([_truncate_line(l) for l in capped] if capped else ["(no logs found for this window)"])
    sections.append(
        "\nBased on the alert and the raw log excerpts above, provide: "
        "1) the likely root cause, 2) which tier/service it originated in, "
        "3) a suggested fix. Be specific and cite the log lines that support your conclusion."
    )
    return "\n".join(sections)


def call_claude(prompt, max_retries=3):
    api_key = get_claude_api_key()
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    last_error = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = json.loads(resp.read())
            return body["content"][0]["text"]
        except Exception as exc:  # noqa: BLE001 -- any transient failure should retry, not just specific ones
            last_error = exc
            logger.warning("Claude API call failed (attempt %d/%d): %s", attempt + 1, max_retries, exc)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise last_error


def send_email(alert, narrative):
    subject = f"[RCA] {alert['alertname']} on {alert['service']}"
    _ses.send_email(
        Source=SES_FROM_EMAIL,
        Destination={"ToAddresses": [SES_TO_EMAIL]},
        Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": narrative}}},
    )


def write_report(alert, narrative, log_references, status):
    table = _dynamodb.Table(DYNAMODB_TABLE)
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={
        "alert_id": alert["alert_id"],
        "report_timestamp": now,
        "gsi_pk": "REPORT",
        "created_at": now,
        "service": alert["service"],
        "alertname": alert["alertname"],
        "severity": alert.get("severity", "unknown"),
        "narrative": narrative,
        "status": status,
        "log_references": log_references,
        # DynamoDB TTL attribute -- see terraform/modules/aiops-rca/dynamodb.tf's
        # ttl block. Reports may carry raw log excerpts, so this is a data-
        # minimization control, not just cost cleanup.
        "expires_at": int(time.time()) + REPORT_RETENTION_DAYS * 86400,
    })


def parse_alertmanager_payload(event):
    body = json.loads(event["body"]) if isinstance(event.get("body"), str) else event
    first = body.get("alerts", [body])[0]
    labels = first.get("labels", {})
    return {
        "alert_id": first.get("fingerprint", labels.get("alertname", "unknown")),
        "alertname": labels.get("alertname", "unknown"),
        "service": labels.get("service", labels.get("job", "unknown")),
        "severity": labels.get("severity", "unknown"),
        "firing_timestamp": first.get("startsAt", datetime.now(timezone.utc).isoformat()),
    }


def handler(event, context):
    alert = parse_alertmanager_payload(event)
    fired_at = datetime.fromisoformat(alert["firing_timestamp"].replace("Z", "+00:00"))
    start_ns = int((fired_at - timedelta(minutes=LOG_WINDOW_MINUTES)).timestamp() * 1e9)
    end_ns = int((fired_at + timedelta(minutes=LOG_WINDOW_MINUTES)).timestamp() * 1e9)

    logs_by_service = {service: query_loki(service, start_ns, end_ns) for service in SERVICES}
    # Same cap as build_prompt's -- the stored report should reflect exactly
    # the evidence Claude actually saw, not a different, larger slice.
    log_references = [line for lines in logs_by_service.values() for line in lines[:MAX_LOG_LINES_PER_SERVICE]]

    if not any(logs_by_service.values()):
        narrative = (
            f"No logs found for {alert['alertname']} in the {2 * LOG_WINDOW_MINUTES}-minute "
            f"window around {alert['firing_timestamp']}."
        )
        write_report(alert, narrative, [], status="no_logs_found")
        send_email(alert, narrative)
        return {"statusCode": 200, "body": json.dumps({"status": "no_logs_found"})}

    narrative = call_claude(build_prompt(alert, logs_by_service))
    write_report(alert, narrative, log_references, status="ok")
    send_email(alert, narrative)
    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}
