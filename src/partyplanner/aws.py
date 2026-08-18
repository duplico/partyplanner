"""Sync link records from an occasion config into the occasion's DynamoDB table.

Link items are the API's source of truth for which tokens are live, what they can
RSVP to, and name prefill. Any record whose token is no longer an active link in
the config (revoked or simply removed) is deleted, which kills the API for it
even if a cached page lingers.
"""

from __future__ import annotations

import datetime as dt

from .config import Occasion

RETENTION = dt.timedelta(days=183)


def link_items(occasion: Occasion) -> list[dict]:
    latest = occasion.latest_end()
    expires = int((latest + RETENTION).timestamp()) if latest else None
    rsvp_open = occasion.rsvp_open_ids()
    items = []
    for link in occasion.links:
        scope = occasion.resolve_scope(link)
        item = {
            "pk": f"LINK#{link.token}",
            "sk": "META",
            "rsvp_events": [i for i in scope if i in rsvp_open],
        }
        if link.prefill_name:
            item["prefill_name"] = link.prefill_name
        if expires:
            item["expires_at"] = expires
        items.append(item)
    if occasion.admin_key:
        item = {"pk": f"ADMIN#{occasion.admin_key}", "sk": "META"}
        if expires:
            item["expires_at"] = expires
        items.append(item)
    config = {
        "pk": "CONFIG#OCCASION",
        "sk": "META",
        "max_rsvps_per_event": occasion.max_rsvps_per_event,
    }
    if expires:
        config["expires_at"] = expires
    items.append(config)
    return items


def _existing_meta_pks(table) -> set[str]:
    pks: set[str] = set()
    kwargs: dict = {}
    while True:
        page = table.scan(ProjectionExpression="pk, sk", **kwargs)
        pks.update(
            str(item["pk"])
            for item in page["Items"]
            if str(item.get("pk", "")).startswith(("LINK#", "ADMIN#")) and item.get("sk") == "META"
        )
        if "LastEvaluatedKey" not in page:
            return pks
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def sync_links(occasion: Occasion, table_name: str) -> tuple[int, int]:
    """Upsert link items for active links; delete records for any other token."""
    import boto3

    table = boto3.resource("dynamodb").Table(table_name)
    items = link_items(occasion)
    active = {item["pk"] for item in items}
    stale = (_existing_meta_pks(table) | {f"LINK#{t}" for t in occasion.revoked}) - active
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
        for pk in sorted(stale):
            batch.delete_item(Key={"pk": pk, "sk": "META"})
    links = sum(1 for item in items if str(item["pk"]).startswith("LINK#"))
    return links, len(stale)


def export_rsvps(table_name: str) -> list[dict]:
    """Dump all RSVP rows (the 'reporting layer')."""
    import boto3

    table = boto3.resource("dynamodb").Table(table_name)
    rows = []
    kwargs: dict = {}
    while True:
        page = table.scan(**kwargs)
        for item in page["Items"]:
            if str(item.get("pk", "")).startswith("EVENT#"):
                rows.append(
                    {
                        "event_id": str(item["pk"]).removeprefix("EVENT#"),
                        "name": item.get("name"),
                        "response": item.get("response"),
                        "party_size": int(item.get("party_size", 0)),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                        "via_token": item.get("via_token"),
                    }
                )
        if "LastEvaluatedKey" not in page:
            return sorted(rows, key=lambda r: (r["event_id"], r["name"] or ""))
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
