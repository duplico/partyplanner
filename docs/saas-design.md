# Design notes: making partyplanner easier for other people to use

Planning document, not a commitment. It answers two questions:

1. How do less-technical cohosts edit (and create) occasions without writing git
   commits?
2. What would it take to productize this?

## Two axes, four destinations

The questions look like one project and are two independent axes:

- **Mutability** — where an occasion's content lives and who may change it.
  Today: YAML in a private git repo, changed by a `git push`.
- **Distribution** — whose AWS account it runs in and who does the deploying.
  Today: an operator with Terraform, DNS, and OIDC set up, one capsule per
  occasion.

Question 1 is entirely a mutability question. Question 2 splits along
distribution, and the two directions it can go are near-opposites:

| Destination | Mutability | Distribution | Verdict |
|---|---|---|---|
| **A. Cohosts edit in a browser** | content as data, YAML as mirror | unchanged (your deploy) | The answer to question 1 |
| **B. Adoptable self-hosting** | same as A | others deploy their own | **Likely landing spot** |
| **C. A small service for people you know** | same as A | you host, multi-tenant | Plausible, socially expensive |
| **D. A SaaS you operate** | same as A | you host, strangers self-serve | Sketch only |

A is a prerequisite for all of them, which makes it the only investment that is
unconditionally worth making. B and C are alternatives, not sequential rungs:
B is open-source distribution work, C is running a service. D is C plus a
business.

Effort below is in my own sessions, not human-team time.

## What must not change

