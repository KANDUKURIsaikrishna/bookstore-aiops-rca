resource "aws_security_group" "rca_lambda" {
  name        = "bookstore-rca-lambda-sg"
  description = "RCA Lambda -- outbound only (Loki on monitoring EC2, Claude API, SES, Secrets Manager, DynamoDB, EC2 describe)"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }
}

resource "aws_iam_role" "rca_lambda" {
  name = "bookstore-rca-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Grants ENI create/attach/delete (needed because the function runs in a VPC)
# plus basic CloudWatch Logs permissions -- the standard AWS-managed policy
# for any VPC-attached Lambda.
resource "aws_iam_role_policy_attachment" "rca_lambda_vpc_access" {
  role       = aws_iam_role.rca_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "rca_lambda_inline" {
  name = "bookstore-rca-lambda-inline"
  role = aws_iam_role.rca_lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.llm_api_key.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:Query"]
        Resource = aws_dynamodb_table.rca_reports.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = var.ses_identity_arn
      },
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.rca_dlq.arn
      },
      {
        # Same ec2:DescribeInstances-on-"*" shape as the EKS node role's
        # equivalent grant (terraform/modules/eks/iam.tf:83-94) -- this
        # action doesn't support resource-level ARN restriction.
        Effect   = "Allow"
        Action   = "ec2:DescribeInstances"
        Resource = "*"
      }
    ]
  })
}
