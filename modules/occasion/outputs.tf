output "site_url" {
  description = "Base URL of the occasion site."
  value       = "https://${var.domain}/"
}

output "bucket" {
  description = "S3 bucket for rendered site assets (sync `out/site` here)."
  value       = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  description = "CloudFront distribution ID (for invalidations)."
  value       = aws_cloudfront_distribution.site.id
}

output "table_name" {
  description = "DynamoDB table name (for `partyplanner sync-links` / `export-rsvps`)."
  value       = aws_dynamodb_table.occasion.name
}
