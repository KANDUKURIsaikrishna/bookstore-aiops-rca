# PK+SK lets an alert that fires multiple times accumulate multiple reports
# instead of overwriting. The GSI gives the dashboard an ordered "most recent
# reports first" listing via Query instead of an unbounded Scan — every item
# gets the same gsi_pk ("REPORT"), a common single-table-design trick for a
# small, single-purpose table where a real partition key for the GSI isn't
# needed.
resource "aws_dynamodb_table" "rca_reports" {
  name         = "bookstore-rca-reports"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "alert_id"
  range_key    = "report_timestamp"

  attribute {
    name = "alert_id"
    type = "S"
  }

  attribute {
    name = "report_timestamp"
    type = "S"
  }

  attribute {
    name = "gsi_pk"
    type = "S"
  }

  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "by_created_at"
    hash_key        = "gsi_pk"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  # Data-minimization control: reports may carry raw log excerpts (see
  # var.rca_report_retention_days' description). DynamoDB TTL deletes expired
  # items automatically within ~48h of the epoch value stored in
  # `expires_at` -- lambda_function.py's write_report() sets it on every
  # PutItem, computed from this same retention window.
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
