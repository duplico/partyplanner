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


class _FakeTable:
    def __init__(self, existing_pks):
        self._existing = existing_pks
        self.puts = []
        self.deletes = []

    def scan(self, **kwargs):
        return {"Items": [{"pk": pk, "sk": "META"} for pk in self._existing]}

    def batch_writer(self):
        table = self

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def put_item(self, Item):
                table.puts.append(Item)

            def delete_item(self, Key):
                table.deletes.append(Key)

        return _Batch()


def test_sync_links_deletes_removed_and_revoked_without_duplicates(monkeypatch):
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    # a record for a link no longer in the config, plus one for a revoked token
    fake = _FakeTable(
        {"LINK#fixtureaaronfull2222", "LINK#fixtureremovedlink2", "LINK#fixturerevokedtoken2"}
    )

    class _Resource:
        def Table(self, name):
            return fake

    import boto3

    monkeypatch.setattr(boto3, "resource", lambda service: _Resource())
    active, stale = aws.sync_links(occasion, "t")

    assert active == len(occasion.links)
    deleted = {d["pk"] for d in fake.deletes}
    assert deleted == {"LINK#fixtureremovedlink2", "LINK#fixturerevokedtoken2"}
    assert len(fake.deletes) == len(deleted)  # no duplicate keys in the batch
    assert stale == 2
    assert all(d["sk"] == "META" for d in fake.deletes)


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
