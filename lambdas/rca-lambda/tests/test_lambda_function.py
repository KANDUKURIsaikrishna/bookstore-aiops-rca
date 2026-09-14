import json
import os
from unittest.mock import patch, MagicMock

import boto3
import pytest
from moto import mock_aws

os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["DYNAMODB_TABLE"] = "rca_reports_test"
os.environ["CLAUDE_API_KEY_SECRET_ARN"] = "arn:aws:secretsmanager:us-west-1:123456789012:secret:claude-key"
os.environ["SES_FROM_EMAIL"] = "alerts@example.com"
os.environ["SES_TO_EMAIL"] = "alerts@example.com"
os.environ["CLAUDE_MODEL"] = "claude-sonnet-5"
os.environ["LOG_WINDOW_MINUTES"] = "5"

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
         patch("lambda_function.call_claude", return_value="Root cause: DB timeout in order-service.") as mock_claude, \
         patch("lambda_function._ses") as mock_ses:
        result = lambda_function.handler(make_alertmanager_event(), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"])["status"] == "ok"
    mock_claude.assert_called_once()
    mock_ses.send_email.assert_called_once()

    table = boto3.resource("dynamodb", region_name="us-west-1").Table(os.environ["DYNAMODB_TABLE"])
    items = table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["narrative"] == "Root cause: DB timeout in order-service."
    assert items[0]["status"] == "ok"


def test_call_claude_retries_on_transient_failure():
    success_response = MagicMock()
    success_response.__enter__.return_value.read.return_value = json.dumps(
        {"content": [{"text": "ok"}]}
    ).encode()

    with patch("lambda_function.get_claude_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen") as mock_urlopen, \
         patch("lambda_function.time.sleep"):
        mock_urlopen.side_effect = [Exception("timeout"), success_response]
        result = lambda_function.call_claude("test prompt", max_retries=3)

    assert result == "ok"
    assert mock_urlopen.call_count == 2


def test_call_claude_raises_after_exhausting_retries():
    with patch("lambda_function.get_claude_api_key", return_value="sk-test"), \
         patch("lambda_function.urllib.request.urlopen", side_effect=Exception("down")), \
         patch("lambda_function.time.sleep"):
        with pytest.raises(Exception, match="down"):
            lambda_function.call_claude("test prompt", max_retries=2)


def test_parse_alertmanager_payload_extracts_expected_fields():
    alert = lambda_function.parse_alertmanager_payload(make_alertmanager_event())
    assert alert == {
        "alert_id": "abc123",
        "alertname": "HighErrorRate",
        "service": "order-service",
        "severity": "critical",
        "firing_timestamp": "2026-09-12T10:00:00Z",
    }
