import io
import json
import os
import time
import urllib.error
from unittest.mock import patch, MagicMock

import boto3
import pytest
from moto import mock_aws

os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["DYNAMODB_TABLE"] = "rca_reports_test"
os.environ["LLM_API_KEY_SECRET_ARN"] = "arn:aws:secretsmanager:us-west-1:123456789012:secret:llm-key"
os.environ["SES_FROM_EMAIL"] = "alerts@example.com"
os.environ["SES_TO_EMAIL"] = "alerts@example.com"
os.environ["CLAUDE_MODEL"] = "claude-sonnet-5"
os.environ["LOG_WINDOW_MINUTES"] = "5"
os.environ["REPORT_RETENTION_DAYS"] = "400"
os.environ["MAX_LOG_LINES_PER_SERVICE"] = "12"
os.environ["MAX_LOG_LINE_CHARS"] = "400"
os.environ["CLAUDE_MAX_TOKENS"] = "700"

import lambda_function  # noqa: E402 -- env vars above must be set before import


@pytest.fixture
def dynamodb_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-west-1")
        client.create_table(
            TableName=os.environ["DYNAMODB_TABLE"],
            KeySchema=[
                {"AttributeName": "alert_id", "KeyType": "HASH"},
                {"AttributeName": "report_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "alert_id", "AttributeType": "S"},
                {"AttributeName": "report_timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def make_alertmanager_event():
    return {
        "body": json.dumps({
            "alerts": [{
                "fingerprint": "abc123",
                "startsAt": "2026-09-12T10:00:00Z",
                "labels": {"alertname": "HighErrorRate", "service": "order-service", "severity": "critical"},
            }]
        })
    }


def make_pod_level_alertmanager_event(pod="argocd-application-controller-0", alertname="HighPodCPUUsage"):
    # PodCrashLooping / HighPodCPUUsage / HighPodMemoryUsage all carry
    # namespace+pod labels from kube-state-metrics/cAdvisor, never a
    # "service" label -- they can fire on ANY pod in the cluster, not just
    # one of the 5 named bookstore microservices (confirmed live
    # 2026-10-01: HighPodCPUUsage fired on ArgoCD's own controller, which
    # was busy-looping on a failed sync -- "no logs found" resulted,
    # because the old fixed-SERVICES-list search had no way to look for
    # that pod's own name).
    return {
        "body": json.dumps({
            "alerts": [{
                "fingerprint": "pod-level-123",
                "startsAt": "2026-09-12T10:00:00Z",
                "labels": {"alertname": alertname, "namespace": "argocd", "pod": pod, "severity": "warning"},
            }]
        })
    }


def test_parse_alertmanager_payload_extracts_pod_label():
    alert = lambda_function.parse_alertmanager_payload(make_pod_level_alertmanager_event())
    assert alert["pod"] == "argocd-application-controller-0"


def test_parse_alertmanager_payload_pod_defaults_empty_when_absent():
    alert = lambda_function.parse_alertmanager_payload(make_alertmanager_event())
    assert alert["pod"] == ""


def test_log_search_targets_includes_pod_name_alongside_the_known_services():
    alert = {"service": "unknown", "pod": "argocd-application-controller-0"}
    targets = lambda_function._log_search_targets(alert)
    assert "argocd-application-controller-0" in targets
    assert set(lambda_function.SERVICES).issubset(set(targets))


def test_log_search_targets_is_just_the_known_services_when_no_pod_label():
    alert = {"service": "order-service", "pod": ""}
    assert lambda_function._log_search_targets(alert) == list(lambda_function.SERVICES)


def test_handler_finds_logs_by_pod_name_for_alerts_with_no_service_label(dynamodb_table):
    # The alert's own pod name must be searched, not just the 5 bookstore
    # service names -- otherwise an alert on any non-bookstore pod (ArgoCD,
    # the AWS Load Balancer Controller, kube-system, ...) always produces
    # "no logs found" even when that pod's own logs explain exactly what
    # happened.
    def fake_query_loki(target, *_):
        if target == "argocd-application-controller-0":
            return ['level=warning msg="Skipping auto-sync: failed previous sync attempt"']
        return []

    with patch("lambda_function.query_loki", side_effect=fake_query_loki), \
         patch("lambda_function.call_llm", return_value="Root cause: ArgoCD sync lockout.") as mock_llm, \
         patch("lambda_function._ses") as mock_ses:
        result = lambda_function.handler(make_pod_level_alertmanager_event(), None)

    assert json.loads(result["body"])["status"] == "ok"
    mock_llm.assert_called_once()
    mock_ses.send_email.assert_called_once()


def test_handler_writes_no_logs_report_when_loki_returns_nothing(dynamodb_table):
    with patch("lambda_function.query_loki", return_value=[]), \
         patch("lambda_function._ses") as mock_ses:
        result = lambda_function.handler(make_alertmanager_event(), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == "no_logs_found"
    mock_ses.send_email.assert_called_once()

    table = boto3.resource("dynamodb", region_name="us-west-1").Table(os.environ["DYNAMODB_TABLE"])
    items = table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["status"] == "no_logs_found"


def test_handler_calls_claude_and_writes_report_when_logs_exist(dynamodb_table):
    fake_logs = {"order-service": ['{"level":"error","message":"DB timeout"}']}
    fake_logs.update({s: [] for s in lambda_function.SERVICES if s != "order-service"})

    with patch("lambda_function.query_loki", side_effect=lambda service, *_: fake_logs[service]), \
         patch("lambda_function.call_llm", return_value="Root cause: DB timeout in order-service.") as mock_llm, \
         patch("lambda_function._ses") as mock_ses:
        result = lambda_function.handler(make_alertmanager_event(), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == "ok"
    mock_llm.assert_called_once()
    mock_ses.send_email.assert_called_once()

    table = boto3.resource("dynamodb", region_name="us-west-1").Table(os.environ["DYNAMODB_TABLE"])
    items = table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["narrative"] == "Root cause: DB timeout in order-service."
    assert items[0]["status"] == "ok"


def test_handler_skips_reanalysis_when_alert_already_has_an_ok_report(dynamodb_table):
    # Alertmanager re-POSTs the same still-firing alert (same fingerprint)
    # repeatedly until a webhook call succeeds, then again on every
    # subsequent retry cycle if something downstream keeps failing --
    # confirmed live: one flapping alert produced ~30 Gemini calls in 11
    # minutes before the actual bug was fixed, because every retry re-ran
    # the full Loki-query + LLM-call pipeline from scratch. Once an alert_id
    # already has a successful ("ok") report, a duplicate notification for
    # the exact same alert shouldn't re-spend a Loki query + an LLM call --
    # Alertmanager's own email already tells a human it's still firing.
    lambda_function.write_report(
        {"alert_id": "abc123", "service": "order-service", "alertname": "HighErrorRate"},
        "Root cause: DB timeout in order-service.", [], "ok",
    )

    with patch("lambda_function.query_loki") as mock_query_loki, \
         patch("lambda_function.call_llm") as mock_llm, \
         patch("lambda_function._ses") as mock_ses:
        result = lambda_function.handler(make_alertmanager_event(), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == "skipped_duplicate"
    mock_query_loki.assert_not_called()
    mock_llm.assert_not_called()
    mock_ses.send_email.assert_not_called()

    table = boto3.resource("dynamodb", region_name="us-west-1").Table(os.environ["DYNAMODB_TABLE"])
    assert len(table.scan()["Items"]) == 1  # the seeded report only -- no duplicate written


def test_write_report_sets_ttl_from_retention_window(dynamodb_table):
    before = int(time.time()) + 400 * 86400
    lambda_function.write_report(
        {"alert_id": "ttl-test", "service": "order-service", "alertname": "HighErrorRate"},
        "narrative", [], "ok",
    )
    after = int(time.time()) + 400 * 86400

    table = boto3.resource("dynamodb", region_name="us-west-1").Table(os.environ["DYNAMODB_TABLE"])
    item = table.scan()["Items"][0]
    assert before <= item["expires_at"] <= after


def test_build_prompt_caps_lines_per_service_to_control_token_cost():
    logs_by_service = {"order-service": [f"line-{i}" for i in range(50)]}
    prompt = lambda_function.build_prompt(
        {"alertname": "HighErrorRate", "service": "order-service", "severity": "critical", "firing_timestamp": "t"},
        logs_by_service,
    )
    assert prompt.count("line-") == 12  # MAX_LOG_LINES_PER_SERVICE, not all 50
    assert "line-11" in prompt
    assert "line-12" not in prompt


def test_build_prompt_truncates_long_log_lines_to_control_token_cost():
    long_line = "x" * 1000
    prompt = lambda_function.build_prompt(
        {"alertname": "HighErrorRate", "service": "order-service", "severity": "critical", "firing_timestamp": "t"},
        {"order-service": [long_line]},
    )
    included = [l for l in prompt.splitlines() if l.startswith("x")][0]
    assert len(included) <= 400 + len("...[truncated]")
    assert included.endswith("...[truncated]")


def test_call_llm_uses_configured_max_tokens_to_control_output_cost():
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"text": "ok"}]}
    ).encode()

    with patch("lambda_function.get_llm_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen", return_value=success_response) as mock_urlopen:
        lambda_function.call_llm("test prompt")

    sent_payload = json.loads(mock_urlopen.call_args[0][0].data)
    assert sent_payload["max_tokens"] == 700


def test_call_llm_retries_on_transient_failure():
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"text": "ok"}]}
    ).encode()

    with patch("lambda_function.get_llm_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen") as mock_urlopen, \
         patch("lambda_function.time.sleep"):
        mock_urlopen.side_effect = [Exception("timeout"), success_response]
        result = lambda_function.call_llm("test prompt", max_retries=3)

    assert result == "ok"
    assert mock_urlopen.call_count == 2


