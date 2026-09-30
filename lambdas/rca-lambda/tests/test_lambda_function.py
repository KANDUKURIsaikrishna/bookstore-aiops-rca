import json
import os
import time
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
        "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key=AIza-test"
    )
    sent_payload = json.loads(sent_request.data)
    assert sent_payload["contents"] == [{"parts": [{"text": "test prompt"}]}]


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
