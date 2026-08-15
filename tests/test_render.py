import csv
from pathlib import Path

import pytest

from conftest import FIXTURES
from partyplanner import config
from partyplanner.config import ConfigError
from partyplanner.render import render


def _asset_occasion(tmp_path: Path, **overrides):
    (tmp_path / "ceremony").mkdir()
    (tmp_path / "reception").mkdir()
    (tmp_path / "ceremony" / "hero.svg").write_text("<svg>ceremony</svg>")
    (tmp_path / "reception" / "hero.svg").write_text("<svg>reception</svg>")
    data = {
        "title": "T",
        "domain": "t.example.com",
        "timezone": "America/Chicago",
        "events": [
            {
                "id": "a",
                "title": "A",
                "when": "2026-06-20 15:00",
                "photo": "ceremony/hero.svg",
            },
            {
                "id": "b",
                "title": "B",
                "when": "2026-06-21 15:00",
                "photo": "reception/hero.svg",
            },
        ],
        "links": [{"token": "fixtureassettoken222", "scope": "all"}],
    }
    data.update(overrides)
    return config.parse(data)


def test_assets_with_same_basename_do_not_collide(tmp_path: Path):
    occasion = _asset_occasion(tmp_path)
    render(occasion, tmp_path, tmp_path / "out")
    img = tmp_path / "out" / "site" / "assets" / "img"
    assert (img / "ceremony" / "hero.svg").read_text() == "<svg>ceremony</svg>"
    assert (img / "reception" / "hero.svg").read_text() == "<svg>reception</svg>"
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert "/assets/img/ceremony/hero.svg" in page
    assert "/assets/img/reception/hero.svg" in page


def test_whitespace_blurb_uses_default_og_description(tmp_path: Path):
    occasion = _asset_occasion(tmp_path, blurb="  \n  ")
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert 'og:description" content="You&#39;re invited."' in page


def test_pages_suppress_cross_origin_referrer(tmp_path: Path):
    occasion = _asset_occasion(tmp_path)
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert '<meta name="referrer" content="same-origin">' in page


def test_asset_path_escaping_config_dir_rejected(tmp_path: Path):
    (tmp_path / "evil.svg").write_text("<svg></svg>")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    occasion = _asset_occasion(config_dir, photo="../evil.svg")
    with pytest.raises(ConfigError, match="escapes"):
        render(occasion, config_dir, tmp_path / "out")


def test_asset_symlink_escaping_config_dir_rejected(tmp_path: Path):
    (tmp_path / "secret.svg").write_text("<svg>secret</svg>")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sneaky.svg").symlink_to(tmp_path / "secret.svg")
    occasion = _asset_occasion(config_dir, photo="sneaky.svg")
    with pytest.raises(ConfigError, match="escapes"):
        render(occasion, config_dir, tmp_path / "out")


def test_embed_path_escaping_config_dir_rejected(tmp_path: Path):
    (tmp_path / "private.html").write_text("secret")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    occasion = _asset_occasion(config_dir, landing={"blurb": "hi", "embed": "../private.html"})
    with pytest.raises(ConfigError, match="escapes"):
        render(occasion, config_dir, tmp_path / "out")


def test_config_html_is_autoescaped(tmp_path: Path):
    occasion = _asset_occasion(tmp_path, blurb='<script>alert("x")</script>')
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert '<script>alert("x")</script>' not in page
    assert "&lt;script&gt;" in page


def test_render_bbq(tmp_path: Path):
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    render(occasion, FIXTURES / "bbq", tmp_path)
    site = tmp_path / "site"

    assert (site / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"
    assert (site / "assets" / "app.js").exists()
    assert (site / "assets" / "custom.css").exists()
    assert (site / "assets" / "img" / "assets" / "bbq.svg").exists()
    assert (site / "ics" / "bbq.ics").exists()

    page = (site / "i" / "fixturebbqgroupchat2" / "index.html").read_text()
    assert "BBQ Saturday" in page
    assert 'data-form="bbq"' in page
    assert "Saturday, June 20, 2026 · 3:00 PM CDT" in page
    assert "123 Example Ave" in page
    assert '<meta name="robots" content="noindex, nofollow">' in page
    assert 'og:image" content="https://bbq.example.com/assets/img/assets/bbq.svg"' in page

    landing = (site / "index.html").read_text()
    assert "ask your host" in landing
    assert "data-form" not in landing

    with (tmp_path / "links.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows == [
        {
            "prefill_name": "",
            "note": "group chat link, forward freely",
            "scope": "bbq",
            "url": "https://bbq.example.com/i/fixturebbqgroupchat2/",
        }
    ]


def test_render_allhallowtide_scopes(tmp_path: Path):
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    render(occasion, FIXTURES / "allhallowtide", tmp_path)
    site = tmp_path / "site"

    full = (site / "i" / "fixtureaaronfull2222" / "index.html").read_text()
    party = (site / "i" / "fixturechanceparty22" / "index.html").read_text()
    stream = (site / "i" / "fixturestreamonly222" / "index.html").read_text()

    assert "Travelers Dinner" in full
    assert "Travelers Dinner" not in party
    assert "Main Party" in party
    assert "Main Party" not in stream
    assert "Watch the Live Stream" in stream
    assert "data-form" not in stream  # stream card is rsvp: none

    # same scope -> identical page bytes at both token paths
    generic = (site / "i" / "fixturegenericparty2" / "index.html").read_text()
    assert generic == party

    # landing page carries the embed
    landing = (site / "index.html").read_text()
    assert "fake-stream-embed" in landing

    # overrides replaced the empty custom.css
    assert "--accent: #f28c28" in (site / "assets" / "custom.css").read_text()

    # revoked token gets no page
    assert not (site / "i" / "fixturerevokedtoken2").exists()

    # no ICS for the undated stream card
    assert not (site / "ics" / "stream.ics").exists()
    assert (site / "ics" / "party.ics").exists()