def test_call_llm_raises_after_exhausting_retries():
    with patch("lambda_function.get_llm_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen", side_effect=Exception("down")), \
         patch("lambda_function.time.sleep"):
        with pytest.raises(Exception, match="down"):
            lambda_function.call_llm("test prompt", max_retries=2)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 503])
def test_call_llm_does_not_retry_on_permanent_or_overload_failures(status_code):
    # Retrying immediately on 429 (rate limited) or 503 (overloaded) only adds
    # more requests to an already-throttled key -- confirmed live (2026-10-01
    # chaos/RCA drill): Alertmanager's own retry-on-failure loop already
    # re-invokes every ~70-90s on failure, and this handler's blind 3x retry
    # on top of that turned one flapping alert into ~30 Gemini calls in 11
    # minutes, burning a free-tier quota outright. Fail fast on these two
    # codes instead -- let Alertmanager's much slower retry cadence be the
    # only retry.
    http_error = urllib.error.HTTPError(
        "http://example.com", status_code, "err", {}, io.BytesIO(b'{"error": "rate limited"}')
    )
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"text": "ok"}]}
    ).encode()

    with patch("lambda_function.get_llm_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen") as mock_urlopen, \
         patch("lambda_function.time.sleep") as mock_sleep:
        mock_urlopen.side_effect = [http_error, success_response]
        with pytest.raises(urllib.error.HTTPError):
            lambda_function.call_llm("test prompt", max_retries=3)

    assert mock_urlopen.call_count == 1
    mock_sleep.assert_not_called()


