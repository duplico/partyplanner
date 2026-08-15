locals {
  # Bounded, collision-free resource name: a readable prefix plus a hash of
  # the full domain. Longest derived name is "${local.name}-site" (S3, 63 max).
  name = "${substr(replace(var.domain, ".", "-"), 0, 39)}-${substr(sha256(var.domain), 0, 8)}"
  tags = merge({ "partyplanner:occasion" = var.domain }, var.tags)
}
