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
SES_FROM_EMAIL = os.environ["SES_FROM_EMAIL"]
SES_TO_EMAIL = os.environ["SES_TO_EMAIL"]
# Provider swap -- LLM_* vars are the generic names; CLAUDE_* are the original
# names and stay as the fallback so existing terraform/config.env deployments
# (which only set CLAUDE_API_KEY_SECRET_ARN/CLAUDE_MODEL/CLAUDE_MAX_TOKENS)
# keep working unchanged with the default provider (anthropic). Set
# LLM_PROVIDER=openai|gemini plus LLM_MODEL and, if the key lives in a
# differently-named secret, LLM_API_KEY_SECRET_ARN to switch providers.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()
LLM_API_KEY_SECRET_ARN = os.environ.get("LLM_API_KEY_SECRET_ARN") or os.environ["CLAUDE_API_KEY_SECRET_ARN"]
# Haiku by default, not Sonnet -- this is a structured summarization task
# (read N log lines, name a root cause, cite the lines), not deep multi-step
# reasoning, and Haiku is dramatically cheaper per token. Override via
# var.claude_model / CLAUDE_MODEL (or LLM_MODEL) for a harder incident that
# genuinely needs deeper reasoning, or when switching providers -- there's no
# cross-provider default that makes sense, so this must be set explicitly
# when LLM_PROVIDER isn't anthropic.
LLM_MODEL = os.environ.get("LLM_MODEL") or os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
LOG_WINDOW_MINUTES = int(os.environ.get("LOG_WINDOW_MINUTES", "5"))
REPORT_RETENTION_DAYS = int(os.environ.get("REPORT_RETENTION_DAYS", "400"))
# Token-cost controls -- the dominant cost driver here is input tokens: up to
# 5 services' worth of raw log lines get pasted into one prompt. Capping
# lines-per-service and per-line length bounds worst case regardless of how
# noisy a service's logging gets, without losing the actual point of querying
# all 5 services (cross-service root-cause correlation).
MAX_LOG_LINES_PER_SERVICE = int(os.environ.get("MAX_LOG_LINES_PER_SERVICE", "12"))
MAX_LOG_LINE_CHARS = int(os.environ.get("MAX_LOG_LINE_CHARS", "400"))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS") or os.environ.get("CLAUDE_MAX_TOKENS", "700"))

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

_llm_api_key_cache = None
_loki_host_cache = None


def get_llm_api_key():
    global _llm_api_key_cache
    if _llm_api_key_cache is None:
        response = _secrets_client.get_secret_value(SecretId=LLM_API_KEY_SECRET_ARN)
        _llm_api_key_cache = response["SecretString"]
    return _llm_api_key_cache


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


def _build_anthropic_request(prompt, api_key):
    payload = json.dumps({
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    return urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )


def _parse_anthropic_response(body):
    return body["content"][0]["text"]


def _build_openai_request(prompt, api_key):
    payload = json.dumps({
        "model": LLM_MODEL,
        "max_tokens": LLM_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    return urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )


def _parse_openai_response(body):
    return body["choices"][0]["message"]["content"]


def _build_gemini_request(prompt, api_key):
    # Gemini takes the key as a query param, not a header -- its REST API has
    # no bearer/x-api-key auth mode.
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": LLM_MAX_TOKENS},
    }).encode()
    url = (
        f"https://generativelanguage.googleapis.com/v1/models/{LLM_MODEL}:generateContent"
        f"?key={api_key}"
    )
    return urllib.request.Request(
        url, data=payload, headers={"content-type": "application/json"}, method="POST"
    )


def _parse_gemini_response(body):
    return body["candidates"][0]["content"]["parts"][0]["text"]


# One entry per supported LLM_PROVIDER value: (request builder, response parser).
# Both differ per-provider (auth scheme, payload shape, response shape) --
# everything else (retry/backoff, error logging) is identical, so only these
# two hooks vary.
_LLM_PROVIDERS = {
    "anthropic": (_build_anthropic_request, _parse_anthropic_response),
    "openai": (_build_openai_request, _parse_openai_response),
    "gemini": (_build_gemini_request, _parse_gemini_response),
}


def call_llm(prompt, max_retries=3):
    try:
        build_request, parse_response = _LLM_PROVIDERS[LLM_PROVIDER]
    except KeyError:
        raise ValueError(
            f"Unsupported LLM_PROVIDER {LLM_PROVIDER!r} (expected one of {sorted(_LLM_PROVIDERS)})"
        ) from None

    request = build_request(prompt, get_llm_api_key())
    last_error = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = json.loads(resp.read())
            return parse_response(body)
        except Exception as exc:  # noqa: BLE001 -- any transient failure should retry, not just specific ones
            last_error = exc
            logger.warning("%s API call failed (attempt %d/%d): %s", LLM_PROVIDER, attempt + 1, max_retries, exc)
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

    narrative = call_llm(build_prompt(alert, logs_by_service))
    write_report(alert, narrative, log_references, status="ok")
    send_email(alert, narrative)
    return {"statusCode": 200, "body": json.dumps({"status": "ok"})}