- **No guest accounts, ever.** The link is the invitation and the credential —
  the "Doodle model": no sign-up, save your link if you want to come back. This
  is the differentiator (see [Why not Partiful](#why-not-just-use-partiful)) and
  it is why there is no password reset, no session store, no consent screen, and
  no idle cost. State it as a product principle, not a description of the code.
- **RSVPs are socially visible** to everyone in a link's scope.
- **Capability-based authority**, with [security-model.md](security-model.md)'s
  invariants intact.
- **~$0 idle cost.** No always-on compute.
- **Link unfurls look like invitations** — server-rendered OpenGraph tags. This
  quietly constrains a lot of architecture (see [Content as
  data](#content-as-data-the-one-real-change)).
- **Your data is a YAML file you own**, and the CLI keeps working, even once a
  database is authoritative.

## Why not just use Partiful?

For a single-event party, Partiful beats this: free, no ops, SMS reminders and
text-blast updates, comments, a shared photo album, an app. Delivery is the hard
part of party software and they solved it; partyplanner deliberately didn't.

Three differences that matter anyway:

1. **Multi-part occasions with partial attendance.** An occasion with six events
   where guests are invited to a subset is the `scopes:` feature, and Partiful
   has no equivalent — hosts make several disconnected events or one lumpy one.
   Real today, but copyable.
2. **The Doodle model.** Partiful's RSVP flow asks for a name *and a phone
   number*, verified by an SMS code; those numbers power reminders, host text
   blasts, and invitations from a guest's "Mutuals." partyplanner asks for a name
   in a box. This one is durable, because it is load-bearing for their product
   and an absence in ours.
3. **Your domain, your data, your retention.** `2026.example.com` rather than a
   path under someone's marketplace, with a CSV export and a TTL you chose.

The strategic read: the differentiator is an *absence*, and absences are hard to
charge for — which argues against D and for B. "Doodle for parties, on your own
domain" is a good open-source pitch and a weak consumer-SaaS one.

## What currently couples content to git

| Coupling | Mechanism today | What it blocks |
|---|---|---|
| Content is baked into HTML | `render()` writes one page per link scope at deploy time | Any edit needs a re-render + S3 sync + invalidation |
| Content lives in a working tree | `occasion.yaml` + `assets/` read from the config's directory | Editing requires a clone, a commit, and push rights |
| Ids and tokens are minted locally | `mint` / `link add` write back to YAML | Creating a link requires the CLI on a laptop |
| Deploys are repo events | Per-occasion GitHub Actions workflow | Publishing requires GitHub identity |
| Secrets sit next to content | `.links.yaml` (live tokens + admin key) shares the repo | Anyone who may edit prose can read every guest's link |

That last row is the sharpest: **granting a cohost "edit the blurb" today means
granting them the admin key and every invitation token.** Git permissions cannot
express what you actually want.

## A — cohosts edit in a browser (~4–6 sessions)

Content moves into DynamoDB; the occasion's YAML becomes a mirror the platform
machine-commits back to the events repo, one-way. Saving re-renders the affected
scopes to S3 and invalidates. The capsule, the renderer, the RSVP API, and the
security model are unchanged.

Needed:

- **Editor keys** — one `EDITOR#<key>` record per cohost, labelled and
  revocable, deliberately *not* the admin key: editing prose and editing other
  people's RSVPs are different powers, fused today only because both live in git.
- **A form UI over the config model.** `Occasion` is already a pydantic model, so
  the form and its validation messages can be generated from JSON Schema.
- **Live preview using the real renderer.** `preview.py` already renders and
  serves an in-memory RSVP stand-in, so the cohost sees the real page.
- **Photo upload.** Presigned PUT, content-type and size caps, EXIF stripping.
- **Optimistic concurrency on save**, so a second editor gets "someone else
  changed this — reload" rather than a silent clobber.

Stop here and cohosts edit and create occasions in a browser with no git, no
YAML, and no GitHub account, on infrastructure that still costs nothing idle.
Remaining limitation: a brand-new occasion waits 5–20 minutes on ACM and
CloudFront.

**Rejected: a git-backed editor** that commits YAML through a GitHub App. Its
only unique benefit over a database is audit/rollback, which a small version
table also provides, and socially it is indistinguishable from handing cohosts
commit access. It charges commit latency, a GitHub App credential, and conflict
machinery, and it is a dead end for C.

**The zero-cost interim, worth doing this week regardless:** a PR check that runs
`partyplanner validate` and comments friendly errors, plus one that publishes a
preview site under a `pr-<n>/` prefix. Cohosts still see YAML and still need a
GitHub account — for some cohosts that is enough.

## Content as data: the one real change

"Content is mutable" fights "pages are static." Three ways to serve it:

| Approach | Edit → live | Unfurls |
|---|---|---|
| **Re-render on save** (worker re-renders affected scopes, writes S3, invalidates) | seconds | perfect (baked OG tags) |
| Client-side render from `/api/state` | instant | **broken** — crawlers don't run JS |
| Static shell + edge-rendered `<head>` | instant | perfect, most moving parts |

Re-render on save is the answer: it preserves today's output byte for byte,
reuses code that exists, and seconds are invisible to a human clicking Save.
Client-side rendering is rejected on the record because it looks cheapest and
silently destroys a load-bearing feature.

## B — adoptable self-hosting (~3–5 sessions)

The likely landing spot, and mostly distribution work rather than architecture.
The blocker today isn't the code, it's [bootstrapping.md](bootstrapping.md): an
AWS account, a Terraform state bucket, a hosted zone plus delegation, a GitHub
OIDC role, a per-occasion `terraform/main.tf`, a workflow file.

- **`partyplanner bootstrap`** — one prompt-driven command for the state bucket,
  OIDC role, and zone wiring, most of which is already inferable from
  `partyplanner.yaml`.
- **A local-first trial path.** Give `preview` durable storage and it becomes
  "run it on your laptop, a Pi, or in Docker" — nobody needs an AWS account to
  evaluate it, and it serves people who never want AWS at all.
- **Packaged artifacts instead of a fork:** a published package, a versioned
  Terraform module, a template events repo, and a deploy path that doesn't
  assume GitHub Actions.
- **The A editor ships inside the self-hosted product**, which is what makes it
  adoptable by non-git people.

## C — a small service for people you know (~4–6 sessions)

The per-occasion capsule is right for a tool and wrong for a service: creation is
slow (cert validation, distribution propagation), quota-bound (CloudFront
distributions per account; ~$0.50/mo per hosted zone), and requires deploy
credentials. Invert it: **one** distribution + wildcard cert on a platform
domain, **one** API + Lambda, **one** table, and an occasion becomes rows plus a
bucket prefix. Terraform then describes the platform, not the tenant.

Consequences to design through, not around:

- **Tenant dimension in every key.** `EVENT#<id>` becomes
  `OCCASION#<id>#EVENT#<id>`; every read path in `handler.py`, `aws.py`, and
  `preview.py` is touched. Doing this while there is exactly one tenant per table
  is the cheapest future-proofing available.
- **Isolation becomes a code property, not an AWS property** — one table instead
  of one per occasion, so cross-tenant reads need explicit tests.
- **Noisy neighbours.** Per-occasion throttles and concurrency caps move into the
  request path, or one popular party spends your money.
- **Host recovery.** See [the email dial](#host-email-a-three-position-dial).
- **The social cost is the real cost.** Once friends-of-friends create occasions,
  you personally own moderation, your domain's reputation, and the AWS bill.
  That's a decision about you, not about architecture.

## Host email: a three-position dial

The Doodle answer, and the whole identity system: one transactional email to the
host at creation containing the host/admin links. No accounts, no passwords.
**Guests never supply contact information in any position**, so the invariant
holds.

| Position | Stored | Buys | Costs |
|---|---|---|---|
| **Nothing** (**the default everywhere**, including a hosted service) | — | no PII at rest, no data-subject obligations | no recovery at all |
| **Salted hash** (opt-in per occasion) | `sha256(salt + address)` per occasion | "I lost my link": re-type the address, re-send | can't contact hosts proactively |
| **Plaintext** | address | digests, expiry nags | the thing you're trying to avoid |

Decided: store nothing by default even in destination C, and let a host opt into
the hash if they want recovery. Losing your link is then the same class of
problem as losing any other capability here, which is consistent with how guests
are already treated.

Caveats that apply to all three: the mail provider still logs the send, so
"we store nothing" is true of your database and not of the whole system; and
bounce status should be recorded per occasion, never keyed by address.

## D — a SaaS you operate (sketch)

Everything a product needs that a tool doesn't: host accounts and cohost
management; invite delivery (SES production access, bounce and complaint
handling, consent); billing (Stripe, plans, dunning — natural paid axes are
custom domains, guest volume, retention); an abuse reporting and takedown path,
since unguessable URLs make you a tempting phishing host; a privacy policy,
deletion requests, and a retention policy that isn't just TTL; structured
key-redacted logs and per-tenant metrics without violating invariant 9; and a
licensing decision, since MIT means anyone can run what you're selling
(open-core: keep the CLI, renderer, and modules MIT, license the control plane
separately — decide before writing the control plane).

Guest accounts are still not required here, and adding them would make this a
worse Partiful.

## Cheap now, pays later

Small today, useful on their own merits, and they remove most of the risk in
every destination above:

1. **Factor `partyplanner` into core vs ops.** `config`/`render`/`ics`/`md` have
   no AWS dependency and can run in a Lambda as-is; this is also the packaging
   boundary B needs.
2. **Make the renderer take an in-memory asset map** rather than only a config
   directory, so uploads needn't be materialized in a git tree.
3. **A render-and-diff harness** asserting byte-stability, so moving the render
   trigger is verifiable rather than hoped-at.
4. **Extract the preview RSVP stand-in from the CLI** so one implementation
   serves `preview`, editor previews, and tests.
5. **Tenant-prefix the DynamoDB key space** — only if C is live; skip it for B.

## Decisions this needs from you

1. **B or C?** They're alternatives. B keeps you a maintainer; C makes you an
   operator.
2. **Do cohosts get GitHub accounts?** If yes, the interim PR-preview work may be
   the whole answer to question 1.
3. **Licensing**, if D is ever real.

## Filed issues

| Where | Issue |
|---|---|
| interim | #28 cohost PRs: friendly validation + a preview site |
| A | #35 content as data / re-render on save, #29 editor keys, #30 `modules/editor`, #31 photo uploads |
| B | #41 adoptable self-hosting, #42 local-first mode |
| prep | #33 core/ops split + render harness |
| C | #34 hosting plane (on hold), #32 tenant-prefixed keys (only if C) |
| D | #36 delivery, billing, safety, legal, ops, licensing |

## Non-goals

Explicitly not proposed, so they don't get re-litigated:

- Guest accounts or logins of any kind.
- Private (per-guest-authenticated) RSVP lists; visibility is the product.
- Client-side content rendering, which breaks link unfurls.
- Guest-facing discovery, a public event marketplace, or invitations from
  "mutuals."
- An always-on component that puts a floor under idle cost.
- A general-purpose CMS. The config model is small on purpose.
