import json
import threading
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from partyplanner import config
from partyplanner.preview import PreviewError, PreviewStore, completed, make_server, preview_urls
from partyplanner.render import render
from partyplanner.tokens import TOKEN_RE

TOKEN = "fixturepreviewtoken2"
PARTY_TOKEN = "fixturepartytoken222"


def _occasion(**overrides):
    data = {
        "title": "T",
        "domain": "t.example.com",
        "timezone": "America/Chicago",
        "events": [
            {"id": "a", "title": "A", "when": "2026-06-20 15:00"},
            {"id": "b", "title": "B", "when": "2026-06-21 15:00"},
        ],
        "links": [
            {"token": TOKEN, "scope": "all", "prefill_name": "Pat"},
            {"token": PARTY_TOKEN, "scope": ["b"]},
        ],
    }
    data.update(overrides)
    return config.parse(data)


def test_completed_fills_ids_and_tokens_without_touching_complete_ones():
    occasion = _occasion(
        events=[
            {"id": "a", "title": "A", "when": "2026-06-20 15:00"},
            {"title": "Big Party!", "when": "2026-06-21 15:00"},
        ],
        links=[{"token": TOKEN, "scope": "all"}, {"note": "new"}],
    )
    done, notes = completed(occasion)
    assert [e.id for e in done.events] == ["a", "big-party"]
    assert done.links[0].token == TOKEN
    assert TOKEN_RE.match(done.links[1].token)
    assert notes == ["unminted link 'new': using a preview-only token"]


def test_completed_adds_a_link_when_config_has_none():
    done, notes = completed(_occasion(links=[]))
    assert len(done.links) == 1
    assert TOKEN_RE.match(done.links[0].token)
    assert done.resolve_scope(done.links[0]) == ("a", "b")
    assert notes == ["no links in config: added a preview-only link with scope `all`"]


def test_store_state_scope_and_prefill():
    store = PreviewStore(_occasion())
    state = store.state(TOKEN)
    assert set(state["events"]) == {"a", "b"}
    assert state["prefill"] == "Pat"
    assert set(store.state(PARTY_TOKEN)["events"]) == {"b"}
    with pytest.raises(PreviewError, match="unknown link"):
        store.state("nosuchtokenatall22")


def test_store_rsvp_upserts_and_suppresses_prefill():
    store = PreviewStore(_occasion())
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "  pat ", "response": "maybe"})
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes", "party_size": 2})
    rows = store.state(TOKEN)["events"]["a"]
    assert rows == [{"name": "Pat", "response": "yes", "party_size": 2}]
    assert store.state(TOKEN)["prefill"] is None


def test_store_rsvp_enforces_scope_and_validation():
    store = PreviewStore(_occasion())
    with pytest.raises(PreviewError, match="cannot RSVP"):
        store.rsvp({"token": PARTY_TOKEN, "event_id": "a", "name": "X", "response": "yes"})
    with pytest.raises(PreviewError, match="response"):
        store.rsvp({"token": TOKEN, "event_id": "a", "name": "X", "response": "nope"})
    with pytest.raises(PreviewError, match="party_size"):
        store.rsvp(
            {"token": TOKEN, "event_id": "a", "name": "X", "response": "yes", "party_size": 99}
        )
    with pytest.raises(PreviewError, match="name"):
        store.rsvp({"token": TOKEN, "event_id": "a", "name": "", "response": "yes"})


def test_store_excludes_rsvp_none_events():
    occasion = _occasion(
        events=[
            {"id": "a", "title": "A", "when": "2026-06-20 15:00"},
            {"id": "b", "title": "B", "rsvp": "none"},
        ],
    )
    store = PreviewStore(occasion)
    assert set(store.state(TOKEN)["events"]) == {"a"}


def test_preview_urls_lists_landing_and_every_link():
    urls = preview_urls(_occasion(), "http://127.0.0.1:1234")
    assert urls[0] == ("landing page", "http://127.0.0.1:1234/")
    assert urls[1] == ("Pat (scope: all)", f"http://127.0.0.1:1234/i/{TOKEN}/")
    assert urls[2] == ("link #2 (scope: b)", f"http://127.0.0.1:1234/i/{PARTY_TOKEN}/")


def test_server_serves_site_and_api_end_to_end(tmp_path: Path):
    occasion = _occasion()
    render(occasion, tmp_path, tmp_path / "out")
    server = make_server(occasion, tmp_path / "out" / "site", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(f"{base}/i/{TOKEN}/").read().decode()
        assert "A" in page and 'placeholder="Your name"' in page

        body = json.dumps(
            {"token": TOKEN, "event_id": "a", "name": "Sam", "response": "yes", "party_size": 1}
        ).encode()
        req = urllib.request.Request(
            f"{base}/api/rsvp", data=body, headers={"Content-Type": "application/json"}
        )
        assert json.load(urllib.request.urlopen(req)) == {"ok": True}

        state = json.load(urllib.request.urlopen(f"{base}/api/state?t={TOKEN}"))
        assert state["events"]["a"] == [{"name": "Sam", "response": "yes", "party_size": 1}]

        with pytest.raises(HTTPError) as err:
            urllib.request.urlopen(f"{base}/api/state?t=nosuchtokenatall22")
        assert err.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
