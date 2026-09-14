import json
import os

import boto3
from boto3.dynamodb.conditions import Key

DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
_dynamodb = boto3.resource("dynamodb")


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    table = _dynamodb.Table(DYNAMODB_TABLE)
    path_params = event.get("pathParameters") or {}

    if path_params.get("alert_id") and path_params.get("report_timestamp"):
        item = table.get_item(Key={
            "alert_id": path_params["alert_id"],
            "report_timestamp": path_params["report_timestamp"],
        }).get("Item")
        if not item:
            return _response(404, {"error": "report not found"})
        return _response(200, item)

    result = table.query(
        IndexName="by_created_at",
        KeyConditionExpression=Key("gsi_pk").eq("REPORT"),
        ScanIndexForward=False,
        Limit=50,
    )
    return _response(200, {"reports": result.get("Items", [])})
