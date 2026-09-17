resource "aws_s3_bucket" "dashboard" {
  bucket = "bookstore-rca-dashboard-${var.account_id}"
}

# Explicit resources rather than relying on AWS's platform-default SSE-S3 --
# an auditor wants evidence of intent in the code, not an assumption about
# an account-level default that could change or differ per account.
resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket                  = aws_s3_bucket.dashboard.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "bookstore-rca-dashboard-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  default_root_object = "index.html"

  origin {
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_id                = "dashboard-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "dashboard-s3"
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.dashboard.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard.arn }
      }
    }]
  })
}

resource "aws_s3_object" "dashboard_index" {
  bucket       = aws_s3_bucket.dashboard.id
  key          = "index.html"
  content_type = "text/html"
  content      = templatefile("${path.module}/../../../dashboard/index.html", { READ_API_URL = aws_apigatewayv2_stage.dashboard_read.invoke_url })
  etag         = md5(templatefile("${path.module}/../../../dashboard/index.html", { READ_API_URL = aws_apigatewayv2_stage.dashboard_read.invoke_url }))
}

resource "aws_s3_object" "dashboard_script" {
  bucket       = aws_s3_bucket.dashboard.id
  key          = "script.js"
  source       = "${path.module}/../../../dashboard/script.js"
  content_type = "application/javascript"
  etag         = filemd5("${path.module}/../../../dashboard/script.js")
}

resource "aws_s3_object" "dashboard_style" {
  bucket       = aws_s3_bucket.dashboard.id
  key          = "style.css"
  source       = "${path.module}/../../../dashboard/style.css"
  content_type = "text/css"
  etag         = filemd5("${path.module}/../../../dashboard/style.css")
}
