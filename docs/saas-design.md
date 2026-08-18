# SaaS-ification: design notes and a staged ladder

Planning document, not a commitment. It answers two questions:

1. How do less-technical cohosts edit (and create) occasions without writing git
   commits?
2. What would it take to productize this — anywhere from "a little site I deploy
   for friends" to a real multi-tenant SaaS?

The two questions look like one project and are actually two independent axes.
Naming them separately is most of the value of this document:

- **Mutability**: where the occasion's content lives, and who may change it.
  Today: YAML in a private git repo, changed by a `git push` from someone with
  commit rights and a working `partyplanner` install.
- **Tenancy**: how many isolated deployments the platform runs, and who owns the
  AWS account they run in. Today: one AWS-account-owning operator, one Terraform
  capsule (site + table + Lambda + cert + subdomain) per occasion.

You can move a long way along the mutability axis without touching tenancy — and
that is exactly what question 1 asks for. Tenancy is what question 2 is really
about, and it is the expensive one.

## What must not change

These are the things that make partyplanner good, and every option below is
judged against them:

- **No guest accounts, ever.** The link is the invitation and the credential.
- **RSVPs are socially visible** to everyone in a link's scope. Seeing who's
  coming is the product.
- **Capability-based authority**, with the invariants in
  [security-model.md](security-model.md) intact.
- **~$0 idle cost.** No always-on compute, no per-occasion floor beyond a hosted
  zone.
