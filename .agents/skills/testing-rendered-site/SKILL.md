---
name: testing-rendered-site
description: How to run and end-to-end test a partyplanner rendered static site plus the RSVP API locally, without any AWS resources.
---

# Testing a partyplanner rendered site locally

## Render the fixtures
```bash
uv sync   # uv is at ~/.local/bin/uv
uv run partyplanner render fixtures/bbq/occasion.yaml --out out/bbq
uv run partyplanner render fixtures/allhallowtide/occasion.yaml --out out/allhallowtide
```
Rendered sites land in `out/<name>/site` (per-link pages at `i/<token>/index.html`,
plus `index.html` landing, `robots.txt`, `ics/*.ics`, `assets/`).

## Serve static site + the real Lambda handler with no AWS
The production API is `modules/occasion/lambda/handler.py` behind CloudFront `/api/*`.
You can run the *actual* handler locally against moto's in-memory DynamoDB:

1. `uv pip install moto` (dev-only; boto3 is already in the dev group).
2. Write a small script that:
   - starts `moto.mock_aws()`, creates a table named per `TABLE_NAME` env with
     `pk` (HASH, S) / `sk` (RANGE, S) keys;
   - seeds link items with the real `partyplanner.aws.sync_links(occasion, table)`
     (this also deletes revoked tokens, so revocation is exercised for real);
   - subclasses `SimpleHTTPRequestHandler` with `directory=<site dir>`, routing
     `/api/*` GET/POST to `handler.lambda_handler` with an event shaped like
     `{"requestContext": {"http": {"method": ...}}, "rawPath": ..., "queryStringParameters": ..., "body": ...}`.
   A known-good copy of this script from a prior session: `/tmp/serve_occasion.py`
   (usage: `python serve_occasion.py <config.yaml> <site_dir> <port>`).
3. Run one server per fixture (e.g. BBQ on :8001, allhallowtide on :8002).
   `SimpleHTTPRequestHandler` serves `index.html` for `/i/<token>/` natively,
   matching the CloudFront rewrite function in production.

## Alternative: `partyplanner preview` (in-memory API, zero deps)
`uv run partyplanner preview fixtures/allhallowtide/occasion.yaml --port 8010`
renders to a temp dir and serves it with an in-memory `PreviewStore` that has
full parity with the Lambda handler (same 403 messages, RSVP edit keys, admin).
No moto needed. It prints the link URLs plus an "admin view" URL with an
auto-minted preview-only admin key (`?me=<key>`). RSVP state survives hot
reloads but not process restarts.

## RSVP edit keys / admin (if the feature branch has them)
- First RSVP for an (event, name) returns `{ok, me}` and the UI shows a
  "bookmark your private edit link" message with a `?me=<key>` URL; the key is
  stored in `localStorage["partyplanner-me"]` and spans all events on the link.
- Owned rows show "(you)", a confirm-guarded "remove" button, form prefill,
  and an "Update RSVP" submit label.
- Keyless resubmit of an owned name → 403 shown in the status line:
  "that name already has an RSVP here — use your private edit link to change it".
- Admin (`?me=<admin key>`) sees remove buttons on all rows but no "(you)";
  the frontend must NOT overwrite a stored admin key with `data.me` from edits
  (check `localStorage.getItem("partyplanner-me")` after an admin edit + reload).
- Good multi-user simulation: normal window = user A / keyless stranger
  (clear localStorage between roles), incognito window = returning user via
  the `?me=` link.
- CAUTION with two windows open: the CDP-based console tool (browser_console)
  attaches to ONE target only (in practice the incognito window's page), not
  necessarily the window visible on screen — verify which context you're in
  (e.g. set `document.title` and look at the tabs) before mutating
  localStorage, or use each window's own devtools (F12) instead.
- Identity-UX behaviors (if the branch has them): host mode renders a JS
  `.admin-banner` div with a "Leave host mode" link (href = URL minus `me`);
  a persistent `.me-link` note is appended at the end of `<main>` only when
  the browser's own saved key is the active, vetted, server-known identity —
  it must be absent for fresh visitors, in admin mode, and on borrowed `?me=`
  links (a borrowed URL key also never overwrites the saved identity).

## What to check
- Tokens live in the fixture YAMLs (`links:` in the config, or the sibling
  machine-generated `.links.yaml`); the revoked one for allhallowtide
  is `fixturerevokedtoken2` (check the YAML — docs elsewhere may cite a wrong token).
- Scope enforcement: POST `/api/rsvp` with a token whose scope excludes the
  event_id must return HTTP 400 `{"error": "this link cannot RSVP to that event"}`.
- Prefill: `prefill` is returned by `/api/state` only until a matching (casefolded)
  name appears in any scoped event's RSVP list.
- Upsert: same name (casefolded) re-submitting replaces the row, never duplicates.
- Events with `rsvp: none` (e.g. the stream card) render with no form and no list.

## Devin Secrets Needed
None — everything runs locally with fake AWS credentials under moto.
