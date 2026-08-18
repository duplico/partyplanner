import json

import handler
import pytest
from botocore.exceptions import ClientError


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
    assert parsed["me"] is None
    assert parsed["remove"] is False


def test_parse_rsvp_remove_skips_response_fields():
    parsed = handler.parse_rsvp(
        {
            "token": "fixturebbqgroupchat2",
            "event_id": "bbq",
            "name": "Aaron",
            "me": "fixtureeditkey222222",
            "remove": True,
        }
    )
    assert parsed["remove"] is True
    assert parsed["me"] == "fixtureeditkey222222"


@pytest.mark.parametrize(
    "overrides",
    [
        {"token": "SHOUTING-NOT-A-TOKEN"},
        {"token": "short"},
        {"event_id": "../nope"},
        {"name": ""},
        {"name": "x" * 41},
        {"name": 42},
        {"name": "evil\u202egnp.exe"},  # RTL-override spoofing
        {"name": "zero\u200bwidth"},
        {"response": "definitely"},
        {"party_size": -1},
        {"party_size": 11},
        {"party_size": "2"},
        {"party_size": True},
        {"me": "SHOUTING-NOT-A-KEY"},
        {"me": 42},
        {"remove": "yes"},
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
    monkeypatch.setattr(handler, "get_state", lambda token, me: {"events": {}, "prefill": None})
    result = handler.lambda_handler(_event("GET", "/api/state", query={"t": "sometoken"}), None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"events": {}, "prefill": None}
    assert result["headers"]["X-Content-Type-Options"] == "nosniff"
    assert result["headers"]["Cache-Control"] == "no-store"


def test_handler_routes_rsvp(monkeypatch):
    captured = {}

    def fake_put(rsvp):
        captured.update(rsvp)
        return {"ok": True, "me": "fixtureeditkey222222"}

    monkeypatch.setattr(handler, "put_rsvp", fake_put)
    result = handler.lambda_handler(_event("POST", "/api/rsvp", body=_rsvp_body()), None)
    assert result["statusCode"] == 200
    assert captured["name"] == "Aaron"
    assert json.loads(result["body"])["me"] == "fixtureeditkey222222"


def test_handler_forbidden_is_403(monkeypatch):
    def fake_put(rsvp):
        raise handler.Forbidden("nope")

    monkeypatch.setattr(handler, "put_rsvp", fake_put)
    result = handler.lambda_handler(_event("POST", "/api/rsvp", body=_rsvp_body()), None)
    assert result["statusCode"] == 403
    assert json.loads(result["body"]) == {"error": "nope"}


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
        self.rows = {}
        for pk, items in self.rsvps.items():
            for item in items:
                self.rows[(pk, "NAME#" + item["name"].casefold())] = item
        self.updates = []
        self.deletes = []
        self.consistent_reads = []
        self.puts = []

    def get_item(self, Key, ConsistentRead=False):
        self.consistent_reads.append(ConsistentRead)
        if Key["sk"] == "META":
            item = self.links.get(Key["pk"])
        else:
            item = self.rows.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def query(
        self,
        KeyConditionExpression,
        ExpressionAttributeValues,
        Limit=None,
        ExclusiveStartKey=None,
        ConsistentRead=False,
        Select=None,
    ):
        pk = ExpressionAttributeValues[":pk"]
        items = self.rsvps.get(pk, [])
        if Select == "COUNT":
            return {"Count": len(items)}
        if Limit is not None and len(items) > Limit:
            return {
                "Items": items[:Limit],
                "LastEvaluatedKey": {"pk": pk, "sk": items[Limit - 1]["name"]},
            }
        return {"Items": items}

    def put_item(self, Item):
        self.puts.append(Item)
        if Item["sk"] == "META":
            self.links[Item["pk"]] = Item
        else:
            self.rows[(Item["pk"], Item["sk"])] = Item

    @staticmethod
    def _condition_failed():
        return ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "nope"}},
            "UpdateItem",
        )

    def _check_condition(self, row, condition, values):
        if condition is None:
            return
        if condition == "attribute_not_exists(pk)":
            if row is not None:
                raise self._condition_failed()
        elif condition == "edit_key = :owner":
            if row is None or row.get("edit_key") != values[":owner"]:
                raise self._condition_failed()
        elif condition == "edit_key = :me":
            if row is None or row.get("edit_key") != values[":me"]:
                raise self._condition_failed()
        else:
            raise AssertionError(f"unexpected condition {condition!r}")

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        values = kwargs["ExpressionAttributeValues"]
        key = (kwargs["Key"]["pk"], kwargs["Key"]["sk"])
        self._check_condition(self.rows.get(key), kwargs.get("ConditionExpression"), values)
        row = self.rows.setdefault(key, {"created_at": values[":now"]})
        row.update(
            {
                "name": values[":name"],
                "response": values[":response"],
                "party_size": values[":party_size"],
            }
        )
        if ":edit_key" in values:
            row["edit_key"] = values[":edit_key"]

    def delete_item(self, **kwargs):
        key = (kwargs["Key"]["pk"], kwargs["Key"]["sk"])
        self._check_condition(
            self.rows.get(key),
            kwargs.get("ConditionExpression"),
            kwargs.get("ExpressionAttributeValues", {}),
        )
        self.deletes.append(kwargs["Key"])
        self.rows.pop(key, None)