- **Link unfurls look like invitations** — server-rendered OpenGraph tags. This
  one quietly constrains a lot of architecture (see
  [Content as data](#stage-3--content-as-data-the-real-saas)).
- **Your data is a YAML file you own.** Even if the source of truth moves into a
  database, YAML round-trips must stay lossless.

## What currently couples content to git

| Coupling | Mechanism today | What it blocks |
|---|---|---|
| Content is baked into HTML | `render()` writes one page per distinct link scope at deploy time | Any edit needs a re-render + S3 sync + CloudFront invalidation |
| Content lives in a git working tree | `occasion.yaml` + `assets/` + `overrides/` read from the config's directory | Editing requires a clone, a commit, and push rights |
| Ids and tokens are minted locally | `partyplanner mint`, `link add` write back to YAML; `render` refuses without them | Creating a link requires the CLI on someone's laptop |
| Deploys are repo events | Per-occasion GitHub Actions workflow on push to the deployable branch | Publishing requires GitHub identity; feedback takes minutes |
| Each occasion is a Terraform capsule | `modules/occasion` per occasion, own state key, own ACM cert + subdomain | Creating an occasion needs Terraform, DNS, and 5–20 min of cert/CloudFront time |
| Keys are per-occasion table records | `LINK#`, `ADMIN#`, `KEY#`, `EVENT#` in an occasion-private table | Nothing today — but the key space has no tenant dimension, so it is the thing to fix first if a table is ever shared |
| Secrets sit next to content | `.links.yaml` (live tokens + admin key) in the same repo as the config | Anyone who may edit content can read every invitation token and the admin key |

That last row is the sharpest one for question 1: **granting a cohost "edit the
blurb" today means granting them the admin key and every guest's link.** Git
permissions are not fine-grained enough to express what you actually want.

## The ladder

Four rungs. Each is independently shippable and independently *stoppable* —
rungs 0 and 1 are useful even if you never build 2 or 3, and nothing in 0/1 is
wasted work if you do.

| Rung | What it is | Cohosts get | Effort |
|---|---|---|---|
| 0 | Tighten the git loop | Edit YAML in GitHub's web editor; a PR check validates and renders a preview | ~1 session |
| 1 | A capability-gated editor app, still git-backed | A form-based editor with live preview and a Publish button; no git, no YAML, no GitHub account | ~3–5 sessions |
| 2 | One multi-tenant hosting plane | Occasion creation in seconds instead of a Terraform apply | ~4–6 sessions |
| 3 | Content as data + accounts + billing | A real product | ~8–15 sessions plus external waits |

Effort is in my own sessions, not human-team time. Rung 3's real cost is
external: SES production access, Stripe onboarding, a legal review of
guest-data handling, and custom-domain support.

### Rung 0 — tighten the git loop

Do this regardless; it is worth more per hour than anything else here.

GitHub's web editor already lets a cohost change `occasion.yaml` and open a PR
from the browser. What's missing is the feedback loop that makes that safe for
someone who doesn't read YAML:

- A PR check that runs `partyplanner validate` and turns pydantic errors into a
  friendly comment ("event 'Dinner' has rsvp: open and needs a `when`" is
  already close).
- A PR check that renders and publishes a **preview site** (a `pr-<n>/` prefix in
  the existing bucket, or a throwaway capsule) so the cohost sees their edit on
  a real page with a real preview link.
- Split the config so cohosts touch only prose: `occasion.yaml` (content) is
  already separate from `.links.yaml` (secrets), but the repo is not — see
  [Splitting secrets from content](#splitting-secrets-from-content).

Limits: cohosts still see YAML, still need a GitHub account, still can't upload
a photo without knowing what a file path is, and a typo is only caught after a
round-trip through CI. That's the ceiling of rung 0, and for some cohosts it's
enough.

### Rung 1 — the editor as a capability app

This is the recommended answer to question 1, and it is a surprisingly small
change to the system: **nothing about a deployed occasion changes.** The editor
is a new, separate, per-events-repo deployment (`modules/editor`) that writes
YAML into the events repo through a GitHub App, and lets the existing deploy
pipeline do exactly what it does today.

```
cohost's browser ── /edit/<editor-key>/ ─→ CloudFront → Lambda (editor API)
                                                          ├─ render preview (in-memory, no AWS)
                                                          ├─ presigned S3 PUT for photos
                                                          └─ GitHub App: commit occasion.yaml
                                                                             ↓
                                                             existing deploy-occasion workflow
```

Why git-backed rather than a database:

- The audit log, review, rollback, and blame story is free and better than
  anything I'd build in a sprint.
- The deploy path, the renderer, the security model, and every existing test
  stay exactly as they are. The blast radius is one new module.
- If the editor breaks or you abandon it, the repo is still the source of truth
  and the CLI still works. That is a genuinely cheap bet.

What it needs:

- **Editor keys** (see [below](#editor-identity-editor-keys)) — per-cohost,
  revocable, attributed, and *not* the admin key.
- **A form UI over the config model.** `Occasion` is already a pydantic model, so
  the field list, types, and validation messages can be generated rather than
  hand-written; a JSON Schema from pydantic drives the form.
- **Live preview using the real renderer.** `preview.py` already renders and
  serves with an in-memory RSVP stand-in — that is 80% of a preview service
  already written, and it means the cohost sees the actual page, not an
  approximation.
- **Photo upload.** Presigned PUT, content-type and size caps, and EXIF
  stripping (guest photos carry GPS).
- **Compare-and-swap on publish.** The GitHub contents API takes the base blob
  SHA; a stale SHA becomes "someone else edited this — reload" instead of a
  silent clobber. Git-as-a-database is fine here precisely because writes are
  rare and human-paced.
- **Deploy status in the UI.** Publish → live is a couple of minutes; the editor
  should poll the workflow run and say so, or the cohost will publish four times.

Creating an occasion from the editor is the same shape of work: `scaffold.py` is
pure file generation, so "new occasion" is one commit containing the config stub,
the Terraform root, and the two workflows — plus the Terraform apply the pipeline
already does. Worth noting the honest limitation: **a brand-new occasion still
waits on ACM validation and CloudFront (5–20 minutes) before its URL works.**
That's what rung 2 fixes.

Stop here and you have: cohosts who edit and create events in a browser with no
git, no YAML, and no GitHub account, on infrastructure that costs nothing when
idle, with git history and the CLI still underneath. For "a little site I deploy
for friends," this rung *is* the product.

### Rung 2 — one hosting plane instead of a capsule per occasion

The per-occasion capsule is the right design for a tool and the wrong one for a
product. Per occasion it currently creates a CloudFront distribution, an ACM
cert, DNS records, an API Gateway, a Lambda, and a DynamoDB table, from a
Terraform root with its own state key. That means occasion creation is slow
(cert validation, distribution propagation), quota-bound (CloudFront
distributions are limited per account; hosted zones cost ~$0.50/mo each), and
requires the creator to hold deploy credentials.

The inversion: **one** distribution + wildcard cert on a platform domain
(`*.parties.example.com`), **one** API Gateway + Lambda, **one** DynamoDB table,
and an occasion becomes rows in it plus a prefix in a bucket. Creating an
occasion becomes a write, and it's live in seconds. Terraform then describes the
platform, not the tenant — which is what Terraform is actually good at.

Consequences to design through, not around:

- **Tenant dimension in every key.** `EVENT#<id>` becomes
  `OCCASION#<id>#EVENT#<id>` (or the occasion id moves into the partition key and
  today's key becomes the sort key). Every read path in `handler.py`, `aws.py`,
  and `preview.py` is touched. Doing this *before* rung 2 is the single cheapest
  piece of future-proofing available (see
  [Cheap now, pays later](#cheap-now-pays-later)).
- **Noisy neighbours.** Today's guardrails (API throttle, reserved concurrency,
  budget alarm) are per-occasion and per-account. Shared, they become
  per-occasion rate limits and quotas enforced in the request path, plus caps on
  asset size/count, or one popular party degrades everyone's and spends your
  money.
- **Isolation is now a code property, not an AWS property.** Today an occasion
  cannot read another occasion's data because it's a different table. After
  rung 2 that guarantee is a `begins_with` away from being wrong. It needs
  explicit tests asserting cross-tenant reads fail.
- **Custom domains become the paid feature** they naturally are: the wildcard
  covers `<name>.parties.example.com` for free; a customer's own domain needs an
  ACM cert + an alternate domain name on a distribution (or Cloudflare for SaaS
  in front), which is where per-tenant cost and support actually lives.
- **Retirement changes meaning.** "Destroy the capsule and free the subdomain" is
  currently a Terraform destroy with a final CSV export. It becomes a data
  lifecycle: export, then delete rows and objects, with the TTL still doing the
  routine work.

### Rung 3 — content as data (the real SaaS)

Move the source of truth into DynamoDB; YAML becomes import/export. Editing an
occasion is an API write, not a commit and a deploy. This is where the remaining
latency and all of the git-shaped awkwardness goes away — and where the
architecture question gets interesting, because "content is mutable" fights
"pages are static."

Three ways to serve mutable content, and the tradeoff is entirely about link
unfurls and first paint:

| Approach | Edit → live | Unfurls | Notes |
|---|---|---|---|
| Re-render on save (worker re-renders the scopes affected, writes S3, invalidates) | seconds | perfect (baked OG tags) | Keeps today's renderer and its output verbatim; invalidation costs and races need care |
| Client-side render from `/api/state` | instant | broken — crawlers don't run JS | Cheapest to build, and it silently destroys a load-bearing feature |
| Static shell + edge-rendered `<head>` (CloudFront Function / Lambda@Edge reading a small OG record) | instant | perfect | Most moving parts; the shell is cacheable and the OG record is tiny |

Re-render-on-save is the right first answer: it preserves the current output
exactly, reuses code that exists, and the seconds of latency are invisible to a
human clicking Save. Client-side rendering should be rejected explicitly, on the
record, because it looks cheapest and it breaks the unfurl.

Rung 3 also drags in everything a product needs that a tool doesn't:

- **Accounts for hosts and cohosts.** Capability links are wonderful for guests
  and cohosts-of-a-friend; they are a support nightmare at scale ("I lost the
  link" has no self-service recovery) and billing needs a durable customer
  identity anyway. Email magic-link is the minimum; keep it strictly on the host
  side, never for guests.
- **Invite delivery.** Today delivery is "paste it in the group chat," which is a
  feature. A product will want optional guest lists, per-guest links (already
  expressible: scoped links with `prefill_name`), reminders, and "who hasn't
  replied." That means SES production access, bounce/complaint handling, and
  CAN-SPAM-shaped consent thinking.
- **Billing.** Stripe, plans, dunning, and a free tier that is genuinely free
  because idle marginal cost after rung 2 really is ~zero. The natural paid axes
  are custom domains, guest volume, and retention.
- **Abuse and content safety.** Hosting user content on your domain means a
  reporting path, a takedown process, and a ToS. Unguessable URLs make you a
  tempting phishing host.
- **Privacy and legal.** You'd hold guests' names and attendance for other
  people's parties: a processor relationship, a privacy policy, deletion
  requests, and a retention policy that isn't just "TTL."
- **Ops.** Today there are deliberately no access logs. Supporting paying
  customers needs structured, key-redacted logs, per-tenant metrics, PITR on the
  table, and on-demand export — without violating invariant 9.
- **Licensing.** MIT means anyone can run the thing you're selling. If rung 3
  ever becomes a business, the usual answer is open-core: keep the CLI,
  renderer, and modules MIT (that's the part that earns trust and contributors)
  and license the control plane separately. Worth deciding before writing the
  control plane, not after.

## Editor identity: editor keys

The existing security model already has the right primitive, and extending it is
cleaner than bolting on accounts:

| Asset | Sensitivity |
|---|---|
| **Editor key** (`/edit/<key>/`) | Private to one cohost; may change occasion content, but not read invitation tokens or the admin key |

- One record per cohost (`EDITOR#<key>`), with a label — so revoking one cohost
  doesn't disturb the others, and every publish is attributable in the commit
  author/trailer.
- Deliberately **not** the admin key: editing content and editing other people's
  RSVPs are different powers, and today they're fused only because both live in
  git.
- Scope: which occasions the key may edit, and (later) whether it may create new
  ones.
- Same bearer caveats as every other key here, so it inherits the residual-risk
  vocabulary already written down; the security model gets a new actor
  ("cohost"), a new asset row, and a capability-rule row.

This is the piece that makes rung 1 respectable rather than a hack: a cohost gets
exactly "edit the words and pictures" and nothing else.

## Splitting secrets from content

Rung 1 works best if the sensitive parts of an occasion are separable from the
prose. `.links.yaml` is already a separate file; what's missing is that it's in
the same repo with the same permissions. Options, cheapest first:

1. Leave it, and rely on the editor never exposing tokens (the editor API reads
   `occasion.yaml`, writes `occasion.yaml`, and never returns `.links.yaml`).
   Cohosts with the editor key never see secrets; cohosts with repo access still
   do.
2. Move links and the admin key into the occasion's DynamoDB table as the source
   of truth (the table already holds them; the repo copy exists so `render` can
   bake page paths). Then the repo holds no secrets — but "redeploys never rotate
   links" now depends on a database, not a committed file, which is a real loss
   of a property you designed for deliberately.
3. Keep the split repos: a content repo cohosts may write, a secrets repo they
   may not, joined at deploy time.

Option 1 first. Option 2 is the rung-3 answer and shouldn't be done early.

## Cheap now, pays later

Work that is small today, useful on its own merits, and removes most of the
rung 2/3 risk:

1. **Tenant-prefix the DynamoDB key space** (and the fakes/tests) while there's
   exactly one tenant per table. Mechanical now, invasive later.
2. **Factor `partyplanner` into core vs ops**: `config`/`render`/`ics`/`md` have
   no AWS or filesystem-mutation dependency and can run in a Lambda as-is; the
   package boundary should say so, so a render service can depend on core only.
3. **Make the renderer take an in-memory asset map** rather than only a config
   directory, so uploads don't have to be materialized in a git tree.
4. **A render-and-diff harness**: given a config, produce the site; assert
   byte-stability. Any move of the render trigger (PR preview, editor preview,
   render-on-save) is then verifiable rather than hoped-at.
5. **Extract the preview RSVP stand-in from the CLI** so one implementation
   serves `preview`, editor previews, and integration tests — invariant "preview
   and Lambda stay in lockstep" gets easier, not harder.
6. **Write down the cross-tenant isolation tests** you'd want to already have.

## Decisions this needs from you

1. **How far up the ladder do you actually want to go?** Rung 1 is a weekend and
   solves the stated cohost problem; rung 3 is a business.
2. **Do cohosts get GitHub accounts?** If yes, rung 0 is most of the win and
   rung 1 is a nice-to-have. If no, rung 1 is the answer.
3. **Self-hosted-per-events-repo editor, or one editor you host for friends?**
   The former is a Terraform module and fits the existing product shape; the
   latter is rung 2's tenancy problem arriving early.
4. **Is the "no accounts for anyone" property negotiable for *hosts*?** Rung 3
   needs it to be.
5. **Licensing**, if rung 3 is ever real.

## Non-goals

Explicitly not proposed, so they don't get re-litigated:

- Guest accounts or logins of any kind.
- Private (per-guest-authenticated) RSVP lists; visibility is the product.
- Client-side content rendering, which breaks link unfurls.
- An always-on component (a container, an RDS instance) that puts a floor under
  idle cost.
- A general-purpose CMS. The config model is small on purpose.
