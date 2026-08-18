import json
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest

from partyplanner import config
from partyplanner.preview import (
    PreviewError,
    PreviewForbidden,
    PreviewStore,
    Reloader,
    ReloadState,
    completed,
    make_server,
    preview_urls,
)
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
    assert notes == [
        "unminted link 'new': using a preview-only token",
        "no admin key: using a preview-only one",
    ]


def test_completed_adds_a_link_when_config_has_none():
    done, notes = completed(_occasion(links=[]))
    assert len(done.links) == 1
    assert TOKEN_RE.match(done.links[0].token)
    assert done.resolve_scope(done.links[0]) == ("a", "b")
    assert notes == [
        "no links in config: added a preview-only link with scope `all`",
        "no admin key: using a preview-only one",
    ]


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
    first = store.rsvp({"token": TOKEN, "event_id": "a", "name": "  pat ", "response": "maybe"})
    assert TOKEN_RE.match(first["me"])
    store.rsvp(
        {
            "token": TOKEN,
            "event_id": "a",
            "name": "Pat",
            "response": "yes",
            "party_size": 2,
            "me": first["me"],
        }
    )
    rows = store.state(TOKEN)["events"]["a"]
    assert rows == [{"name": "Pat", "response": "yes", "party_size": 2, "mine": False}]
    assert store.state(TOKEN)["prefill"] is None
    assert store.state(TOKEN, me=first["me"])["events"]["a"][0]["mine"] is True


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


ADMIN_KEY = "fixtureadminkey22222"


def test_store_edit_key_protects_existing_rsvps():
    store = PreviewStore(_occasion())
    first = store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes"})
    with pytest.raises(PreviewForbidden, match="private edit link"):
        store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "no"})
    other = store.rsvp({"token": TOKEN, "event_id": "a", "name": "Sam", "response": "yes"})
    assert other["me"] != first["me"]
    with pytest.raises(PreviewForbidden, match="private edit link"):
        store.rsvp(
            {"token": TOKEN, "event_id": "a", "name": "Pat", "response": "no", "me": other["me"]}
        )


def test_store_edit_key_spans_events():
    store = PreviewStore(_occasion())
    first = store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes"})
    second = store.rsvp(
        {"token": TOKEN, "event_id": "b", "name": "Pat", "response": "maybe", "me": first["me"]}
    )
    assert second["me"] == first["me"]
    state = store.state(TOKEN, me=first["me"])
    assert state["events"]["a"][0]["mine"] is True
    assert state["events"]["b"][0]["mine"] is True


def test_store_unknown_key_never_binds_to_new_row():
    store = PreviewStore(_occasion())
    chosen = "strangerchosenkey222"
    result = store.rsvp(
        {"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes", "me": chosen}
    )
    assert result["me"] != chosen
    assert TOKEN_RE.match(result["me"])
    assert store.state(TOKEN, me=chosen)["events"]["a"][0]["mine"] is False


def test_store_minted_key_survives_remove_then_rersvp():
    # a bookmarked edit link keeps working after its last RSVP is removed
    store = PreviewStore(_occasion())
    first = store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes"})
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "remove": True, "me": first["me"]})
    again = store.rsvp(
        {"token": TOKEN, "event_id": "a", "name": "Pat B", "response": "yes", "me": first["me"]}
    )
    assert again["me"] == first["me"]
    assert store.state(TOKEN, me=first["me"])["events"]["a"][0]["mine"] is True


def test_store_known_is_occasion_wide():
    # minted keys vet on any link (even disjoint scope) and survive removal
    store = PreviewStore(_occasion(admin_key=ADMIN_KEY))
    first = store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes"})
    assert store.state(PARTY_TOKEN, me=first["me"])["known"] is True
    assert store.state(TOKEN, me=ADMIN_KEY)["known"] is True
    assert store.state(TOKEN)["known"] is False
    assert store.state(TOKEN, me="strangerchosenkey222")["known"] is False
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "remove": True, "me": first["me"]})
    assert store.state(TOKEN, me=first["me"])["known"] is True


def test_store_remove_requires_owner_or_admin():
    store = PreviewStore(_occasion(admin_key=ADMIN_KEY))
    first = store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes"})
    with pytest.raises(PreviewForbidden, match="remove"):
        store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "remove": True})
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "remove": True, "me": first["me"]})
    assert store.state(TOKEN)["events"]["a"] == []

    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Sam", "response": "yes"})
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Sam", "remove": True, "me": ADMIN_KEY})
    assert store.state(TOKEN)["events"]["a"] == []


