locals {
  name = replace(var.domain, ".", "-")
  tags = merge({ "partyplanner:occasion" = var.domain }, var.tags)
}
