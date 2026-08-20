import shutil
from pathlib import Path

import pytest

from conftest import EXAMPLE, FIXTURES
from partyplanner import config
from partyplanner.config import ConfigError


def test_load_bbq_fixture():
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    assert occasion.title == "BBQ Saturday"
    assert occasion.event_ids() == ["bbq"]
    assert occasion.resolve_scope(occasion.links[0]) == ("bbq",)


def test_load_allhallowtide_fixture():
    occasion = config.load(EXAMPLE / "occasion.yaml")
    assert occasion.event_ids() == ["dinner", "crawl", "candy", "party", "stream", "brunch"]
    aaron, chance, generic, stream, visitors = occasion.links
    assert occasion.resolve_scope(aaron) == tuple(occasion.event_ids())
    assert occasion.resolve_scope(chance) == ("candy", "party", "stream")
    assert occasion.resolve_scope(stream) == ("stream",)
    assert visitors.token == "visitors-fixture222"
    assert occasion.resolve_scope(visitors) == tuple(occasion.event_ids())
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


def test_unknown_timezone_rejected():
    with pytest.raises(ConfigError, match="unknown timezone"):
        config.parse(_minimal(timezone="Not/AZone"))


@pytest.mark.parametrize(
    "bad_domain",
    ["EXAMPLE.COM", "no-dots", "exa mple.com", 'x.com"><script>', "-leading.example.com"],
)
def test_bad_domain_rejected(bad_domain):
    with pytest.raises(ConfigError, match="domain"):
        config.parse(_minimal(domain=bad_domain))


@pytest.mark.parametrize("good_domain", ["2026.allhallowtide.party", "events.1512.link"])
def test_good_domain_accepted(good_domain):
    assert config.parse(_minimal(domain=good_domain)).domain == good_domain


@pytest.mark.parametrize("bad_id", ["../../outside", "UPPER", "has space", "x" * 65])
def test_bad_event_id_rejected(bad_id):
    events = [{"id": bad_id, "title": "A", "when": "2026-06-20 15:00"}]
    with pytest.raises(ConfigError, match="event id"):
        config.parse(_minimal(events=events))


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


def test_mint_fills_explicit_null_id_and_token(tmp_path: Path):
    src = tmp_path / "occasion.yaml"
    src.write_text(
        "title: BBQ Saturday\n"
        "domain: bbq.example.com\n"
        "timezone: America/Chicago\n"
        "events:\n"
        "  - id:\n"
        "    title: Backyard BBQ\n"
        "    when: 2026-06-20 15:00\n"
        "links:\n"
        "  - token:\n"
        "    note: group chat\n"
    )
    notes = config.mint(src)
    assert len(notes) == 2
    config.require_complete(config.load(src))


def test_token_over_64_chars_rejected():
    with pytest.raises(ConfigError, match="token"):
        config.parse(_minimal(links=[{"token": "a" * 65, "scope": "all"}]))


def test_slugged_token_accepted():
    occasion = config.parse(
        _minimal(links=[{"token": "out-of-towners-a7k2m6qexz", "scope": "all"}])
    )
    assert occasion.links[0].token == "out-of-towners-a7k2m6qexz"


def test_token_tail_under_10_chars_rejected():
    with pytest.raises(ConfigError, match="token"):
        config.parse(_minimal(links=[{"token": "visitors-a7k2m6qex", "scope": "all"}]))


def test_overlong_prefill_name_rejected():
    with pytest.raises(ConfigError, match="prefill_name"):
        config.parse(_minimal(links=[{"scope": "all", "prefill_name": "x" * 41}]))


def test_mixed_aware_and_naive_times_rejected():
    events = [
        {"id": "a", "title": "A", "when": "2026-06-20 15:00", "end": "2026-06-20 18:00-05:00"}
    ]
    with pytest.raises(ConfigError, match="mixes"):
        config.parse(_minimal(events=events))


def test_trailing_newline_token_rejected():
    with pytest.raises(ConfigError, match="token"):
        config.parse(_minimal(links=[{"token": "abcdefghijklmnop\n", "scope": "all"}]))


