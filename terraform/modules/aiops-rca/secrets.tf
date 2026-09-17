# A real Claude API key isn't computable from anything Terraform has -- it
# always has to come from a human, unlike the random_password-generated
# secrets elsewhere in this project. Two ways to populate this shell:
#   1. Set CLAUDE_API_KEY in config.env, run `python3 scripts/configure.py`,
#      then `terraform apply` -- var.claude_api_key flows in via
#      terraform.tfvars (gitignored) and the secret_version below is created
#      automatically. Changing the key later is the same two commands again.
#   2. Leave CLAUDE_API_KEY unset and populate by hand instead:
#      `aws secretsmanager put-secret-value --secret-id /bookstore/claude-api-key --secret-string "sk-ant-..."`
# Either way the key never touches git (config.env/terraform.tfvars are both
# gitignored) or `terraform plan`/`apply` console output (var.claude_api_key
# is `sensitive`) -- it does land in Terraform state via path 1, in the same
# encrypted S3 backend every other secret in this project's state already
# sits in (db credentials, jwt secret, grafana admin, ...). That's a
# deliberate, consistent tradeoff, not a new one introduced here.
resource "aws_secretsmanager_secret" "claude_api_key" {
  name                    = "/bookstore/claude-api-key"
  recovery_window_in_days = var.secrets_recovery_window_days
}

resource "aws_secretsmanager_secret_version" "claude_api_key" {
  count         = var.claude_api_key != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.claude_api_key.id
  secret_string = var.claude_api_key
}
