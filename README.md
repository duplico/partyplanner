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
  informational cards like a live stream link — only `title` is required, so a
  card can be as little as a title and a blurb). Blurbs are **Markdown**
  (raw HTML is escaped); single newlines flow within a paragraph, blank lines
  start a new one. An event's `where` renders as a link to a Google Maps
  search; set `where_url` to point it somewhere else.
- A **link** is the unit of invitation: an unguessable URL whose **scope** is a
  subset of the occasion's events. The link is both the invitation and the
  credential — no logins. Forward it freely; recipients self-identify by name.
  Optionally personalize a link so it prefills someone's name.
- RSVPs (name, yes/maybe/no, +N guests) are visible to everyone who can see the
  page — seeing who's coming drives attendance, and it's also how hosts read
  the list.
- The first RSVP mints a **private edit key** for that person: the page offers
  a bookmarkable `?me=<key>` link (and remembers the key in the browser) that
  lets them edit or remove their RSVPs for every event under that link. Only
  the key holder — or the host's **admin key** (`partyplanner link admin`),
  which can edit or remove anyone's RSVP — can change an existing RSVP.
- Every dated event gets **add-to-calendar links** (Google, Outlook, Yahoo, and
  an ICS file for everything else), and pages carry
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
```

```console
$ partyplanner mint occasion.yaml      # writes event ids back into the file
event 'BBQ': id = bbq
$ partyplanner link add occasion.yaml --note "group chat link, forward freely"
group chat link, forward freely	https://bbq-2026.events.example.com/i/k7f3q2vmxw4tzr6ehb2a/
$ partyplanner preview occasion.yaml   # local preview at http://127.0.0.1:8000
$ partyplanner render occasion.yaml    # out/site/ + out/links.csv
$ partyplanner links occasion.yaml
group chat link, forward freely	https://bbq-2026.events.example.com/i/k7f3q2vmxw4tzr6ehb2a/
```

See `fixtures/allhallowtide/` for the maximal example: six events, scoped
links, a landing-page stream embed, theme colors and per-event accents,
optional end times, per-occasion CSS overrides, and a revoked token.

## CLI

| command | what it does |
| --- | --- |
| `partyplanner validate <config>` | validate an occasion config |
| `partyplanner mint <config>` | fill in missing event ids and link tokens (writes back, preserves comments) |
| `partyplanner link add <config> --scope <s> --note <n> [--prefill <name>]` | mint a new invitation link into the machine-generated `.links.yaml` |
| `partyplanner link revoke <config> <token>` | move a generated link's token to `revoked:` (URL 404s on next deploy) |
| `partyplanner link admin <config>` | print the occasion's admin key (minting one into `.links.yaml` if needed) |
| `partyplanner link list <config>` | print invitation URLs |
| `partyplanner preview <config>` | render + serve locally with an in-memory RSVP API; prints a URL per link view |
| `partyplanner render <config> --out out` | render static site, ICS files, and `links.csv` |
| `partyplanner links <config>` | print invitation URLs |
| `partyplanner sync-links <config> --table <name>` | upsert link records to DynamoDB; delete revoked |
| `partyplanner export-rsvps --table <name>` | dump all RSVPs as CSV |
| `partyplanner init ...` | set up an events repo: bootstrap Terraform root + `partyplanner.yaml` (see [docs/bootstrapping.md](docs/bootstrapping.md)) |
| `partyplanner new <name> ...` | generate an occasion capsule (config stub, Terraform root, workflows) |

`mint` is a local, committed step: `render` refuses to run with missing ids or
tokens so that CI deploys are deterministic and **redeploys never rotate
links**.

Links are managed with `partyplanner link add`/`revoke`, which write a
machine-generated `.links.yaml` next to the occasion config (commit it; you
hand-author `events:` and `scopes:`, the tool owns the links). To rotate a
leaked link: `partyplanner link revoke <config> <token>`, then `link add` a
replacement and redeploy. A `links:` section in the occasion config itself
still works and is merged in for back-compat.

`preview` needs no mint and no AWS: unminted events/links get preview-only
ids/tokens (the file is untouched), every invitation link's view gets its own
local URL, and the RSVP forms work against an in-memory stand-in for the API
that is discarded when the server stops. It watches the config and its assets:
saving an edit re-renders the site and open pages refresh themselves (broken
edits keep the last good render and print the error). It binds loopback only;
if you need
to reach it from outside (e.g. a Windows browser when WSL2 localhost
forwarding misbehaves), pass `--host 0.0.0.0` and browse to the machine's IP.

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
any unused name (social trust is the moderation model), but changing or
removing an existing RSVP takes that person's private edit key or the host's
admin key. Keys are bearer capabilities: the edit link shouldn't be shared,
and the admin key (committed in `.links.yaml` in your private events repo)
shouldn't leave the hosts. Guest edit keys persist in the browser's
localStorage; the admin key never does — it lives only in the `?me=` URL, so
close the tab (and mind your history) on a shared machine. Admin edits never
take ownership of a guest's RSVP. Hosts can also force-rotate links or edit
rows in DynamoDB directly.
