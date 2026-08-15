# Bootstrapping an AWS account for partyplanner

One-time setup, done once per AWS account — after this, deploying an occasion
is just a merge in your (private) events repo. Five steps:

1. [Terraform state bucket](#1-terraform-state-bucket)
2. [Hosted zones + budget alarm (`modules/bootstrap`)](#2-hosted-zones--budget-alarm)
3. [Delegate DNS at your registrar](#3-delegate-dns)
4. [GitHub Actions OIDC role](#4-github-actions-oidc-role)
5. [Consumer repo wiring](#5-consumer-repo-wiring)

Prerequisites: an AWS account, admin-ish credentials on your workstation for
this one-time setup (`aws sts get-caller-identity` works), Terraform >= 1.5,
and the domain(s) you plan to use.

## 1. Terraform state bucket

Everything else is Terraform, so the state bucket is the one hand-made thing.
Versioned, private, one per account. Set the name once and the rest of the
block pastes as-is:

```bash
export TF_STATE_BUCKET=your-tf-state-bucket

aws s3api create-bucket --bucket "$TF_STATE_BUCKET" --region us-east-1
aws s3api put-bucket-versioning --bucket "$TF_STATE_BUCKET" \
    --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "$TF_STATE_BUCKET" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

(For regions other than `us-east-1`, add
`--create-bucket-configuration LocationConstraint=REGION` to the first
command.)

No DynamoDB lock table is needed: the reusable deploy/destroy workflows
serialize runs per occasion with a GitHub Actions concurrency group, and each
occasion uses its own state key.

## 2. Hosted zones + budget alarm

`modules/bootstrap` creates a Route53 hosted zone per domain and a monthly
cost budget with email alerts (80% forecast, 100% actual). Put this root
module in your events repo (e.g. `bootstrap/main.tf`) and apply it once from
your workstation:

```hcl
terraform {
  backend "s3" {
    # bucket comes from `terraform init -backend-config="bucket=$TF_STATE_BUCKET"`
    key    = "bootstrap/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = "us-east-1"
}

module "bootstrap" {
  source           = "github.com/duplico/partyplanner//modules/bootstrap?ref=default"
  zones            = ["allhallowtide.party", "events.example.com"]
  budget_email     = "you@example.com"
  budget_limit_usd = 10
}

output "zone_ids" {
  value = module.bootstrap.zone_ids
}

output "name_servers" {
  value = module.bootstrap.name_servers
}
```

Terraform backend blocks can't read environment variables, so pass the bucket
at init time instead of hardcoding it:

```bash
terraform init -backend-config="bucket=$TF_STATE_BUCKET" && terraform apply
```

Note the outputs: `zone_ids` feeds each occasion's `zone_id`, and
`name_servers` is what you delegate to next. Hosted zones cost ~$0.50/month
each — the only always-on cost in the system.

## 3. Delegate DNS

For each zone, create NS records at your registrar (or parent zone) pointing
at the four name servers from the `name_servers` output. For a subdomain zone
like `events.example.com`, that's an NS record set *in the parent domain's
DNS*, not a registrar change.

Verify before the first deploy — ACM cert validation hangs until delegation
works:

```bash
dig +short NS allhallowtide.party
```

## 4. GitHub Actions OIDC role

The deploy workflow prefers OIDC (no long-lived keys in GitHub). Add this to
the same bootstrap root and re-apply, substituting your events repo:

```hcl
data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_iam_role" "deploy" {
  name = "partyplanner-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # Only workflows running on the default branch may assume the role —
          # never pull-request jobs or other refs.
          "token.actions.githubusercontent.com:sub" = "repo:YOUR-ORG/YOUR-EVENTS-REPO:ref:refs/heads/default"
        }
      }
    }]
  })
}

# The occasion module spans S3/CloudFront/ACM/Route53/API GW/Lambda/DynamoDB
# and creates the Lambda's execution role, so the deploy role is broad. It is
# still confined to one repo's default branch via the OIDC subject condition
# above; scope it down further once your resource-naming conventions settle.
resource "aws_iam_role_policy" "deploy" {
  name = "partyplanner-deploy"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:*",
          "cloudfront:*",
          "acm:*",
          "route53:*",
          "apigateway:*",
          "lambda:*",
          "dynamodb:*",
          "logs:*",
          "iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:TagRole",
          "iam:PassRole", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies",
          "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
          "iam:ListInstanceProfilesForRole",
        ]
        Resource = "*"
      },
    ]
  })
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}
```

If you'd rather not use OIDC, the workflows also accept
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` as secrets — but prefer the role.

## 5. Consumer repo wiring

Layout in your private events repo, one directory per occasion:

```
bootstrap/                    # step 2 + 4 root module
occasions/
  bbq-2026/
    occasion.yaml             # minted (ids + tokens committed)
    assets/…                  # photos referenced by the config
    terraform/main.tf         # root module below
.github/workflows/deploy-bbq-2026.yml
.github/workflows/destroy-bbq-2026.yml
```

`occasions/bbq-2026/terraform/main.tf` — its own state key, and the
`us-east-1` provider alias CloudFront certs require:

```hcl
terraform {
  backend "s3" {
    # bucket comes from the workflows' tf_state_bucket input (or
    # `terraform init -backend-config="bucket=$TF_STATE_BUCKET"` locally)
    key    = "occasions/bbq-2026/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = "us-east-1"
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

module "occasion" {
  source = "github.com/duplico/partyplanner//modules/occasion?ref=default"
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
  domain  = "bbq-2026.events.example.com"
  zone_id = "Z0123456789EXAMPLE" # from bootstrap zone_ids
}

output "bucket" { value = module.occasion.bucket }
output "distribution_id" { value = module.occasion.distribution_id }
output "table_name" { value = module.occasion.table_name }
output "site_url" { value = module.occasion.site_url }
```

Deploy workflow (merge to your default branch = deploy):

```yaml
name: deploy-bbq-2026
on:
  push:
    branches: [default]
    paths: ["occasions/bbq-2026/**"]

permissions:
  contents: read
  id-token: write

jobs:
  deploy:
    uses: duplico/partyplanner/.github/workflows/deploy-occasion.yml@default
    with:
      occasion_dir: occasions/bbq-2026
      role_to_assume: arn:aws:iam::ACCOUNT_ID:role/partyplanner-deploy
      tf_state_bucket: your-tf-state-bucket
```

Destroy workflow — `workflow_dispatch` only, never wired to config deletion.
It exports a final RSVP snapshot artifact before tearing the capsule down:

```yaml
name: destroy-bbq-2026
on: workflow_dispatch

permissions:
  contents: read
  id-token: write

jobs:
  destroy:
    uses: duplico/partyplanner/.github/workflows/destroy-occasion.yml@default
    with:
      occasion_dir: occasions/bbq-2026
      role_to_assume: arn:aws:iam::ACCOUNT_ID:role/partyplanner-deploy
      tf_state_bucket: your-tf-state-bucket
```

Pin `@default` (and `?ref=default`) while iterating; switch both to a release
tag once things settle.

### First deploy checklist

- [ ] `partyplanner mint occasions/bbq-2026/occasion.yaml` run locally and the
      result committed (ids + tokens in the YAML — deploys never rotate links)
- [ ] `zone_id` in `terraform/main.tf` matches the occasion's domain
- [ ] DNS delegation verified (`dig +short NS <zone>`)
- [ ] Merge; watch the run, then grab invitation URLs from the `links-*`
      artifact (or `partyplanner links` locally)