def test_call_llm_still_retries_on_other_transient_errors():
    # Genuine transient failures (network blips, non-rate-limit 5xx) should
    # still get the short in-invocation retry -- only 429/503 skip it.
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"text": "ok"}]}
    ).encode()
    http_error = urllib.error.HTTPError(
        "http://example.com", 500, "err", {}, io.BytesIO(b'{"error": "internal"}')
    )

    with patch("lambda_function.get_llm_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen") as mock_urlopen, \
         patch("lambda_function.time.sleep"):
        mock_urlopen.side_effect = [http_error, success_response]
        result = lambda_function.call_llm("test prompt", max_retries=3)

    assert result == "ok"
    assert mock_urlopen.call_count == 2


def test_call_llm_dispatches_to_anthropic_by_default():
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"text": "anthropic reply"}]}
    ).encode()

    with patch("lambda_function.get_llm_api_key", return_value="sk-ant-test"), \
         patch("lambda_function.urllib.request.urlopen", return_value=success_response) as mock_urlopen:
        result = lambda_function.call_llm("test prompt")

    assert result == "anthropic reply"
    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == "https://api.anthropic.com/v1/messages"
    assert sent_request.headers["X-api-key"] == "sk-ant-test"


def test_call_llm_dispatches_to_openai():
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"choices": [{"message": {"content": "openai reply"}}]}
    ).encode()

    with patch("lambda_function.LLM_PROVIDER", "openai"), \
         patch("lambda_function.get_llm_api_key", return_value="sk-oai-test"), \
         patch("lambda_function.urllib.request.urlopen", return_value=success_response) as mock_urlopen:
        result = lambda_function.call_llm("test prompt")

    assert result == "openai reply"
    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == "https://api.openai.com/v1/chat/completions"
    assert sent_request.headers["Authorization"] == "Bearer sk-oai-test"
    sent_payload = json.loads(sent_request.data)
    assert sent_payload["messages"] == [{"role": "user", "content": "test prompt"}]


