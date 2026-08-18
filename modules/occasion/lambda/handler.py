"""RSVP API for a partyplanner occasion.

Routes (behind CloudFront, same origin as the static site):
  GET  /api/state?t=<token>[&me=<key>]  -> RSVP lists (+ `mine` flags, `admin`)
  POST /api/rsvp             -> upsert an RSVP; first write mints an edit key

An RSVP row is locked to the edit key minted when it was created (returned as
`me` and echoed back by the client), so only its owner — or the occasion's
admin key — can change or remove it. Keyless rows (host-entered, or written
before edit keys existed) are host-only; admin writes never bind a key.

Table layout (single table, on-demand):
  CONFIG#OCCASION / META  max_rsvps_per_event, expires_at?  (synced by sync-links)
  LINK#<token> / META   rsvp_events, prefill_name?, expires_at?
  ADMIN#<key> / META    expires_at?  (host key: edit or remove any RSVP)
  KEY#<edit_key> / META expires_at?  (written at mint; outlives the rows the
                                      key owns, so a bookmark survives
                                      remove-then-re-RSVP)
  EVENT#<id> / NAME#<name casefold>   name, response, party_size, edit_key,
                                      timestamps, via_token
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import secrets

import boto3
from botocore.exceptions import ClientError

TOKEN_RE = re.compile(r"^[a-z2-7]{16,64}\Z")
RESPONSES = ("yes", "maybe", "no")
MAX_NAME = 40
MAX_PARTY = 10
MAX_BODY = 2048
DEFAULT_MAX_EVENT_RSVPS = 200

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        "body": json.dumps(body),
    }


class BadRequest(Exception):
    pass


class Forbidden(Exception):
    pass


def mint_key() -> str:
    return base64.b32encode(secrets.token_bytes(12)).decode("ascii").rstrip("=").lower()


def key_is_minted(me: str) -> bool:
    """True if this key was minted in this occasion (KEY# record exists).

    Strongly consistent so a key minted by a just-committed first RSVP is
    recognized by an immediate second one.
    """
    item = table().get_item(Key={"pk": f"KEY#{me}", "sk": "META"}, ConsistentRead=True).get("Item")
    return item is not None


def is_admin(me: str | None) -> bool:
    if not me:
        return False
    item = (
        table().get_item(Key={"pk": f"ADMIN#{me}", "sk": "META"}, ConsistentRead=True).get("Item")
    )
    return item is not None


def parse_rsvp(body: dict) -> dict:
    """Validate a POST /api/rsvp payload; returns normalized fields."""
    token = body.get("token")
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        raise BadRequest("bad token")
    event_id = body.get("event_id")
    if not isinstance(event_id, str) or not re.match(r"^[a-z0-9-]{1,64}\Z", event_id):
        raise BadRequest("bad event_id")
    name = body.get("name")
    if not isinstance(name, str):
        raise BadRequest("bad name")
    name = " ".join(name.split())
    if not 1 <= len(name) <= MAX_NAME:
        raise BadRequest(f"name must be 1-{MAX_NAME} characters")
    if not name.isprintable():
        raise BadRequest("name contains unprintable characters")
    me = body.get("me")
    if me is not None and (not isinstance(me, str) or not TOKEN_RE.match(me)):
        raise BadRequest("bad me")
    remove = body.get("remove", False)
    if not isinstance(remove, bool):
        raise BadRequest("bad remove")
    if remove:
        return {"token": token, "event_id": event_id, "name": name, "me": me, "remove": True}
    response = body.get("response")
    if response not in RESPONSES:
        raise BadRequest("response must be yes, maybe, or no")
    party_size = body.get("party_size", 0)
    if not isinstance(party_size, int) or isinstance(party_size, bool):
        raise BadRequest("bad party_size")
    if not 0 <= party_size <= MAX_PARTY:
        raise BadRequest(f"party_size must be 0-{MAX_PARTY}")
    return {
        "token": token,
        "event_id": event_id,
        "name": name,
        "response": response,
        "party_size": party_size,
        "me": me,
        "remove": False,
    }


def get_link(token: str) -> dict | None:
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return None
    # Strongly consistent so a just-revoked or just-restricted link takes
    # effect immediately after sync-links.
    item = (
        table().get_item(Key={"pk": f"LINK#{token}", "sk": "META"}, ConsistentRead=True).get("Item")
    )
    return item


def max_event_rsvps() -> int:
    """Occasion-configured cap on RSVP rows per event.

    Strongly consistent so a just-lowered cap takes effect immediately
    after sync-links.
    """
    item = (
        table()
        .get_item(Key={"pk": "CONFIG#OCCASION", "sk": "META"}, ConsistentRead=True)
        .get("Item")
    )
    if item is None:
        return DEFAULT_MAX_EVENT_RSVPS
    return int(item.get("max_rsvps_per_event", DEFAULT_MAX_EVENT_RSVPS))


def event_rows(event_id: str, limit: int, consistent: bool = False) -> list[dict]:
    """Up to `limit` NAME# rows for an event, bounding work per request.

    `consistent` is for limit enforcement; display reads tolerate staleness.
    """
    items: list[dict] = []
    kwargs = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
        "ExpressionAttributeValues": {":pk": f"EVENT#{event_id}", ":sk": "NAME#"},
        "Limit": limit,
        "ConsistentRead": consistent,
    }
    while len(items) < limit:
        page = table().query(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        kwargs["Limit"] = limit - len(items)
    return items[:limit]


def event_row_count(event_id: str, max_pages: int = 5) -> int:
    """Total NAME# rows for an event, counted without loading items.

    COUNT pages carry no item data, so a small page bound covers tens of
    thousands of rows while keeping the work per request bounded.
    """
    total = 0
    kwargs = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
        "ExpressionAttributeValues": {":pk": f"EVENT#{event_id}", ":sk": "NAME#"},
        "Select": "COUNT",
    }
    for _ in range(max_pages):
        page = table().query(**kwargs)
        total += page["Count"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return total


def get_state(token: str, me: str = "") -> dict:
    link = get_link(token)
    if link is None:
        raise BadRequest("unknown link")
    if me and not TOKEN_RE.match(me):
        me = ""
    admin = is_admin(me)
    # Occasion-wide: a key can be known here even when none of its rows are
    # in this link's scope (disjoint-scope links, or all rows removed).
    known = admin or (bool(me) and key_is_minted(me))
    events: dict[str, list[dict]] = {}
    more: dict[str, int] = {}
    prefill = link.get("prefill_name")
    cap = max_event_rsvps()
    for event_id in link.get("rsvp_events", []):
        rows = [
            {
                "name": item["name"],
                "response": item["response"],
                "party_size": int(item["party_size"]),
                "mine": bool(me) and item.get("edit_key") == me,
            }
            for item in event_rows(event_id, cap)
        ]
        # Host-entered rows can exceed the cap; tell the client how many
        # names the truncated list is hiding.
        if len(rows) >= cap:
            hidden = event_row_count(event_id) - cap
            if hidden > 0:
                more[event_id] = hidden
        rows.sort(key=lambda r: (RESPONSES.index(r["response"]), r["name"].casefold()))
        events[event_id] = rows
        if prefill and any(r["name"].casefold() == str(prefill).casefold() for r in rows):
            prefill = None
    return {"events": events, "more": more, "prefill": prefill, "admin": admin, "known": known}


def put_rsvp(rsvp: dict) -> dict:
    link = get_link(rsvp["token"])
    if link is None:
        raise BadRequest("unknown link")
    if rsvp["event_id"] not in link.get("rsvp_events", []):
        raise BadRequest("this link cannot RSVP to that event")
    me = rsvp["me"]
    admin = is_admin(me)
    key = {"pk": f"EVENT#{rsvp['event_id']}", "sk": f"NAME#{rsvp['name'].casefold()}"}
    existing = table().get_item(Key=key, ConsistentRead=True).get("Item")
    owner_key = existing.get("edit_key") if existing else None
    forbidden = Forbidden("only that RSVP's private edit link (or the host) can remove it")
    if rsvp["remove"]:
        if not (admin or (owner_key and owner_key == me)):
            raise forbidden
        kwargs: dict = {"Key": key}
        if not admin:
            kwargs["ConditionExpression"] = "edit_key = :me"
            kwargs["ExpressionAttributeValues"] = {":me": me}
        try:
            table().delete_item(**kwargs)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise forbidden from e
            raise
        return {"ok": True}
    # Only the row's owner key or the host may touch an existing row; a
    # keyless (host-entered) row is host-only. The message is the same either
    # way so it doesn't reveal whether a row is host-entered.
    if existing is not None and not admin and (not owner_key or owner_key != me):
        raise Forbidden(
            "that name already has an RSVP here — use your private edit link to change it"
        )
    # The cap doesn't bind the host, and a key that already owns a row here
    # may exceed it by one so a rename (create-then-remove) works at a full
    # event without letting any one key grow the list unboundedly.
    if existing is None and not admin:
        cap = max_event_rsvps()
        rows = event_rows(rsvp["event_id"], cap + 1, consistent=True)
        allowance = 1 if me and any(r.get("edit_key") == me for r in rows) else 0
        if len(rows) >= cap + allowance:
            raise BadRequest("this event's RSVP list is full")
    # A supplied key binds to a new row only if this occasion minted it;
    # anything else gets a fresh mint, recorded so the key stays honored
    # even after its last row is removed.
    if admin or owner_key:
        edit_key = owner_key
    elif me and key_is_minted(me):
        edit_key = me
    else:
        edit_key = mint_key()
        record = {"pk": f"KEY#{edit_key}", "sk": "META"}
        if "expires_at" in link:
            record["expires_at"] = link["expires_at"]
        table().put_item(Item=record)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    update = (
        "SET #n = :name, #r = :response, party_size = :party_size, "
        "updated_at = :now, via_token = :token, created_at = if_not_exists(created_at, :now)"
    )
    values = {
        ":name": rsvp["name"],
        ":response": rsvp["response"],
        ":party_size": rsvp["party_size"],
        ":now": now,
        ":token": rsvp["token"],
    }
    if edit_key:
        update += ", edit_key = :edit_key"
        values[":edit_key"] = edit_key
    if "expires_at" in link:
        update += ", expires_at = :expires"
        values[":expires"] = link["expires_at"]
    kwargs = {}
    if not admin:
        if owner_key:
            kwargs["ConditionExpression"] = "edit_key = :owner"
            values[":owner"] = owner_key
        else:
            kwargs["ConditionExpression"] = "attribute_not_exists(pk)"
    try:
        table().update_item(
            Key=key,
            UpdateExpression=update,
            ExpressionAttributeNames={"#n": "name", "#r": "response"},
            ExpressionAttributeValues=values,
            **kwargs,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Forbidden(
                "that name already has an RSVP here — use your private edit link to change it"
            ) from e
        raise
    result = {"ok": True}
    if edit_key and not admin:
        result["me"] = edit_key
    return result


def lambda_handler(event: dict, _context: object) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    try:
        if method == "GET" and path.endswith("/state"):
            params = event.get("queryStringParameters") or {}
            return _response(200, get_state(params.get("t", ""), params.get("me", "")))
        if method == "POST" and path.endswith("/rsvp"):
            raw = event.get("body") or ""
            if len(raw.encode("utf-8")) > MAX_BODY:
                raise BadRequest("body too large")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as e:
                raise BadRequest("invalid JSON") from e
            if not isinstance(body, dict):
                raise BadRequest("invalid JSON")
            return _response(200, put_rsvp(parse_rsvp(body)))
        return _response(404, {"error": "not found"})
    except BadRequest as e:
        return _response(400, {"error": str(e)})
    except Forbidden as e:
        return _response(403, {"error": str(e)})