class PagingTable(FakeTable):
    def query(
        self,
        KeyConditionExpression,
        ExpressionAttributeValues,
        Limit=None,
        ExclusiveStartKey=None,
        ConsistentRead=False,
        Select=None,
    ):
        pk = ExpressionAttributeValues[":pk"]
        items = self.rsvps.get(pk, [])
        if Select == "COUNT":
            return {"Count": len(items)}
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
    assert state["more"] == {}
    assert state["admin"] is False


def test_get_state_bounds_rows_per_event(monkeypatch):
    over = handler.DEFAULT_MAX_EVENT_RSVPS + 50
    table = FakeTable(
        links={"LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]}},
        rsvps={
            "EVENT#bbq": [
                {"name": f"guest {i:04d}", "response": "yes", "party_size": 0} for i in range(over)
            ]
        },
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    state = handler.get_state("fixturebbqgroupchat2")
    assert len(state["events"]["bbq"]) == handler.DEFAULT_MAX_EVENT_RSVPS
    assert state["more"]["bbq"] == 50


def test_put_rsvp_rejects_new_name_when_event_full(monkeypatch):
    full = [
        {"name": f"guest {i:04d}", "response": "yes", "party_size": 0}
        for i in range(handler.DEFAULT_MAX_EVENT_RSVPS)
    ]
    full[0]["edit_key"] = "fixtureeditkey222222"
    table = FakeTable(
        links={"LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]}},
        rsvps={"EVENT#bbq": full},
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    with pytest.raises(handler.BadRequest, match="full"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(name="One Too Many")))
    # existing rows can still be edited and removed at the cap
    handler.put_rsvp(
        handler.parse_rsvp(_rsvp_body(name="guest 0000", response="no", me="fixtureeditkey222222"))
    )
    assert table.rows[("EVENT#bbq", "NAME#guest 0000")]["response"] == "no"
    handler.put_rsvp(
        handler.parse_rsvp(_rsvp_body(name="guest 0000", remove=True, me="fixtureeditkey222222"))
    )
    assert ("EVENT#bbq", "NAME#guest 0000") not in table.rows


def test_rsvp_cap_exempts_admin(monkeypatch):
    full = [
        {"name": f"guest {i:04d}", "response": "yes", "party_size": 0}
        for i in range(handler.DEFAULT_MAX_EVENT_RSVPS)
    ]
    table = FakeTable(
        links={
            "LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]},
            "ADMIN#fixtureadminkey22222": {"sk": "META"},
        },
        rsvps={"EVENT#bbq": full},
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    result = handler.put_rsvp(
        handler.parse_rsvp(_rsvp_body(name="Late Addition", me="fixtureadminkey22222"))
    )
    assert result["ok"] is True
    assert ("EVENT#bbq", "NAME#late addition") in table.rows


def test_rsvp_cap_allows_one_extra_for_row_owner(monkeypatch):
    # A rename is create-then-remove, so a key that owns a row may go one over
    # the cap — but only one.
    full = [
        {"name": f"guest {i:04d}", "response": "yes", "party_size": 0}
        for i in range(handler.DEFAULT_MAX_EVENT_RSVPS)
    ]
    full[0]["edit_key"] = "fixtureeditkey222222"
    table = FakeTable(
        links={
            "LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]},
            "KEY#fixtureeditkey222222": {"sk": "META"},
        },
        rsvps={"EVENT#bbq": full},
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    handler.put_rsvp(
        handler.parse_rsvp(_rsvp_body(name="Renamed Guest", me="fixtureeditkey222222"))
    )
    assert table.rows[("EVENT#bbq", "NAME#renamed guest")]["edit_key"] == "fixtureeditkey222222"
    table.rsvps["EVENT#bbq"] = full + [table.rows[("EVENT#bbq", "NAME#renamed guest")]]
    with pytest.raises(handler.BadRequest, match="full"):
        handler.put_rsvp(
            handler.parse_rsvp(_rsvp_body(name="Another Extra", me="fixtureeditkey222222"))
        )


def test_rsvp_cap_is_occasion_configurable(monkeypatch):
    table = FakeTable(
        links={
            "LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq"]},
            "CONFIG#OCCASION": {"max_rsvps_per_event": 2},
        },
        rsvps={
            "EVENT#bbq": [
                {"name": "guest a", "response": "yes", "party_size": 0},
                {"name": "guest b", "response": "yes", "party_size": 0},
                {"name": "guest c", "response": "yes", "party_size": 0},
            ]
        },
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    state = handler.get_state("fixturebbqgroupchat2")
    assert len(state["events"]["bbq"]) == 2
    with pytest.raises(handler.BadRequest, match="full"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(name="One Too Many")))


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
    (key_record,) = table.puts
    assert key_record["expires_at"] == 1234567890


LINKS = {"LINK#fixturebbqgroupchat2": {"rsvp_events": ["bbq", "pool"]}}
ADMIN_KEY = "fixtureadminkey22222"


def test_put_rsvp_mints_and_reuses_edit_key(monkeypatch):
    table = FakeTable(links=LINKS)
    monkeypatch.setattr(handler, "table", lambda: table)
    first = handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    assert handler.TOKEN_RE.match(first["me"])
    assert table.links[f"KEY#{first['me']}"] == {"pk": f"KEY#{first['me']}", "sk": "META"}
    # the owner's key updates the row and spans other events under the link
    same = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(response="no", me=first["me"])))
    assert same["me"] == first["me"]
    other_event = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(event_id="pool", me=first["me"])))
    assert other_event["me"] == first["me"]
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["response"] == "no"