def test_store_admin_key_edits_anyone_and_flags_state():
    store = PreviewStore(_occasion(admin_key=ADMIN_KEY))
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes"})
    result = store.rsvp(
        {"token": TOKEN, "event_id": "a", "name": "Pat", "response": "no", "me": ADMIN_KEY}
    )
    assert "me" not in result  # never echo the guest's key to the admin
    state = store.state(TOKEN, me=ADMIN_KEY)
    assert state["admin"] is True
    assert state["events"]["a"][0]["response"] == "no"
    assert store.state(TOKEN)["admin"] is False


def test_store_admin_created_row_is_host_only():
    store = PreviewStore(_occasion(admin_key=ADMIN_KEY))
    result = store.rsvp(
        {"token": TOKEN, "event_id": "a", "name": "Pat", "response": "yes", "me": ADMIN_KEY}
    )
    assert "me" not in result
    with pytest.raises(PreviewForbidden, match="private edit link"):
        store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "response": "maybe"})
    assert store.state(TOKEN)["events"]["a"][0]["response"] == "yes"
    edited = store.rsvp(
        {"token": TOKEN, "event_id": "a", "name": "Pat", "response": "no", "me": ADMIN_KEY}
    )
    assert "me" not in edited
    store.rsvp({"token": TOKEN, "event_id": "a", "name": "Pat", "remove": True, "me": ADMIN_KEY})
    assert store.state(TOKEN)["events"]["a"] == []


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


def test_preview_urls_include_admin_view():
    urls = preview_urls(_occasion(admin_key=ADMIN_KEY), "http://127.0.0.1:1234")
    assert urls[-1] == (
        "admin view (edit/remove any RSVP)",
        f"http://127.0.0.1:1234/i/{TOKEN}/?me={ADMIN_KEY}",
    )


def test_server_binds_requested_host(tmp_path: Path):
    occasion = _occasion()
    render(occasion, tmp_path, tmp_path / "out")
    server = make_server(occasion, tmp_path / "out" / "site", port=0, host="0.0.0.0")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0] == "0.0.0.0"
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert urllib.request.urlopen(f"{base}/").status == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
        result = json.load(urllib.request.urlopen(req))
        assert result["ok"] is True
        assert TOKEN_RE.match(result["me"])

        state = json.load(urllib.request.urlopen(f"{base}/api/state?t={TOKEN}"))
        assert state["events"]["a"] == [
            {"name": "Sam", "response": "yes", "party_size": 1, "mine": False}
        ]
        assert state["admin"] is False

        state = json.load(urllib.request.urlopen(f"{base}/api/state?t={TOKEN}&me={result['me']}"))
        assert state["events"]["a"][0]["mine"] is True

        with pytest.raises(HTTPError) as err:
            urllib.request.urlopen(f"{base}/api/state?t=nosuchtokenatall22")
        assert err.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_completed_reuses_preview_tokens_across_reloads():
    occasion = _occasion(links=[{"note": "new"}])
    first, _ = completed(occasion)
    second, notes = completed(occasion, previous=first)
    assert second.links[0].token == first.links[0].token
    assert notes == []


def test_completed_reuses_synthetic_link_token_across_reloads():
    first, _ = completed(_occasion(links=[]))
    second, notes = completed(_occasion(links=[]), previous=first)
    assert second.links[0].token == first.links[0].token
    assert notes == []


def _write_config(config_dir: Path, title: str) -> Path:
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "occasion.yaml"
    config_path.write_text(
        f"""\
title: {title}
domain: t.example.com
timezone: America/Chicago
events:
  - id: a
    title: A
    when: 2026-06-20 15:00
links:
  - token: {TOKEN}
    scope: all
"""
    )
    return config_path


