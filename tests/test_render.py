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


def test_default_favicon_linked_and_shipped(tmp_path: Path):
    occasion = _asset_occasion(tmp_path)
    render(occasion, tmp_path, tmp_path / "out")
    site = tmp_path / "out" / "site"
    assert (site / "assets" / "favicon.svg").exists()
    for page in ("index.html", "i/fixtureassettoken222/index.html"):
        assert '<link rel="icon" href="/assets/favicon.svg">' in (site / page).read_text()


def test_custom_favicon_copied_to_site_root(tmp_path: Path):
    (tmp_path / "fav.ico").write_bytes(b"\x00\x00\x01\x00")
    occasion = _asset_occasion(tmp_path, favicon="fav.ico")
    render(occasion, tmp_path, tmp_path / "out")
    site = tmp_path / "out" / "site"
    assert (site / "favicon.ico").read_bytes() == b"\x00\x00\x01\x00"
    assert '<link rel="icon" href="/favicon.ico">' in (site / "index.html").read_text()


def test_favicon_path_escaping_config_dir_rejected(tmp_path: Path):
    (tmp_path / "evil.ico").write_bytes(b"\x00")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    occasion = _asset_occasion(config_dir, favicon="../evil.ico")
    with pytest.raises(ConfigError, match="escapes"):
        render(occasion, config_dir, tmp_path / "out")


def test_asset_path_escaping_config_dir_rejected(tmp_path: Path):
    (tmp_path / "evil.svg").write_text("<svg></svg>")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    occasion = _asset_occasion(config_dir, photo="../evil.svg")
    with pytest.raises(ConfigError, match="escapes"):
        render(occasion, config_dir, tmp_path / "out")


def test_override_symlink_escaping_config_dir_rejected(tmp_path: Path):
    (tmp_path / "secret.txt").write_text("secret")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    overrides = config_dir / "overrides"
    overrides.mkdir()
    (overrides / "leak.txt").symlink_to(tmp_path / "secret.txt")
    occasion = _asset_occasion(config_dir)
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


def test_markdown_blurbs_render(tmp_path: Path):
    occasion = _asset_occasion(
        tmp_path,
        blurb="**Big** party\nyou're invited\n\nSecond paragraph.",
        events=[
            {
                "id": "a",
                "title": "A",
                "when": "2026-06-20 15:00",
                "blurb": "Bring [a chair](https://example.com/chairs).",
            }
        ],
    )
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    # soft line break stays in one paragraph; blank line starts a new one
    assert "<p><strong>Big</strong> party\nyou're invited</p>" in page
    assert "<p>Second paragraph.</p>" in page
    assert '<a href="https://example.com/chairs">a chair</a>' in page
    # og:description is the first paragraph with markup stripped;
    # soft line breaks flow into one line
    assert 'og:description" content="Big party you&#39;re invited"' in page


def test_markdown_landing_blurb(tmp_path: Path):
    occasion = _asset_occasion(tmp_path, landing={"blurb": "watch *live*"})
    render(occasion, tmp_path, tmp_path / "out")
    index = (tmp_path / "out" / "site" / "index.html").read_text()
    assert "watch <em>live</em>" in index


def test_markdown_raw_html_and_bad_links_neutralized(tmp_path: Path):
    occasion = _asset_occasion(
        tmp_path,
        events=[
            {
                "id": "a",
                "title": "A",
                "when": "2026-06-20 15:00",
                "blurb": "<img src=x onerror=alert(1)>\n\n[click](javascript:alert(1))",
            }
        ],
    )
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert "&lt;img" in page
    assert "<img src=x" not in page
    assert 'href="javascript:' not in page


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
    assert (site / "i" / "fixturebbqgroupchat2" / "bbq.ics").exists()
    assert not (site / "ics").exists()

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

    # calendar files live behind each capability URL, scoped to that link:
    # a stream-only recipient gets no party.ics, and there is no guessable
    # site-wide /ics/ directory
    assert not (site / "ics").exists()
    assert (site / "i" / "fixtureaaronfull2222" / "party.ics").exists()
    assert (site / "i" / "fixturechanceparty22" / "party.ics").exists()
    assert not (site / "i" / "fixturestreamonly222" / "party.ics").exists()
    # no ICS for the undated stream card
    assert not (site / "i" / "fixtureaaronfull2222" / "stream.ics").exists()
    # pages reference the calendar relative to the token directory
    assert 'href="party.ics"' in full


