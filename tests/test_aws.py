from click.testing import CliRunner

from conftest import FIXTURES
from partyplanner import aws, config
from partyplanner.aws import link_items
from partyplanner.cli import main


def test_export_rsvps_neutralizes_formula_cells(monkeypatch):
    rows = [
        {
            "event_id": "bbq",
            "name": "=1+2",
            "response": "yes",
            "party_size": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "via_token": "fixturebbqgroupchat2",
        }
    ]
    monkeypatch.setattr(aws, "export_rsvps", lambda table_name: rows)
    result = CliRunner().invoke(main, ["export-rsvps", "--table", "t"])
    assert result.exit_code == 0
    assert "'=1+2" in result.output
    assert ",=1+2" not in result.output


def test_link_items_allhallowtide():
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    items = {item["pk"]: item for item in link_items(occasion)}

    aaron = items["LINK#fixtureaaronfull2222"]
    assert aaron["rsvp_events"] == ["dinner", "crawl", "candy", "party", "brunch"]
    assert aaron["prefill_name"] == "Aaron"
    assert aaron["expires_at"] > 1_700_000_000

    stream = items["LINK#fixturestreamonly222"]
    assert stream["rsvp_events"] == []  # stream card is rsvp: none
    assert "prefill_name" not in stream
