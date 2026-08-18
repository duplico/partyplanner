# Security model

Partyplanner is deliberately account-free: every authority in the system is a
**bearer capability** — an unguessable token whose possession *is* the
permission. This document is the reference model; any behavior that violates an
invariant here is a bug, and any proposed change should be checked against it.

## Assets

| Asset | Sensitivity |
|---|---|
| RSVP data (names, responses, party sizes) | Semi-public: visible to everyone holding an invitation link in scope |
| Invitation token (`/i/<token>/`) | Secret shared with an audience; leaking it exposes the pages and RSVP lists in its scope |
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
| Create an RSVP under an unclaimed name | invitation token |
| Modify/remove an RSVP with an edit key | that edit key, or the admin key |
| Modify/remove/claim a **keyless** RSVP | invitation token (first ordinary write claims it) or admin key (never claims) |

## Invariants

**Key lifecycle**

1. Keys are 96-bit `secrets`-random base32; format-validated (`^[a-z2-7]{16,64}$`)
   everywhere they cross a trust boundary.
2. A guest edit key is bound to a row **by the server**, and returned only to
   the writer of a successful non-admin write. Clients never pick which key a
   row gets bound to on the victim's behalf (see "unvetted keys" below).
3. One key per person per occasion: the same key is reused and honored across
   all events reachable from the invitation.

**Authorization (enforced server-side, atomically)**

4. Modifying or removing a keyed row requires its edit key or the admin key,
   enforced with DynamoDB condition expressions — a read-then-write race
   cannot bypass ownership.
5. The admin key may modify or remove any row but **never claims one**: rows
   it creates or edits stay keyless so the real guest can claim them.
6. Admin authority exists only while the `ADMIN#<key>` metadata record is
   synced; an unsynced admin key has no authority anywhere.

**Secrecy**

7. State responses never contain raw keys — ownership surfaces only as the
   boolean `mine`, admin status only as the boolean `admin`.
8. API responses to admin writes never echo a key.
9. The API never logs request bodies or keys.
10. The admin key never appears in rendered static output; it lives only in
    the occasion's `.links.yaml` (private events repo) and in the host's URL.
11. Pages send `referrer: same-origin`, so tokens and `?me=` keys don't leak
    via outbound links (maps, calendar providers).

**Browser handling of `?me=`**

12. A URL-supplied key is *unvetted* until a state response proves it is the
    visitor's (it is admin, or it owns a row). Unvetted keys are never sent
    with writes, never persisted, and are dropped (falling back to the stored
    key) once disproven — including when the state fetch fails.
13. Guest keys persist in `localStorage` only after vetting; the admin key is
    never persisted client-side.

## Accepted residual risks (design tradeoffs, not bugs)

- **Bearer semantics.** Anyone who obtains a link or key has its power. No
  revocation short of re-minting (links) or rotating the admin key.
- **Keys in URLs.** `?me=` bookmarks live in browser history and sync'd
  bookmark stores. Mitigated by the share warning and referrer policy;
  accepted for the bookmarkability it buys.
- **Name squatting.** A link-holder can RSVP under someone else's name before
  they do. The claimed-name 403 makes it visible, and the host can delete the
  squatter. Accepted: the audience is a trusted social circle.
- **Keyless-row claims.** Pre-upgrade rows and host-entered rows are claimable
  by the first ordinary write from a link-holder. The alternative (locking
  them) would strand guests whose RSVP the host typed in for them. The host
  can always repair a hijacked row.
- **Server-side `me` adoption.** A *direct API caller* creating a new row may
  supply their own key value rather than receiving a minted one. They only
  ever gain control of a row they created themselves, so no one else's
  authority is affected; the browser client never does this (invariant 12).

## Checklist for changes

Before merging anything touching RSVP or key flows, confirm:

- [ ] No raw key added to any state/read response or rendered output.
- [ ] Every new mutation path enforces ownership with a condition expression.
- [ ] Any new client-side use of `me` respects the vetting rule (invariant 12).
- [ ] Admin responses still echo no key; admin writes still never claim rows.
- [ ] Preview (`preview.py`) and Lambda (`handler.py`) semantics stay in lockstep.
- [ ] Tests cover the new path's unauthorized case, not just the happy path.
