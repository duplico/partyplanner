# Validation harness and usage example for the occasion module.
# The module requires an aliased us-east-1 provider (CloudFront certificates
# must live there), so `terraform validate` has to run against a root like
# this one rather than the module directory itself.

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-2"
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

module "occasion" {
  source = "../../modules/occasion"

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  domain  = "bbq.example.com"
  zone_id = "Z0000000000000000000"
}

output "site_url" {
  value = module.occasion.site_url
}