def test_minted_key_survives_remove_then_rersvp(monkeypatch):
    # a bookmarked edit link keeps working after its last RSVP is removed
    table = FakeTable(links=LINKS)
    monkeypatch.setattr(handler, "table", lambda: table)
    owner = handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body(remove=True, me=owner["me"])))
    again = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(name="Aaron B", me=owner["me"])))
    assert again["me"] == owner["me"]
    assert table.rows[("EVENT#bbq", "NAME#aaron b")]["edit_key"] == owner["me"]


def test_put_rsvp_rejects_wrong_or_missing_key(monkeypatch):
    table = FakeTable(links=LINKS)
    monkeypatch.setattr(handler, "table", lambda: table)
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    with pytest.raises(handler.Forbidden, match="private edit link"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(response="no")))
    stranger = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(name="Sam")))
    with pytest.raises(handler.Forbidden, match="private edit link"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(response="no", me=stranger["me"])))
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["response"] == "yes"


def test_keyless_row_is_host_only(monkeypatch):
    table = FakeTable(
        links={**LINKS, f"ADMIN#{ADMIN_KEY}": {"sk": "META"}},
        rsvps={"EVENT#bbq": [{"name": "Aaron", "response": "maybe", "party_size": 0}]},
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    with pytest.raises(handler.Forbidden, match="private edit link"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["response"] == "maybe"
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body(response="no", me=ADMIN_KEY)))
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["response"] == "no"
    assert "edit_key" not in table.rows[("EVENT#bbq", "NAME#aaron")]


def test_unknown_key_never_binds_to_new_row(monkeypatch):
    # keys are only ever server-minted: a client-chosen key (including a
    # minted-but-unsynced admin key) never becomes a row's edit key
    table = FakeTable(links=LINKS)
    monkeypatch.setattr(handler, "table", lambda: table)
    chosen = "strangerchosenkey222"
    fresh = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(me=chosen)))
    assert fresh["me"] != chosen
    assert handler.TOKEN_RE.match(fresh["me"])
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["edit_key"] == fresh["me"]


def test_admin_key_edits_and_removes_any_row(monkeypatch):
    table = FakeTable(links={**LINKS, f"ADMIN#{ADMIN_KEY}": {"sk": "META"}})
    monkeypatch.setattr(handler, "table", lambda: table)
    owner = handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    edited = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(response="no", me=ADMIN_KEY)))
    assert "me" not in edited  # never echo the guest's key to the admin
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["edit_key"] == owner["me"]
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body(remove=True, me=ADMIN_KEY)))
    assert table.deletes == [{"pk": "EVENT#bbq", "sk": "NAME#aaron"}]
    assert ("EVENT#bbq", "NAME#aaron") not in table.rows


