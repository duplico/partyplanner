import shutil
from pathlib import Path

import pytest

from conftest import FIXTURES
from partyplanner import config
from partyplanner.config import ConfigError


def test_load_bbq_fixture():
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    assert occasion.title == "BBQ Saturday"
    assert occasion.event_ids() == ["bbq"]
    assert occasion.resolve_scope(occasion.links[0]) == ("bbq",)


def test_load_allhallowtide_fixture():
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    assert occasion.event_ids() == ["dinner", "crawl", "candy", "party", "stream", "brunch"]
    aaron, chance, generic, stream = occasion.links
    assert occasion.resolve_scope(aaron) == tuple(occasion.event_ids())
    assert occasion.resolve_scope(chance) == ("candy", "party", "stream")
    assert occasion.resolve_scope(stream) == ("stream",)
    assert occasion.rsvp_open_ids() == {"dinner", "crawl", "candy", "party", "brunch"}


def _minimal(**overrides) -> dict:
    data = {
        "title": "T",
        "domain": "t.example.com",
        "timezone": "America/Chicago",
        "events": [{"id": "a", "title": "A", "when": "2026-06-20 15:00"}],
        "links": [{"scope": "all"}],
    }
    data.update(overrides)
    return data


def test_duplicate_event_ids_rejected():
    events = [
        {"id": "a", "title": "A", "when": "2026-06-20 15:00"},
        {"id": "a", "title": "B", "when": "2026-06-21 15:00"},
    ]
    with pytest.raises(ConfigError, match="duplicate event ids"):
        config.parse(_minimal(events=events))


def test_duplicate_titles_without_ids_rejected():
    events = [
        {"title": "Party", "when": "2026-06-20 15:00"},
        {"title": "Party", "when": "2026-06-21 15:00"},
    ]
    with pytest.raises(ConfigError, match="titles must be unique"):
        config.parse(_minimal(events=events))


def test_unknown_scope_member_rejected():
    with pytest.raises(ConfigError, match="unknown events"):
        config.parse(_minimal(scopes={"broken": ["nope"]}))


def test_unknown_scope_preset_rejected():
    with pytest.raises(ConfigError, match="unknown scope preset"):
        config.parse(_minimal(links=[{"scope": "nope"}]))


def test_rsvp_open_requires_when():
    with pytest.raises(ConfigError, match="needs a `when`"):
        config.parse(_minimal(events=[{"id": "a", "title": "A"}]))


def test_active_token_in_revoked_rejected():
    data = _minimal(
        links=[{"token": "fixturetokenactive22", "scope": "all"}],
        revoked=["fixturetokenactive22"],
    )
    with pytest.raises(ConfigError, match="revoked"):
        config.parse(data)


def test_mint_writes_back_ids_and_tokens(tmp_path: Path):
    src = tmp_path / "occasion.yaml"
    src.write_text(
        "title: BBQ Saturday\n"
        "domain: bbq.example.com\n"
        "timezone: America/Chicago\n"
        "# a comment that must survive\n"
        "events:\n"
        "  - title: Backyard BBQ\n"
        "    when: 2026-06-20 15:00\n"
        "links:\n"
        "  - note: group chat\n"
    )
    notes = config.mint(src)
    assert len(notes) == 2
    text = src.read_text()
    assert "id: backyard-bbq" in text
    assert "token:" in text
    assert "# a comment that must survive" in text

    occasion = config.load(src)
    config.require_complete(occasion)
    assert config.mint(src) == []  # idempotent


def test_require_complete_fails_without_tokens(tmp_path: Path):
    shutil.copytree(FIXTURES / "bbq", tmp_path / "bbq")
    path = tmp_path / "bbq" / "occasion.yaml"
    path.write_text(path.read_text().replace("token: fixturebbqgroupchat2\n    ", ""))
    occasion = config.load(path)
    with pytest.raises(ConfigError, match="partyplanner mint"):
        config.require_complete(occasion, path)
