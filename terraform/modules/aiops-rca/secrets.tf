# Shell only -- see this plan's "Notes for the executing engineer" section
# for the one manual step needed to actually populate this with a real key.
# Same shell/no-version-here split as the existing alertmanager_smtp secret
# (terraform/main.tf:246-250), but there's no equivalent of that secret's
# null_resource derivation step here: a real Claude API key isn't computable
# from anything Terraform has, it has to be pasted in by a human.
resource "aws_secretsmanager_secret" "claude_api_key" {
  name                    = "/bookstore/claude-api-key"
  recovery_window_in_days = 0
}