def test_call_llm_dispatches_to_gemini():
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "gemini reply"}]}}]}
    ).encode()

    with patch("lambda_function.LLM_PROVIDER", "gemini"), \
         patch("lambda_function.LLM_MODEL", "gemini-2.5-flash"), \
         patch("lambda_function.get_llm_api_key", return_value="AIza-test"), \
         patch("lambda_function.urllib.request.urlopen", return_value=success_response) as mock_urlopen:
        result = lambda_function.call_llm("test prompt")

    assert result == "gemini reply"
    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=AIza-test"
    )
    sent_payload = json.loads(sent_request.data)
    assert sent_payload["contents"] == [{"parts": [{"text": "test prompt"}]}]
    # No thinkingConfig -- some Gemini variants (e.g. "lite" ones) reject it
    # outright with a 400 INVALID_ARGUMENT. Confirmed live 2026-10-01.
    assert "thinkingConfig" not in sent_payload["generationConfig"]


def test_parse_gemini_response_skips_thought_parts():
    # Thinking-capable Gemini models can return internal reasoning as a
    # separate part marked "thought": true, before the real answer part.
    # Confirmed live (2026-10-01): reading parts[0] alone returned a
    # 54-character mid-reasoning fragment, not an answer.
    body = {
        "candidates": [{
            "content": {
                "parts": [
                    {"thought": True, "text": "Let me think about this..."},
                    {"text": "Root cause: DB timeout in order-service."},
                ]
            }
        }]
    }
    assert lambda_function._parse_gemini_response(body) == "Root cause: DB timeout in order-service."


def test_parse_gemini_response_handles_single_plain_part():
    # Non-thinking models (e.g. "lite" variants) never set "thought" at
    # all -- the filter must be a no-op for them, not break the common case.
    body = {"candidates": [{"content": {"parts": [{"text": "plain answer"}]}}]}
    assert lambda_function._parse_gemini_response(body) == "plain answer"


def test_call_llm_raises_on_unsupported_provider():
    with patch("lambda_function.LLM_PROVIDER", "not-a-real-provider"):
        with pytest.raises(ValueError, match="not-a-real-provider"):
            lambda_function.call_llm("test prompt")


def test_parse_alertmanager_payload_extracts_expected_fields():
    alert = lambda_function.parse_alertmanager_payload(make_alertmanager_event())
    assert alert == {
        "alert_id": "abc123",
        "alertname": "HighErrorRate",
        "service": "order-service",
        "pod": "",
        "severity": "critical",
        "firing_timestamp": "2026-09-12T10:00:00Z",
    }


def test_legacy_claude_api_key_secret_arn_still_works_without_llm_api_key_secret_arn():
    # Deployments that only ever set CLAUDE_API_KEY_SECRET_ARN (pre-rename)
    # must keep working without a terraform/config change -- LLM_API_KEY_SECRET_ARN
    # is additive, not a breaking rename.
    import importlib

    env_backup = dict(os.environ)
    try:
        del os.environ["LLM_API_KEY_SECRET_ARN"]
        os.environ["CLAUDE_API_KEY_SECRET_ARN"] = "arn:aws:secretsmanager:us-west-1:123456789012:secret:legacy-key"
        reloaded = importlib.reload(lambda_function)
        assert reloaded.LLM_API_KEY_SECRET_ARN == (
            "arn:aws:secretsmanager:us-west-1:123456789012:secret:legacy-key"
        )
    finally:
        os.environ.clear()
        os.environ.update(env_backup)
        importlib.reload(lambda_function)
