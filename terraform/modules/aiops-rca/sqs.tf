# Target for the RCA Lambda's dead_letter_config -- failed invocations (after
# Lambda's own internal retries are exhausted) land here instead of vanishing,
# per the spec's error-handling requirement.
resource "aws_sqs_queue" "rca_dlq" {
  name                      = "bookstore-rca-lambda-dlq"
  message_retention_seconds = 1209600 # 14 days, SQS's max
}
