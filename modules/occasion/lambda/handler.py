"""RSVP API for a partyplanner occasion.

Routes (behind CloudFront, same origin as the static site):
  GET  /api/state?t=<token>[&me=<key>]  -> RSVP lists (+ `mine` flags, `admin`)
  POST /api/rsvp             -> upsert an RSVP; first write mints an edit key

An RSVP row is locked to the edit key minted when it was created (returned as
`me` and echoed back by the client), so only its owner — or the occasion's
admin key — can change or remove it. Rows written before edit keys existed
are claimed by the first write that touches them.

Table layout (single table, on-demand):
  LINK#<token> / META   rsvp_events, prefill_name?, expires_at?
  ADMIN#<key> / META    expires_at?  (host key: edit or remove any RSVP)
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

TOKEN_RE = re.compile(r"^[a-z2-7]{16,64}\Z")
RESPONSES = ("yes", "maybe", "no")
MAX_NAME = 40
MAX_PARTY = 10
MAX_BODY = 2048

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


def get_state(token: str, me: str = "") -> dict:
    link = get_link(token)
    if link is None:
        raise BadRequest("unknown link")
    if me and not TOKEN_RE.match(me):
        me = ""
    admin = is_admin(me)
    events: dict[str, list[dict]] = {}
    prefill = link.get("prefill_name")
    for event_id in link.get("rsvp_events", []):
        items = []
        kwargs = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
            "ExpressionAttributeValues": {":pk": f"EVENT#{event_id}", ":sk": "NAME#"},
        }
        while True:
            page = table().query(**kwargs)
            items.extend(page["Items"])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        rows = [
            {
                "name": item["name"],
                "response": item["response"],
                "party_size": int(item["party_size"]),
                "mine": bool(me) and item.get("edit_key") == me,
            }
            for item in items
        ]
        rows.sort(key=lambda r: (RESPONSES.index(r["response"]), r["name"].casefold()))
        events[event_id] = rows
        if prefill and any(r["name"].casefold() == str(prefill).casefold() for r in rows):
            prefill = None
    return {"events": events, "prefill": prefill, "admin": admin}


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
    if rsvp["remove"]:
        if not (admin or (owner_key and owner_key == me)):
            raise Forbidden("only that RSVP's private edit link (or the host) can remove it")
        table().delete_item(Key=key)
        return {"ok": True}
    if owner_key and owner_key != me and not admin:
        raise Forbidden(
            "that name already has an RSVP here — use your private edit link to change it"
        )
    edit_key = owner_key or (me if me and not admin else mint_key())
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    update = (
        "SET #n = :name, #r = :response, party_size = :party_size, edit_key = :edit_key, "
        "updated_at = :now, via_token = :token, created_at = if_not_exists(created_at, :now)"
    )
    values = {
        ":name": rsvp["name"],
        ":response": rsvp["response"],
        ":party_size": rsvp["party_size"],
        ":edit_key": edit_key,
        ":now": now,
        ":token": rsvp["token"],
    }
    if "expires_at" in link:
        update += ", expires_at = :expires"
        values[":expires"] = link["expires_at"]
    table().update_item(
        Key=key,
        UpdateExpression=update,
        ExpressionAttributeNames={"#n": "name", "#r": "response"},
        ExpressionAttributeValues=values,
    )
    return {"ok": True, "me": edit_key}


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
