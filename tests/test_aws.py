import pytest

from conftest import FIXTURES
from partyplanner import config
from partyplanner.aws import link_items
from partyplanner.cli import _defang


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+2", "'=1+2"),
        ("+SUM(A1)", "'+SUM(A1)"),
        ("-2", "'-2"),
        ("@cmd", "'@cmd"),
        ("Aaron", "Aaron"),
        ("", ""),
        (3, 3),
    ],
)
def test_defang_csv_cells(value, expected):
    assert _defang(value) == expected


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
