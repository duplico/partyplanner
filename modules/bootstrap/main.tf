terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "zones" {
  description = "Hosted zone names to create and delegate to (e.g. [\"allhallowtide.party\"])."
  type        = list(string)
  default     = []
}

variable "budget_limit_usd" {
  description = "Monthly cost budget; alerts fire at 80% forecast and 100% actual."
  type        = number
  default     = 10
}

variable "budget_email" {
  description = "Email address for budget alerts."
  type        = string
}

resource "aws_route53_zone" "zones" {
  for_each = toset(var.zones)
  name     = each.key
}

resource "aws_budgets_budget" "monthly" {
  name         = "partyplanner-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "FORECASTED"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_email]
  }
}

output "zone_ids" {
  description = "Map of zone name to hosted zone ID."
  value       = { for name, zone in aws_route53_zone.zones : name => zone.zone_id }
}

output "name_servers" {
  description = "Map of zone name to NS records for delegation at your registrar."
  value       = { for name, zone in aws_route53_zone.zones : name => zone.name_servers }
}
