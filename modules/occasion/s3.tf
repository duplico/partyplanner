resource "aws_s3_bucket" "site" {
  bucket        = local.bucket_name
  force_destroy = true
  tags          = local.tags
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "site_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }

  # Without ListBucket, S3 answers missing keys with an opaque 403 instead of
  # a 404, which would bypass the CloudFront 404 -> /error.html mapping.
  #
  # ListBucket also means a bare "/" origin request would return a key listing
  # (which would expose invitation-token paths). Two independent distribution
  # settings each keep "/" from ever reaching the origin: the viewer-request
  # function rewrites any "/"-terminated URI to its index.html, and
  # default_root_object covers the root even with the function detached.
  # Keep at least one of them if this grant stays.
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.site.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json
}