def test_trailing_newline_event_id_rejected():
    events = [{"id": "a\n", "title": "A", "when": "2026-06-20 15:00"}]
    with pytest.raises(ConfigError, match="event id"):
        config.parse(_minimal(events=events))


def test_trailing_newline_domain_rejected():
    with pytest.raises(ConfigError, match="domain"):
        config.parse(_minimal(domain="bbq.example.com\n"))


def test_overlong_domain_rejected():
    with pytest.raises(ConfigError, match="domain"):
        config.parse(_minimal(domain=("a" * 60 + ".") * 5 + "com"))


def test_prefill_name_normalized_like_api():
    occasion = config.parse(_minimal(links=[{"scope": "all", "prefill_name": "  Aaron   B  "}]))
    assert occasion.links[0].prefill_name == "Aaron B"


def test_whitespace_only_prefill_name_rejected():
    with pytest.raises(ConfigError, match="prefill_name"):
        config.parse(_minimal(links=[{"scope": "all", "prefill_name": "   "}]))


def test_end_without_when_rejected():
    events = [{"id": "a", "title": "A", "rsvp": "none", "end": "2026-06-20 18:00"}]
    with pytest.raises(ConfigError, match="`end` but no `when`"):
        config.parse(_minimal(events=events))


def test_invalid_revoked_token_rejected():
    with pytest.raises(ConfigError, match="revoked token"):
        config.parse(_minimal(revoked=["NOT-A-TOKEN"]))


def test_duplicate_revoked_tokens_rejected():
    with pytest.raises(ConfigError, match="duplicate revoked"):
        config.parse(_minimal(revoked=["fixturetokenrevoked2", "fixturetokenrevoked2"]))


def test_malformed_yaml_raises_config_error(tmp_path: Path):
    src = tmp_path / "occasion.yaml"
    src.write_text("title: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        config.load(src)


def test_require_complete_fails_without_tokens(tmp_path: Path):
    shutil.copytree(FIXTURES / "bbq", tmp_path / "bbq")
    path = tmp_path / "bbq" / "occasion.yaml"
    path.write_text(path.read_text().replace("token: fixturebbqgroupchat2\n    ", ""))
    occasion = config.load(path)
    with pytest.raises(ConfigError, match="partyplanner mint"):
        config.require_complete(occasion, path)


@pytest.mark.parametrize("bad_color", ["red", "#12345", "7a3ff2", "#xyzxyz", "#7a3ff2;}"])
def test_bad_theme_color_rejected(bad_color):
    with pytest.raises(ConfigError, match="hex color"):
        config.parse(_minimal(theme={"accent": bad_color}))


def test_bad_event_accent_rejected():
    events = [{"id": "a", "title": "A", "when": "2026-06-20 15:00", "accent": "orange"}]
    with pytest.raises(ConfigError, match="hex color"):
        config.parse(_minimal(events=events))


def test_theme_and_accent_accepted():
    events = [{"id": "a", "title": "A", "when": "2026-06-20 15:00", "accent": "#3fa7f2"}]
    occasion = config.parse(_minimal(theme={"accent": "#d1550f", "bg": "#fff"}, events=events))
    assert occasion.theme is not None
    assert occasion.theme.accent == "#d1550f"
    assert occasion.events[0].accent == "#3fa7f2"


def test_unknown_theme_key_rejected():
    with pytest.raises(ConfigError):
        config.parse(_minimal(theme={"font": "Comic Sans"}))


def test_where_url_without_where_rejected():
    events = [
        {
            "id": "a",
            "title": "A",
            "when": "2026-06-20 15:00",
            "where_url": "https://maps.example.com/x",
        }
    ]
    with pytest.raises(ConfigError, match="`where_url` but no `where`"):
        config.parse(_minimal(events=events))


def test_non_http_where_url_rejected():
    events = [
        {
            "id": "a",
            "title": "A",
            "when": "2026-06-20 15:00",
            "where": "X",
            "where_url": "javascript:alert(1)",
        }
    ]
    with pytest.raises(ConfigError, match="http"):
        config.parse(_minimal(events=events))
