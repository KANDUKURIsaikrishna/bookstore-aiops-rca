# AWS account-level audit trail. Deliberately re-added after being removed
# 2026-08-23 alongside CloudTrail/CloudTrail/GuardDuty (see docs/ARCHITECTURE.md's
# "No CloudWatch, CloudTrail, or GuardDuty" note) -- that removal was fine for
# a stack with no external compliance obligation, but leaves zero record of
# who-did-what against the AWS account itself, which is an automatic fail on
# SOC 2 CC7.2 / ISO 27001 A.8.15 / PCI DSS Req 10 the moment either applies.
# See docs/compliance/INFORMATION_SECURITY_POLICY.md and
# docs/TROUBLESHOOTING.md for the incident this re-adds coverage for.
#
# This does NOT re-add VPC Flow Logs, EKS control-plane log export, or RDS
# Enhanced Monitoring -- those were genuinely just cost with no consumer
# (nothing ever read them). CloudTrail is different: it's the only source of
# truth for IAM/API-level activity, which nothing else in this stack (Loki,
# Prometheus) can ever substitute for, no matter how much app-level logging
# improves.

resource "aws_s3_bucket" "cloudtrail" {
  # "-v2" is deliberate, not cosmetic: the original "bookstore-cloudtrail-
  # <account_id>" name is permanently claimed by a bucket orphaned during
  # this project's first live apply/destroy validation (2026-09-17) -- its
  # objects were written while object_lock_enabled defaulted to true below,
  # so that specific bucket can never be deleted before 2027-10-22 (400-day
  # COMPLIANCE-mode retention) no matter what this file now says. Renaming
  # avoids BucketAlreadyOwnedByYou colliding with that stuck bucket forever;
  # it costs nothing (a handful of log objects, pennies/month) and needs no
  # action -- its own lifecycle rule expires it on its own once the lock
  # clears, and it isn't Terraform-managed so it can't block anything here.
  bucket = "bookstore-cloudtrail-${data.aws_caller_identity.current.account_id}-v2"

  # Object Lock must be set at bucket creation -- can't be enabled after the
  # fact without a full bucket recreate. Requires versioning (below). Off by
  # default (var.enable_cloudtrail_object_lock) so this demo/dev-cycle stack
  # can `terraform destroy` this bucket cleanly every time, no orphan, no
  # manual `state rm` -- turn it on for a real audit-scoped deployment,
  # understanding that a real destroy will then permanently orphan this
  # bucket (by design -- see OBS-074) and need a bucket rename of its own.
  object_lock_enabled = var.enable_cloudtrail_object_lock

  # Harmless once object lock is on (AWS blocks the delete regardless of
  # this flag during the retention window, so it's inert there) -- but with
  # the lock off (the default here), this is what actually lets a plain
  # `terraform destroy` remove every object version with no manual cleanup.
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket                  = aws_s3_bucket.cloudtrail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Compliance-mode Object Lock -- not even the account root can delete or
# overwrite a log object before the retention period elapses. This is the
# actual "immutable audit trail" auditors ask for, not just "encrypted and
# access-controlled" (which stops external tampering but not an insider or a
# compromised admin credential from covering their tracks). Off by default
# (var.enable_cloudtrail_object_lock) -- see the bucket resource's own
# comment on the tradeoff. docs/compliance/INFORMATION_SECURITY_POLICY.md's
# cryptography section flags this as an open gap while it's off, same
# pattern as var.enable_rds_secret_rotation.
resource "aws_s3_bucket_object_lock_configuration" "cloudtrail" {
  count  = var.enable_cloudtrail_object_lock ? 1 : 0
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.cloudtrail_retention_days
    }
  }
  depends_on = [aws_s3_bucket_versioning.cloudtrail]
}

# With the lock off (default), this is just ordinary cost-control cleanup --
# `force_destroy` on the bucket resource is what actually empties it on a
# real `terraform destroy`. With the lock on, S3 silently defers (not fails)
# any lifecycle expiration attempted while an object is still locked; the
# 35-day buffer over the lock period avoids the delete and the lock
# expiring on the exact same day.
resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    id     = "expire-after-retention-plus-buffer"
    status = "Enabled"
    filter {}
    expiration {
      days = var.cloudtrail_retention_days + 35
    }
    noncurrent_version_expiration {
      noncurrent_days = var.cloudtrail_retention_days + 35
    }
  }
  depends_on = [aws_s3_bucket_versioning.cloudtrail]
}

data "aws_iam_policy_document" "cloudtrail_bucket" {
  statement {
    sid    = "AWSCloudTrailAclCheck"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]
  }

  statement {
    sid    = "AWSCloudTrailWrite"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail_bucket.json
}

resource "aws_cloudtrail" "org" {
  name                          = "bookstore-audit-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}