def test_render_theme_and_accent(tmp_path: Path):
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    render(occasion, FIXTURES / "allhallowtide", tmp_path)
    site = tmp_path / "site"

    full = (site / "i" / "fixtureaaronfull2222" / "index.html").read_text()
    assert "--accent: #d1550f;" in full
    assert "--bg: #f7f2ea;" in full
    assert 'style="--accent: #3fa7f2"' in full  # crawl's per-event accent

    # landing page gets the theme too
    assert "--accent: #d1550f;" in (site / "index.html").read_text()

    # no theme -> no inline style block
    bbq_out = tmp_path / "bbq"
    bbq = config.load(FIXTURES / "bbq" / "occasion.yaml")
    render(bbq, FIXTURES / "bbq", bbq_out)
    page = (bbq_out / "site" / "i" / "fixturebbqgroupchat2" / "index.html").read_text()
    assert ":root" not in page


def test_render_end_time_range(tmp_path: Path):
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    render(occasion, FIXTURES / "allhallowtide", tmp_path)
    full = (tmp_path / "site" / "i" / "fixtureaaronfull2222" / "index.html").read_text()
    # candy ends the same day; crawl crosses midnight
    assert "Saturday, October 31, 2026 · 5:00 PM–8:00 PM CDT" in full
    assert "Friday, October 30, 2026 · 9:00 PM – Saturday 1:00 AM CDT" in full


def test_render_rsvp_form_labels(tmp_path: Path):
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    render(occasion, FIXTURES / "bbq", tmp_path)
    page = (tmp_path / "site" / "i" / "fixturebbqgroupchat2" / "index.html").read_text()
    assert 'placeholder="Your name"' in page
    assert "Your first name" not in page
    assert "guests</label>" in page


def test_render_calendar_links(tmp_path: Path):
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    render(occasion, FIXTURES / "bbq", tmp_path)
    page = (tmp_path / "site" / "i" / "fixturebbqgroupchat2" / "index.html").read_text()
    assert "https://calendar.google.com/calendar/render?action=TEMPLATE" in page
    assert "https://outlook.live.com/calendar/0/deeplink/compose?" in page
    assert "https://calendar.yahoo.com/?" in page
    assert 'href="bbq.ics" download' in page


def test_render_where_links_to_google_maps(tmp_path: Path):
    occasion = _asset_occasion(
        tmp_path,
        events=[
            {"id": "a", "title": "A", "when": "2026-06-20 15:00", "where": "123 Main St & Oak"},
        ],
    )
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert (
        '<a href="https://www.google.com/maps/search/?api=1&amp;query=123%20Main%20St%20%26%20Oak"'
        in page
    )
    assert ">123 Main St &amp; Oak</a>" in page


def test_render_where_url_override(tmp_path: Path):
    occasion = _asset_occasion(
        tmp_path,
        events=[
            {
                "id": "a",
                "title": "A",
                "when": "2026-06-20 15:00",
                "where": "The Park",
                "where_url": "https://maps.app.goo.gl/abc123",
            },
        ],
    )
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert '<a href="https://maps.app.goo.gl/abc123"' in page
    assert "google.com/maps/search" not in page


def test_render_title_only_info_card(tmp_path: Path):
    occasion = _asset_occasion(
        tmp_path,
        events=[
            {"id": "a", "title": "A", "when": "2026-06-20 15:00"},
            {"id": "stay", "title": "Where to stay", "rsvp": "none", "blurb": "Hotel A or B."},
        ],
    )
    render(occasion, tmp_path, tmp_path / "out")
    page = (tmp_path / "out" / "site" / "i" / "fixtureassettoken222" / "index.html").read_text()
    assert "Where to stay" in page
    assert "Hotel A or B." in page
    card = page.split('id="stay"')[1].split("</section>")[0]
    assert "when" not in card and "where" not in card and "rsvp" not in card
