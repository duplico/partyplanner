import json

import handler
import pytest


def _rsvp_body(**overrides):
    body = {
        "token": "fixturebbqgroupchat2",
        "event_id": "bbq",
        "name": "Aaron",
        "response": "yes",
        "party_size": 2,
    }
    body.update(overrides)
    return body


def test_parse_rsvp_ok():
    parsed = handler.parse_rsvp(_rsvp_body(name="  Aaron   B "))
    assert parsed["name"] == "Aaron B"
    assert parsed["party_size"] == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"token": "SHOUTING-NOT-A-TOKEN"},
        {"token": "short"},
        {"event_id": "../nope"},
        {"name": ""},
        {"name": "x" * 41},
        {"name": 42},
        {"response": "definitely"},
        {"party_size": -1},
        {"party_size": 11},
        {"party_size": "2"},
        {"party_size": True},
    ],
)
def test_parse_rsvp_rejects(overrides):
    with pytest.raises(handler.BadRequest):
        handler.parse_rsvp(_rsvp_body(**overrides))


def _event(method, path, body=None, query=None):
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def test_handler_routes_state(monkeypatch):
    monkeypatch.setattr(handler, "get_state", lambda token: {"events": {}, "prefill": None})
    result = handler.lambda_handler(_event("GET", "/api/state", query={"t": "sometoken"}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"events": {}, "prefill": None}


def test_handler_routes_rsvp(monkeypatch):
    captured = {}
    monkeypatch.setattr(handler, "put_rsvp", captured.update)
    result = handler.lambda_handler(_event("POST", "/api/rsvp", body=_rsvp_body()), None)
    assert result["statusCode"] == 200
    assert captured["name"] == "Aaron"


def test_handler_bad_json():
    event = _event("POST", "/api/rsvp")
    event["body"] = "{not json"
    result = handler.lambda_handler(event, None)
    assert result["statusCode"] == 400


def test_handler_body_too_large():
    event = _event("POST", "/api/rsvp")
    event["body"] = "x" * 5000
    result = handler.lambda_handler(event, None)
    assert result["statusCode"] == 400


def test_handler_body_too_large_multibyte():
    event = _event("POST", "/api/rsvp")
    event["body"] = "é" * 1500  # 1500 chars but 3000 UTF-8 bytes
    result = handler.lambda_handler(event, None)
    assert result["statusCode"] == 400


def test_handler_unknown_route():
    result = handler.lambda_handler(_event("GET", "/api/nope"), None)
    assert result["statusCode"] == 404


class FakeTable:
    def __init__(self, links=None, rsvps=None):
        self.links = links or {}
        self.rsvps = rsvps or {}
        self.updates = []

    def get_item(self, Key, ConsistentRead=False):
        self.consistent_reads = getattr(self, "consistent_reads", []) + [ConsistentRead]
        item = self.links.get(Key["pk"])
        return {"Item": item} if item else {}

    def query(self, KeyConditionExpression, ExpressionAttributeValues):
        pk = ExpressionAttributeValues[":pk"]
        return {"Items": self.rsvps.get(pk, [])}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class PagingTable(FakeTable):
    def query(self, KeyConditionExpression, ExpressionAttributeValues, ExclusiveStartKey=None):
        pk = ExpressionAttributeValues[":pk"]
        items = self.rsvps.get(pk, [])
        if ExclusiveStartKey is None:
            return {"Items": items[:1], "LastEvaluatedKey": {"pk": pk, "sk": items[0]["name"]}}
        return {"Items": items[1:]}


def test_get_state_follows_pagination(monkeypatch):
    table = PagingTable(
        links={"LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]}},
        rsvps={
            "EVENT#bbq": [
                {"name": "aaron", "response": "yes", "party_size": 1},
                {"name": "chance", "response": "maybe", "party_size": 0},
            ]
        },
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    state = handler.get_state("fixturebbqgroupchat2")
    assert [r["name"] for r in state["events"]["bbq"]] == ["aaron", "chance"]


def test_get_state_prefill_suppressed_after_rsvp(monkeypatch):
    table = FakeTable(
        links={
            "LINK#fixturebbqgroupchat2": {
                "rsvp_events": ["bbq"],
                "prefill_name": "Aaron",
            }
        },
        rsvps={"EVENT#bbq": [{"name": "aaron", "response": "yes", "party_size": 1}]},
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    state = handler.get_state("fixturebbqgroupchat2")
    assert state["prefill"] is None
    assert state["events"]["bbq"][0]["name"] == "aaron"


def test_put_rsvp_scope_enforced(monkeypatch):
    table = FakeTable(links={"LINK#fixturestreamonly222": {"rsvp_events": []}})
    monkeypatch.setattr(handler, "table", lambda: table)
    with pytest.raises(handler.BadRequest, match="cannot RSVP"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(token="fixturestreamonly222")))
    assert table.updates == []


def test_get_link_uses_consistent_read(monkeypatch):
    table = FakeTable(links={"LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]}})
    monkeypatch.setattr(handler, "table", lambda: table)
    assert handler.get_link("fixturebbqgroupchat2") is not None
    assert table.consistent_reads == [True]


def test_put_rsvp_unknown_token(monkeypatch):
    monkeypatch.setattr(handler, "table", lambda: FakeTable())
    with pytest.raises(handler.BadRequest, match="unknown link"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))


def test_put_rsvp_writes_ttl(monkeypatch):
    table = FakeTable(
        links={"LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"], "expires_at": 1234567890}}
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    (update,) = table.updates
    assert update["Key"] == {"pk": "EVENT#bbq", "sk": "NAME#aaron"}
    assert update["ExpressionAttributeValues"][":expires"] == 1234567890
