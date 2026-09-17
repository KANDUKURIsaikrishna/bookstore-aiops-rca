# Off by default (var.enable_rds_secret_rotation) -- see
# docs/compliance/INFORMATION_SECURITY_POLICY.md's cryptography section and
# the compliance gap report's CRYPTO-1 finding: module.rds has always
# supported rotation_lambda_arn/rotation_days, but nothing ever deployed a
# rotation Lambda to populate it, so /bookstore/db-credentials has never
# actually rotated on a schedule.
#
# Deploys AWS's own officially published single-user MySQL rotation Lambda
# from the Serverless Application Repository (account 297356227824 is AWS's
# own SAR publisher account for the Secrets Manager rotation function
# templates -- not a third-party app). semantic_version is left unset so
# Terraform resolves the latest published version; that also means this
# WILL pick up a newer version's parameter schema over time, so before
# first enabling this in a real account, confirm the current required
# `parameters` keys against the live app:
#   aws serverlessrepo get-application \
#     --application-id arn:aws:serverlessrepo:us-east-1:297356227824:applications/SecretsManagerRDSMySQLRotationSingleUser
# This block was written and validated offline (no AWS account available in
# this session) -- treat the parameter keys below as best-effort against
# AWS's long-documented schema for this app, not as verified against a live
# lookup.

resource "aws_security_group" "rds_rotation_lambda" {
  count       = var.enable_rds_secret_rotation ? 1 : 0
  name        = "bookstore-rds-rotation-lambda-sg"
  description = "RDS credential rotation Lambda -- outbound only (RDS on 3306, Secrets Manager API)"
  vpc_id      = module.network.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }
}

# Same pattern as terraform/modules/aiops-rca/iam.tf's rca_lambda SG rule --
# a standalone security_group_rule referencing the security module's output,
# rather than modifying modules/security itself, so RDS's SG definition
# stays owned by one module.
resource "aws_security_group_rule" "rds_mysql_from_rotation_lambda" {
  count                    = var.enable_rds_secret_rotation ? 1 : 0
  type                     = "ingress"
  from_port                = 3306
  to_port                  = 3306
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.rds_rotation_lambda[0].id
  security_group_id        = module.security_groups.rds_sg_id
  description              = "MySQL from the Secrets Manager rotation Lambda"
}

resource "aws_serverlessapplicationrepository_cloudformation_stack" "rds_rotation" {
  count          = var.enable_rds_secret_rotation ? 1 : 0
  name           = "bookstore-rds-rotation"
  application_id = "arn:aws:serverlessrepo:us-east-1:297356227824:applications/SecretsManagerRDSMySQLRotationSingleUser"
  capabilities   = ["CAPABILITY_IAM", "CAPABILITY_RESOURCE_POLICY"]

  parameters = {
    functionName        = "bookstore-rds-rotation-single-user"
    endpoint            = "https://secretsmanager.${var.aws_region}.amazonaws.com"
    vpcSecurityGroupIds = aws_security_group.rds_rotation_lambda[0].id
    vpcSubnetIds        = join(",", [module.network.private_subnet_ids[4], module.network.private_subnet_ids[5]])
  }
}
