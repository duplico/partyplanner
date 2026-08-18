# Security model

Partyplanner is deliberately account-free: every authority in the system is a
**bearer capability** — an unguessable token whose possession *is* the
permission. This document is the reference model; any behavior that violates an
invariant here is a bug, and any proposed change should be checked against it.

## Assets

| Asset | Sensitivity |
|---|---|
| RSVP data (names, responses, party sizes) | Semi-public: visible to everyone holding an invitation link in scope |
| Invitation token (`/i/<slug>-<token>/`) | Secret shared with an audience; leaking it exposes the pages and RSVP lists in its scope |
| Guest edit key (`?me=<key>`) | Private to one person; controls all of that person's RSVPs across the occasion |
| Admin key | Private to the host; controls every RSVP in the occasion |
| AWS/deploy credentials | Out of scope here (covered by OIDC bootstrap docs) |

## Actors

1. **Outsider** — no token. Sees nothing: pages live under unguessable paths,
   the state/RSVP API requires a valid invitation token.
2. **Invitee** — holds an invitation link. Can view RSVP state in the link's
   scope and create RSVPs.
3. **Guest** — an invitee who has RSVP'd and holds their edit key.
4. **Host** — holds the admin key.
5. **Link-holding attacker** — a malicious invitee or someone the link leaked
   to. This is the main adversary the key system defends against.

## Capability rules

| Action | Requires |
|---|---|
| View pages / RSVP lists in a scope | invitation token |
| Create an RSVP under an unused name | invitation token |
| Modify/remove an RSVP with an edit key | that edit key, or the admin key |
| Modify/remove a **keyless** (host-entered) RSVP | admin key only |

## Invariants

**Key lifecycle**

1. Edit/admin keys are 96-bit `secrets`-random base32; format-validated
   (`^[a-z2-7]{16,64}$`) everywhere they cross a trust boundary. Invitation
   tokens are lower-stakes (see the assets table) and trade entropy for
   readability: an optional host-chosen slug plus a 50-bit `secrets`-random
   base32 tail (`visitors-a7k2m6qexz`). The slug carries no authority; the
   50-bit tail alone makes enumeration uneconomical (~10^15 guesses), and
   what a leak exposes is already an accepted residual below.
2. Keys are only ever minted by the server or the `partyplanner` CLI. Every
   mint writes a `KEY#<key>` metadata record; a client-supplied key binds to
   a new row only if that record exists (i.e. this occasion minted it), and
   any other value gets a fresh server-minted key. A key is returned only to
   the writer of a successful non-admin write.
3. One key per person per occasion: the same key is reused and honored across
   all events reachable from the invitation. Because the `KEY#` record
   outlives the rows it owns, a bookmarked edit link keeps working after its
   last RSVP is removed (e.g. fixing a name typo by remove-and-re-add).
   `KEY#` records carry the same TTL as the link that minted them.

**Authorization (enforced server-side, atomically)**

4. Modifying or removing a keyed row requires its edit key or the admin key,
   enforced with DynamoDB condition expressions — a read-then-write race
   cannot bypass ownership.
5. The admin key may modify or remove any row but **never binds a key**: rows
   it creates or edits stay keyless, and keyless rows are host-only — for a
   guest to own the entry, the host removes it and the guest re-RSVPs.
6. Admin authority exists only while the `ADMIN#<key>` metadata record is
   synced; an unsynced admin key has no authority anywhere.

**Secrecy**

7. State responses never contain raw keys — ownership surfaces only as the
   booleans `mine` (per row), `admin`, and `known` (the supplied key was
   minted in this occasion, judged occasion-wide).
8. API responses to admin writes never echo a key.
9. The API never logs request bodies or keys.
10. The admin key never appears in rendered static output; it lives only in
    the occasion's `.links.yaml` (private events repo) and in the host's URL.
11. Pages send `referrer: same-origin`, so tokens and `?me=` keys don't leak
    via outbound links (maps, calendar providers).

**Browser handling of `?me=`**

12. A URL-supplied key is *unvetted* until a state response proves the server
    knows it (`admin` or `known`). The check is occasion-wide, so a bookmark
    still vets on a link whose scope shows none of its rows and after its
    last RSVP was removed. Unvetted keys are never sent with writes, never
    persisted, and are dropped (falling back to the stored key) once
    disproven — including when the state fetch fails.
13. Guest keys persist in `localStorage` only after vetting; the admin key is
    never persisted client-side.

## Accepted residual risks (design tradeoffs, not bugs)

- **Bearer semantics.** Anyone who obtains a link or key has its power. No
  revocation short of re-minting (links) or rotating the admin key.
- **Keys in URLs.** `?me=` bookmarks live in browser history and sync'd
  bookmark stores. Mitigated by the share warning and referrer policy;
  accepted for the bookmarkability it buys. In a browser that already holds
  its own key, opening someone else's edit link acts with their key for that
  visit only — it never replaces the saved key. A browser with no key of its
  own adopts a vetted URL key permanently; that is what makes a bookmark
  portable to a new device, and the client cannot tell an owner's new device
  from a borrower. That includes creation: a new RSVP made during such
  a visit binds to the lender's key (the lender controls it; the visitor's
  own key never can), the same "you are that identity for the visit"
  semantics as shared devices. The remedy is the same too: the host deletes
  the row and the person re-RSVPs without the borrowed link.
- **Name squatting.** A link-holder can RSVP under someone else's name before
  they do. Accepted because names aren't unique to begin with — the social
  graph's trust is load-bearing here, and people who share a name work it out
  themselves without a control required. The claimed-name 403 makes
  collisions visible, and the host can delete a bad-faith squatter.
- **Shared devices share an identity.** The stored key *is* the browser
  profile's identity, so a second person RSVPing under a new name on someone
  else's browser gets their row bound to that browser's key — like sharing a
  logged-in account. The UI makes it visible ("(you)" on the other person's
  rows), and the affected person can re-RSVP from their own device after the
  host clears the row.
- **Keys transit the API as query parameters.** `GET /api/state?me=<key>`
  would appear in server access logs if any were enabled; none are
  (CloudFront and API Gateway access logging are off), and request bodies —
  where writes carry `me` — are never logged. Accepted rather than moving to
  a header, which would complicate CloudFront origin request handling for
  little gain in the current logging posture.
- **Keyless rows need the host to hand over.** Host-entered (and pre-upgrade)
  rows are host-only: a guest can't take over an RSVP the host typed in for
  them until the host removes it and they re-RSVP. Chosen over first-write
  claiming, which let any link-holder grab a host-entered row. The rejection
  message is the same as for someone else's keyed row, so it doesn't reveal
  which rows are host-entered.


## Checklist for changes

Before merging anything touching RSVP or key flows, confirm:

- [ ] No raw key added to any state/read response or rendered output.
- [ ] Every new mutation path enforces ownership with a condition expression.
- [ ] Any new client-side use of `me` respects the vetting rule (invariant 12).
- [ ] Admin responses still echo no key; admin writes still never bind keys.
- [ ] Preview (`preview.py`) and Lambda (`handler.py`) semantics stay in lockstep.
- [ ] Tests cover the new path's unauthorized case, not just the happy path.
