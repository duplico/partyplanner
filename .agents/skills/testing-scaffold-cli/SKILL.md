---
name: testing-scaffold-cli
description: How to test the partyplanner scaffold commands (bootstrap/occasion, shared partyplanner.yaml config, bootstrap-output inference) locally without AWS or real Terraform.
---

# Testing `partyplanner scaffold` locally

Everything is shell-based (no UI, no recording needed). Work in throwaway dirs
under /tmp; `--dir <path>` targets any events-repo root.

## Fake terraform with a stub on PATH
`scaffold occasion` infers zone id + role ARN by running
`terraform -chdir=<root>/bootstrap output -json` (src/partyplanner/scaffold.py,
`bootstrap_outputs`). No AWS needed — put a stub first on PATH:

```bash
mkdir -p /tmp/stub-bin && cat > /tmp/stub-bin/terraform <<'EOF'
#!/bin/bash
cat <<'JSON'
{"deploy_role_arn":{"value":"arn:aws:iam::123456789012:role/deploy"},"zone_ids":{"value":{"example.com":"Z1AAAAAAAAAA","events.example.com":"Z2BBBBBBBBBB"}}}
JSON
EOF
chmod +x /tmp/stub-bin/terraform
export PATH=/tmp/stub-bin:$PATH
```
The repo must have `bootstrap/main.tf` present (run `scaffold bootstrap` first)
or `bootstrap_outputs` errors before even calling terraform.

## Key behaviors and expected outputs
- `scaffold bootstrap --dir R --zone Z --budget-email E --github-repo O/R2`
  writes `bootstrap/main.tf` AND `partyplanner.yaml` (state_bucket/region/branch/ref).
  `--state-bucket` reads `$TF_STATE_BUCKET` via click envvar; with neither,
  the file gets a commented `# state_bucket: your-tf-state-bucket` line.
- `scaffold occasion NAME --dir R --domain FQDN` needs only name+domain when
  partyplanner.yaml + stub terraform are in place. Zone match is longest-suffix
  (events.example.com beats example.com for a.events.example.com). Inferred
  zone_id lands in `occasions/NAME/terraform/main.tf`; role ARN and state bucket
  land in `.github/workflows/deploy-NAME.yml` (not main.tf — the backend bucket
  comes from the workflow's `tf_state_bucket` input).
- Without `--force`, rerunning errors `refusing to overwrite existing ... (--force
  to regenerate)` and exits 1. With `--force`, everything is rewritten EXCEPT an
  existing `occasions/NAME/occasion.yaml` (it holds minted ids/tokens) and an
  existing root `partyplanner.yaml` (user-editable) — both reported as
  `kept <path>`.
- Error messages to assert verbatim-ish: "no state bucket: pass --state-bucket
  or set state_bucket in partyplanner.yaml"; "no bootstrap zone matches domain
  ...; pass --zone-id explicitly"; "no bootstrap root at ...; run `partyplanner
  scaffold bootstrap` ..."; "terraform not found on PATH; pass --zone-id and
  --role-arn explicitly" (test the last one with `env PATH=<dirs without
  terraform> uv run partyplanner ...`; note /usr/local/bin has a real terraform).
- Continuity: fill `where: EDIT ME` in the scaffolded occasion.yaml, then
  `mint` → `validate` → `render` should all succeed; rendered per-link page has
  OG tags and an ICS next to index.html.

## Gotchas
- `env`/exports do NOT persist between separate exec calls even with the same
  shell_id here — set PATH/vars inside each command string.

## Devin Secrets Needed
None.
