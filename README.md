# partyplanner

Config-first, serverless invitation sites. Write one YAML file describing an
**occasion** (a party, or a whole weekend of them), mint unguessable invitation
links, and deploy a static site with live, socially-visible RSVPs — on your own
AWS account, with ~zero idle cost, no accounts, and no admin UI.

Born from a multi-day Halloween tradition; degenerates cleanly to "BBQ Saturday:
photo, blurb, time, place, link in the group chat, done."

## How it works

- An **occasion** is one self-contained deployment: config file + static site +
  DynamoDB table. Occasions share nothing; retire them independently.
- An occasion contains **events** (dinner, bar crawl, candy crew, main party…),
  each with a time, place, blurb, and an RSVP list (or `rsvp: none` for
  informational cards like a live stream link).
- A **link** is the unit of invitation: an unguessable URL whose **scope** is a
  subset of the occasion's events. The link is both the invitation and the
  credential — no logins. Forward it freely; recipients self-identify by name.
  Optionally personalize a link so it prefills someone's name.
- RSVPs (name, yes/maybe/no, +N guests) are visible to everyone who can see the
  page — seeing who's coming drives attendance, and it's also how hosts read
  the list.
- Every dated event gets an **ICS file** ("add to calendar"), and pages carry
  OpenGraph tags so the link unfurl in Signal/Discord/iMessage looks like an
  invitation, not a bare URL.

## Quick start

```console
$ uv tool install "partyplanner @ git+https://github.com/duplico/partyplanner"
$ cat occasion.yaml
```

```yaml
title: BBQ Saturday
domain: bbq-2026.events.example.com
timezone: America/Chicago
photo: ./assets/bbq.jpg
blurb: |
  Burgers, yard games, bring a chair. Kids and dogs welcome.
events:
  - title: BBQ
    when: 2026-06-20 15:00
    where: 123 Example Ave
links:
  - note: group chat link, forward freely
```

```console
$ partyplanner mint occasion.yaml      # writes ids + tokens back into the file
event 'BBQ': id = bbq
link 'group chat link, forward freely': token = k7f3q2vmxw4tzr6ehb2a
$ partyplanner render occasion.yaml    # out/site/ + out/links.csv
$ partyplanner links occasion.yaml
group chat link, forward freely	https://bbq-2026.events.example.com/i/k7f3q2vmxw4tzr6ehb2a/
```

See `fixtures/allhallowtide/` for the maximal example: six events, scoped
links, a landing-page stream embed, per-occasion CSS overrides, and a revoked
token.

## CLI

| command | what it does |
| --- | --- |
| `partyplanner validate <config>` | validate an occasion config |
| `partyplanner mint <config>` | fill in missing event ids and link tokens (writes back, preserves comments) |
| `partyplanner render <config> --out out` | render static site, ICS files, and `links.csv` |
| `partyplanner links <config>` | print invitation URLs |
| `partyplanner sync-links <config> --table <name>` | upsert link records to DynamoDB; delete revoked |
| `partyplanner export-rsvps --table <name>` | dump all RSVPs as CSV |
| `partyplanner scaffold bootstrap ...` | generate the events-repo bootstrap Terraform root (see [docs/bootstrapping.md](docs/bootstrapping.md)) |
| `partyplanner scaffold occasion <name> ...` | generate an occasion capsule (config stub, Terraform root, workflows) |

`mint` is a local, committed step: `render` refuses to run with missing ids or
tokens so that CI deploys are deterministic and **redeploys never rotate
links**. To force-rotate a leaked link, move its token to `revoked:`, delete it
from the link entry, and redeploy — the old URL 404s and a replacement is
minted.

## Architecture

Static-first: the renderer pre-builds one page per distinct link scope; RSVP
data is the only dynamic thing, fetched client-side from a single Lambda.

```
CloudFront ── /            S3 (landing, per-token pages, assets, ICS, robots.txt)
          └── /api/*       API Gateway (throttled) → Lambda → DynamoDB (on-demand)
Route53 + ACM              per-occasion subdomain, DNS-validated cert
```

Idle cost ≈ $0 (plus ~$0.50/mo per Route53 hosted zone). Guardrails: API
throttling, reserved Lambda concurrency, request validation, and a budget alarm
in the bootstrap module.

### Terraform

- `modules/occasion` — everything one occasion needs. Instantiate it per
  occasion (requires an `aws.us_east_1` provider alias for the CloudFront
  cert):

  ```hcl
  module "occasion" {
    source = "github.com/duplico/partyplanner//modules/occasion?ref=v0.1.0"
    providers = {
      aws           = aws
      aws.us_east_1 = aws.us_east_1
    }
    domain  = "bbq-2026.events.example.com"
    zone_id = "Z0123456789EXAMPLE"
  }
  ```

- `modules/bootstrap` — run once per AWS account: hosted zones (delegate your
  domains to the emitted name servers) and a monthly budget alarm. See
  [docs/bootstrapping.md](docs/bootstrapping.md) for the full account setup
  playbook (state bucket, zones, DNS delegation, OIDC role, consumer repo
  wiring).

### Deploying from a consumer repo

Keep real occasion configs (addresses, guest names, live tokens) in a
**private** repo, and call the reusable workflows from it:

```yaml
jobs:
  deploy:
    uses: duplico/partyplanner/.github/workflows/deploy-occasion.yml@default
    with:
      occasion_dir: occasions/bbq-2026
    secrets: inherit
```

`deploy-occasion` renders, applies Terraform, syncs link records and the site,
invalidates CloudFront, and uploads `links.csv` as a run artifact.
`destroy-occasion` (wire it to `workflow_dispatch` only) exports a final RSVP
snapshot artifact, then tears the capsule down — the subdomain is freed for
whatever comes next.

## Development

```console
$ uv sync
$ uv run pytest
$ uv run ruff check .
$ terraform -chdir=examples/occasion init -backend=false && terraform -chdir=examples/occasion validate
```

## Threat model, briefly

Pages are public-if-you-have-the-URL: long random tokens, `robots.txt`,
`noindex`, and no sitemap — but no auth. Don't put anything on an invitation
page you wouldn't tell a guest's group chat. Anyone with a link can RSVP under
any name (social trust is the moderation model); hosts can delete rows in
DynamoDB and force-rotate links.
