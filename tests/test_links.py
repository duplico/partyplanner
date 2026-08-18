from pathlib import Path

import pytest

from partyplanner import config
from partyplanner.config import LINKS_FILENAME, ConfigError

BASE = (
    "title: BBQ Saturday\n"
    "domain: bbq.example.com\n"
    "timezone: America/Chicago\n"
    "events:\n"
    "  - id: bbq\n"
    "    title: Backyard BBQ\n"
    "    when: 2026-06-20 15:00\n"
    "  - id: bonfire\n"
    "    title: Bonfire\n"
    "    when: 2026-06-20 20:00\n"
    "scopes:\n"
    "  evening: [bonfire]\n"
)


def _config(tmp_path: Path, text: str = BASE) -> Path:
    src = tmp_path / "occasion.yaml"
    src.write_text(text)
    return src


def test_load_merges_links_file(tmp_path: Path):
    src = _config(tmp_path)
    (tmp_path / LINKS_FILENAME).write_text(
        "links:\n  - token: fixturetokenmerge222\n    scope: evening\n"
        "revoked:\n  - fixturetokenrevoke22\n"
    )
    occasion = config.load(src)
    assert [link.token for link in occasion.links] == ["fixturetokenmerge222"]
    assert occasion.revoked == ["fixturetokenrevoke22"]


def test_load_merges_links_from_both_files(tmp_path: Path):
    src = _config(tmp_path, BASE + "links:\n  - token: fixturetokeninline22\n")
    (tmp_path / LINKS_FILENAME).write_text("links:\n  - token: fixturetokenmerge222\n")
    occasion = config.load(src)
    assert [link.token for link in occasion.links] == [
        "fixturetokeninline22",
        "fixturetokenmerge222",
    ]


def test_duplicate_token_across_files_rejected(tmp_path: Path):
    src = _config(tmp_path, BASE + "links:\n  - token: fixturetokendupes222\n")
    (tmp_path / LINKS_FILENAME).write_text("links:\n  - token: fixturetokendupes222\n")
    with pytest.raises(ConfigError, match="duplicate link tokens"):
        config.load(src)


def test_mint_validates_merged_view(tmp_path: Path):
    src = _config(tmp_path, BASE + "links:\n  - token: fixturetokendupes222\n")
    (tmp_path / LINKS_FILENAME).write_text("links:\n  - token: fixturetokendupes222\n")
    with pytest.raises(ConfigError, match="duplicate link tokens"):
        config.mint(src)


def test_links_file_with_unexpected_keys_rejected(tmp_path: Path):
    src = _config(tmp_path)
    (tmp_path / LINKS_FILENAME).write_text("links: []\nevents: []\n")
    with pytest.raises(ConfigError, match="unexpected keys"):
        config.load(src)


def test_add_link_creates_file_and_appends(tmp_path: Path):
    src = _config(tmp_path)
    first = config.add_link(src, "all", note="group chat")
    second = config.add_link(src, "evening", prefill_name="Pat")
    text = (tmp_path / LINKS_FILENAME).read_text()
    assert text.startswith("# Machine-generated")
    assert src.read_text() == BASE  # occasion.yaml untouched
    occasion = config.load(src)
    assert [link.token for link in occasion.links] == [first.token, second.token]
    assert occasion.resolve_scope(occasion.links[1]) == ("bonfire",)
    config.require_complete(occasion)


def test_load_merges_admin_key_from_links_file(tmp_path: Path):
    src = _config(tmp_path)
    (tmp_path / LINKS_FILENAME).write_text("admin_key: fixtureadminkey22222\n")
    assert config.load(src).admin_key == "fixtureadminkey22222"


def test_conflicting_admin_keys_across_files_rejected(tmp_path: Path):
    src = _config(tmp_path, BASE + "admin_key: fixtureadmininline22\n")
    (tmp_path / LINKS_FILENAME).write_text("admin_key: fixtureadminlinks22\n")
    with pytest.raises(ConfigError, match="both"):
        config.load(src)


def test_same_admin_key_in_both_files_ok(tmp_path: Path):
    src = _config(tmp_path, BASE + "admin_key: fixtureadminkey22222\n")
    (tmp_path / LINKS_FILENAME).write_text("admin_key: fixtureadminkey22222\n")
    assert config.load(src).admin_key == "fixtureadminkey22222"


