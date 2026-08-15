import csv
from pathlib import Path

from conftest import FIXTURES
from partyplanner import config
from partyplanner.render import render


def test_render_bbq(tmp_path: Path):
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    render(occasion, FIXTURES / "bbq", tmp_path)
    site = tmp_path / "site"

    assert (site / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"
    assert (site / "assets" / "app.js").exists()
    assert (site / "assets" / "custom.css").exists()
    assert (site / "assets" / "img" / "bbq.svg").exists()
    assert (site / "ics" / "bbq.ics").exists()

    page = (site / "i" / "fixturebbqgroupchat2" / "index.html").read_text()
    assert "BBQ Saturday" in page
    assert 'data-form="bbq"' in page
    assert "Saturday, June 20, 2026 · 3:00 PM CDT" in page
    assert "123 Example Ave" in page
    assert '<meta name="robots" content="noindex, nofollow">' in page
    assert 'og:image" content="https://bbq.example.com/assets/img/bbq.svg"' in page

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