def test_reload_serves_fresh_site_and_bumps_version(tmp_path: Path):
    config_path = _write_config(tmp_path / "cfg", "Before")
    occasion = config.load(config_path)
    out_dir = tmp_path / "out"
    render(occasion, tmp_path / "cfg", out_dir)
    store = PreviewStore(occasion)
    reload_state = ReloadState()
    server = make_server(occasion, out_dir / "site", port=0, store=store, reload_state=reload_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(f"{base}/i/{TOKEN}/").read().decode()
        assert "Before" in page
        assert "var v=0" in page  # auto-refresh script carries the serve-time version
        assert json.load(urllib.request.urlopen(f"{base}/__preview__/version")) == {"version": 0}

        store.rsvp({"token": TOKEN, "event_id": "a", "name": "Sam", "response": "yes"})
        _write_config(tmp_path / "cfg", "After")
        reloader = Reloader(config_path, out_dir, store, reload_state, base, occasion, echo=print)
        assert reloader.reload()

        page = urllib.request.urlopen(f"{base}/i/{TOKEN}/").read().decode()
        assert "After" in page
        assert "var v=1" in page
        assert json.load(urllib.request.urlopen(f"{base}/__preview__/version")) == {"version": 1}
        # RSVPs survive the reload
        assert store.state(TOKEN)["events"]["a"] == [
            {"name": "Sam", "response": "yes", "party_size": 0, "mine": False}
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_reload_keeps_old_site_when_config_is_broken(tmp_path: Path):
    config_path = _write_config(tmp_path / "cfg", "Good")
    occasion = config.load(config_path)
    out_dir = tmp_path / "out"
    render(occasion, tmp_path / "cfg", out_dir)
    store = PreviewStore(occasion)
    reload_state = ReloadState()
    messages: list[str] = []
    config_path.write_text("title: [broken\n")
    reloader = Reloader(
        config_path, out_dir, store, reload_state, "http://x", occasion, echo=messages.append
    )
    assert not reloader.reload()
    assert reload_state.version == 0
    assert any("reload failed" in m for m in messages)
    assert "Good" in (out_dir / "site" / "i" / TOKEN / "index.html").read_text()


def test_watch_triggers_reload_on_change(tmp_path: Path):
    config_path = _write_config(tmp_path / "cfg", "Before")
    occasion = config.load(config_path)
    out_dir = tmp_path / "out"
    render(occasion, tmp_path / "cfg", out_dir)
    reload_state = ReloadState()
    reloader = Reloader(
        config_path,
        out_dir,
        PreviewStore(occasion),
        reload_state,
        "http://x",
        occasion,
        echo=lambda _: None,
    )
    stop = threading.Event()
    thread = threading.Thread(target=reloader.watch, args=(stop, 0.05), daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        _write_config(tmp_path / "cfg", "After")
        deadline = time.monotonic() + 5
        while reload_state.version == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert reload_state.version >= 1
        assert "After" in (out_dir / "site" / "i" / TOKEN / "index.html").read_text()
    finally:
        stop.set()
        thread.join(timeout=5)


def test_completed_new_link_does_not_steal_an_existing_preview_token():
    first, _ = completed(_occasion(links=[{"note": "old"}]))
    old_token = first.links[0].token
    second, notes = completed(_occasion(links=[{"note": "new"}, {"note": "old"}]), previous=first)
    assert second.links[1].token == old_token
    assert second.links[0].token != old_token
    assert notes == ["unminted link 'new': using a preview-only token"]


def test_reload_keeps_old_site_when_render_fails(tmp_path: Path):
    config_dir = tmp_path / "cfg"
    config_path = _write_config(config_dir, "Good")
    (config_dir / "photo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    config_path.write_text(config_path.read_text() + "photo: ./photo.svg\n")
    occasion = config.load(config_path)
    out_dir = tmp_path / "out"
    render(occasion, config_dir, out_dir)
    reload_state = ReloadState()
    messages: list[str] = []
    (config_dir / "photo.svg").unlink()
    reloader = Reloader(
        config_path,
        out_dir,
        PreviewStore(occasion),
        reload_state,
        "http://x",
        occasion,
        echo=messages.append,
    )
    assert not reloader.reload()
    assert reload_state.version == 0
    assert any("reload failed" in m for m in messages)
    assert "Good" in (out_dir / "site" / "i" / TOKEN / "index.html").read_text()


def test_snapshot_watches_dot_files_but_not_dot_directories(tmp_path: Path):
    config_path = _write_config(tmp_path / "cfg", "T")
    (tmp_path / "cfg" / ".links.yaml").write_text("links: []\n")
    occasion = config.load(config_path)
    reloader = Reloader(
        config_path,
        tmp_path / "out",
        PreviewStore(occasion),
        ReloadState(),
        "http://x",
        occasion,
        echo=lambda _: None,
    )
    before = reloader.snapshot()
    assert str(config_path) in before
    assert str(tmp_path / "cfg" / ".links.yaml") in before
    git = tmp_path / "cfg" / ".git"
    git.mkdir()
    (git / "index").write_text("churn")
    assert reloader.snapshot() == before