def test_admin_key_collision_with_link_token_rejected(tmp_path: Path):
    src = _config(tmp_path, BASE + "links:\n  - token: fixtureadminkey22222\n")
    (tmp_path / LINKS_FILENAME).write_text("admin_key: fixtureadminkey22222\n")
    with pytest.raises(ConfigError, match="collides"):
        config.load(src)


def test_ensure_admin_key_mints_once(tmp_path: Path):
    src = _config(tmp_path)
    key, minted = config.ensure_admin_key(src)
    assert minted
    assert config.load(src).admin_key == key
    again, minted_again = config.ensure_admin_key(src)
    assert again == key
    assert not minted_again
    assert src.read_text() == BASE  # occasion.yaml untouched


def test_ensure_admin_key_preserves_existing_links(tmp_path: Path):
    src = _config(tmp_path)
    link = config.add_link(src, "all", note="group chat")
    key, _ = config.ensure_admin_key(src)
    occasion = config.load(src)
    assert [x.token for x in occasion.links] == [link.token]
    assert occasion.admin_key == key


def test_add_link_explicit_event_ids(tmp_path: Path):
    src = _config(tmp_path)
    config.add_link(src, ["bonfire"])
    occasion = config.load(src)
    assert occasion.resolve_scope(occasion.links[0]) == ("bonfire",)


def test_add_link_unknown_scope_rejected(tmp_path: Path):
    src = _config(tmp_path)
    with pytest.raises(ConfigError, match="unknown scope"):
        config.add_link(src, "nope")
    assert not (tmp_path / LINKS_FILENAME).exists()


def test_add_link_invalid_prefill_rejected(tmp_path: Path):
    src = _config(tmp_path)
    with pytest.raises(ConfigError, match="prefill_name"):
        config.add_link(src, "all", prefill_name="x" * 41)
    assert not (tmp_path / LINKS_FILENAME).exists()


def test_add_link_empty_scope_rejected(tmp_path: Path):
    src = _config(tmp_path)
    with pytest.raises(ConfigError, match="empty"):
        config.add_link(src, [])
    assert not (tmp_path / LINKS_FILENAME).exists()


def test_add_and_revoke_tolerate_null_sections(tmp_path: Path):
    src = _config(tmp_path)
    (tmp_path / LINKS_FILENAME).write_text("links:\nrevoked:\n")
    link = config.add_link(src, "all")
    assert link.token is not None
    config.revoke_link(src, link.token)
    occasion = config.load(src)
    assert occasion.links == []
    assert occasion.revoked == [link.token]


def test_revoke_link_rolls_back_when_merged_config_is_invalid(tmp_path: Path):
    src = _config(tmp_path)
    config.add_link(src, ["bonfire"])
    doomed = config.add_link(src, "all")
    assert doomed.token is not None
    # break the merged config independently of the revoke
    src.write_text(
        BASE.replace("  - id: bonfire\n    title: Bonfire\n    when: 2026-06-20 20:00\n", "")
    )
    before = (tmp_path / LINKS_FILENAME).read_text()
    with pytest.raises(ConfigError):
        config.revoke_link(src, doomed.token)
    assert (tmp_path / LINKS_FILENAME).read_text() == before


def test_revoke_link_moves_token_to_revoked(tmp_path: Path):
    src = _config(tmp_path)
    link = config.add_link(src, "all")
    keep = config.add_link(src, "evening")
    assert link.token is not None
    config.revoke_link(src, link.token)
    occasion = config.load(src)
    assert [entry.token for entry in occasion.links] == [keep.token]
    assert occasion.revoked == [link.token]


def test_revoke_config_resident_link_rejected(tmp_path: Path):
    src = _config(tmp_path, BASE + "links:\n  - token: fixturetokeninline22\n")
    with pytest.raises(ConfigError, match="delete its entry there"):
        config.revoke_link(src, "fixturetokeninline22")


def test_revoke_unknown_token_rejected(tmp_path: Path):
    src = _config(tmp_path)
    with pytest.raises(ConfigError, match="unknown token"):
        config.revoke_link(src, "fixturetokenabsent22")
