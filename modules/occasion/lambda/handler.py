"""RSVP API for a partyplanner occasion.

Routes (behind CloudFront, same origin as the static site):
  GET  /api/state?t=<token>  -> RSVP lists for the link's events + prefill name
  POST /api/rsvp             -> upsert an RSVP (same name = edit)

Table layout (single table, on-demand):
  LINK#<token> / META   rsvp_events, prefill_name?, expires_at?
  EVENT#<id> / NAME#<name casefold>   name, response, party_size, timestamps, via_token
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re

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


def get_state(token: str) -> dict:
    link = get_link(token)
    if link is None:
        raise BadRequest("unknown link")
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
            }
            for item in items
        ]
        rows.sort(key=lambda r: (RESPONSES.index(r["response"]), r["name"].casefold()))
        events[event_id] = rows
        if prefill and any(r["name"].casefold() == str(prefill).casefold() for r in rows):
            prefill = None
    return {"events": events, "prefill": prefill}


def put_rsvp(rsvp: dict) -> None:
    link = get_link(rsvp["token"])
    if link is None:
        raise BadRequest("unknown link")
    if rsvp["event_id"] not in link.get("rsvp_events", []):
        raise BadRequest("this link cannot RSVP to that event")
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
    if "expires_at" in link:
        update += ", expires_at = :expires"
        values[":expires"] = link["expires_at"]
    table().update_item(
        Key={"pk": f"EVENT#{rsvp['event_id']}", "sk": f"NAME#{rsvp['name'].casefold()}"},
        UpdateExpression=update,
        ExpressionAttributeNames={"#n": "name", "#r": "response"},
        ExpressionAttributeValues=values,
    )


def lambda_handler(event: dict, _context: object) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    try:
        if method == "GET" and path.endswith("/state"):
            token = (event.get("queryStringParameters") or {}).get("t", "")
            return _response(200, get_state(token))
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
            put_rsvp(parse_rsvp(body))
            return _response(200, {"ok": True})
        return _response(404, {"error": "not found"})
    except BadRequest as e:
        return _response(400, {"error": str(e)})