def test_admin_write_never_binds_a_key(monkeypatch):
    table = FakeTable(
        links={**LINKS, f"ADMIN#{ADMIN_KEY}": {"sk": "META"}},
        rsvps={"EVENT#bbq": [{"name": "Aaron", "response": "maybe", "party_size": 0}]},
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    result = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(response="no", me=ADMIN_KEY)))
    assert "me" not in result
    assert "edit_key" not in table.rows[("EVENT#bbq", "NAME#aaron")]
    created = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(name="Sam", me=ADMIN_KEY)))
    assert "me" not in created
    assert "edit_key" not in table.rows[("EVENT#bbq", "NAME#sam")]


def test_put_rsvp_lost_race_maps_to_forbidden(monkeypatch):
    class RacyTable(FakeTable):
        def get_item(self, Key, ConsistentRead=False):
            if Key["sk"] != "META":
                return {}  # reads see no row, but the write finds one
            return super().get_item(Key, ConsistentRead)

    table = RacyTable(
        links=LINKS,
        rsvps={
            "EVENT#bbq": [
                {
                    "name": "Aaron",
                    "response": "yes",
                    "party_size": 0,
                    "edit_key": "fixtureeditkey222222",
                }
            ]
        },
    )
    monkeypatch.setattr(handler, "table", lambda: table)
    with pytest.raises(handler.Forbidden, match="private edit link"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    assert table.rows[("EVENT#bbq", "NAME#aaron")]["response"] == "yes"


def test_remove_requires_owner_key(monkeypatch):
    table = FakeTable(links=LINKS)
    monkeypatch.setattr(handler, "table", lambda: table)
    owner = handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    stranger = handler.put_rsvp(handler.parse_rsvp(_rsvp_body(name="Sam")))
    with pytest.raises(handler.Forbidden, match="remove"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(remove=True)))
    with pytest.raises(handler.Forbidden, match="remove"):
        handler.put_rsvp(handler.parse_rsvp(_rsvp_body(remove=True, me=stranger["me"])))
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body(remove=True, me=owner["me"])))
    assert ("EVENT#bbq", "NAME#aaron") not in table.rows


def test_get_state_marks_mine_and_admin_without_leaking_keys(monkeypatch):
    table = FakeTable(links={**LINKS, f"ADMIN#{ADMIN_KEY}": {"sk": "META"}})
    monkeypatch.setattr(handler, "table", lambda: table)
    owner = handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    table.rsvps = {"EVENT#bbq": [table.rows[("EVENT#bbq", "NAME#aaron")]], "EVENT#pool": []}
    state = handler.get_state("fixturebbqgroupchat2", owner["me"])
    (row,) = state["events"]["bbq"]
    assert row["mine"] is True
    assert "edit_key" not in row
    assert state["admin"] is False
    assert state["known"] is True
    admin_state = handler.get_state("fixturebbqgroupchat2", ADMIN_KEY)
    assert admin_state["admin"] is True
    assert admin_state["known"] is True
    assert admin_state["events"]["bbq"][0]["mine"] is False
    anon_state = handler.get_state("fixturebbqgroupchat2")
    assert anon_state["events"]["bbq"][0]["mine"] is False
    assert anon_state["known"] is False
    assert handler.get_state("fixturebbqgroupchat2", "strangerchosenkey222")["known"] is False


def test_get_state_knows_key_beyond_link_scope(monkeypatch):
    # a minted key vets occasion-wide: on a disjoint-scope link and after
    # its last RSVP is removed, `known` stays true while no row is `mine`
    table = FakeTable(links={**LINKS, "LINK#fixturepoolonly2222": {"rsvp_events": ["pool"]}})
    monkeypatch.setattr(handler, "table", lambda: table)
    owner = handler.put_rsvp(handler.parse_rsvp(_rsvp_body()))
    state = handler.get_state("fixturepoolonly2222", owner["me"])
    assert state["known"] is True
    assert all(not r["mine"] for rows in state["events"].values() for r in rows)
    handler.put_rsvp(handler.parse_rsvp(_rsvp_body(remove=True, me=owner["me"])))
    assert handler.get_state("fixturebbqgroupchat2", owner["me"])["known"] is True
