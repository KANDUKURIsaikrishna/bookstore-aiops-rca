import json
import os

import boto3
import pytest
from moto import mock_aws

os.environ["AWS_DEFAULT_REGION"] = "us-west-1"
os.environ["DYNAMODB_TABLE"] = "rca_reports_test"

import lambda_function  # noqa: E402


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
                {"AttributeName": "gsi_pk", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "by_created_at",
                "KeySchema": [
                    {"AttributeName": "gsi_pk", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        table = boto3.resource("dynamodb", region_name="us-west-1").Table(os.environ["DYNAMODB_TABLE"])
        table.put_item(Item={
            "alert_id": "alert-1", "report_timestamp": "2026-09-12T10:00:00+00:00",
            "gsi_pk": "REPORT", "created_at": "2026-09-12T10:00:00+00:00",
            "service": "order-service", "alertname": "HighErrorRate", "narrative": "first report", "status": "ok",
        })
        table.put_item(Item={
            "alert_id": "alert-2", "report_timestamp": "2026-09-12T11:00:00+00:00",
            "gsi_pk": "REPORT", "created_at": "2026-09-12T11:00:00+00:00",
            "service": "user-service", "alertname": "LoginFailures", "narrative": "second report", "status": "ok",
        })
        yield


def test_list_reports_returns_most_recent_first(dynamodb_table):
    result = lambda_function.handler({"pathParameters": None}, None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert [r["alert_id"] for r in body["reports"]] == ["alert-2", "alert-1"]


def test_get_report_detail_returns_matching_item(dynamodb_table):
    event = {"pathParameters": {"alert_id": "alert-1", "report_timestamp": "2026-09-12T10:00:00+00:00"}}
    result = lambda_function.handler(event, None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 200
    assert body["narrative"] == "first report"


def test_get_report_detail_404s_when_missing(dynamodb_table):
    event = {"pathParameters": {"alert_id": "nope", "report_timestamp": "2026-09-12T10:00:00+00:00"}}
    result = lambda_function.handler(event, None)
    assert result["statusCode"] == 404
