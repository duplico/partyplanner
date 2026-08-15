data "aws_caller_identity" "current" {}

locals {
  # Bounded, collision-free resource name: a readable prefix plus a hash of
  # the full domain. Longest derived name is "${local.bucket_name}" (S3, 63 max).
  name = "${substr(replace(var.domain, ".", "-"), 0, 39)}-${substr(sha256(var.domain), 0, 8)}"
  # S3 bucket names are globally unique across all AWS accounts, so the
  # bucket's hash also covers the account ID — the same domain can then be
  # deployed (e.g. the documented example) from different accounts.
  bucket_name = "${substr(replace(var.domain, ".", "-"), 0, 39)}-${substr(sha256("${data.aws_caller_identity.current.account_id}/${var.domain}"), 0, 8)}-site"
  tags        = merge({ "partyplanner:occasion" = var.domain }, var.tags)
}
