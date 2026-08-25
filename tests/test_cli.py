from pathlib import Path

from click.testing import CliRunner

from partyplanner.cli import main

BASE = (
    "title: BBQ Saturday\n"
    "domain: bbq.example.com\n"
    "timezone: America/Chicago\n"
    "events:\n"
    "  - id: bbq\n"
    "    title: Backyard BBQ\n"
    "    when: 2026-06-20 15:00\n"
)


def _write_config(directory: Path) -> Path:
    src = directory / "occasion.yaml"
    src.write_text(BASE)
    return src


def test_config_path_defaults_to_cwd_occasion_yaml(tmp_path: Path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["validate"])
    assert result.exit_code == 0
    assert "ok: BBQ Saturday" in result.output


def test_missing_default_config_is_a_usage_error(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["validate"])
    assert result.exit_code == 2
    assert "no occasion.yaml in the current directory" in result.output
    assert "Traceback" not in result.output


def test_directory_config_path_is_a_clean_error(tmp_path: Path):
    result = CliRunner().invoke(main, ["links", str(tmp_path)])
    assert result.exit_code == 2
    assert "Traceback" not in result.output


def test_binary_config_is_a_clean_error(tmp_path: Path):
    src = tmp_path / "occasion.yaml"
    src.write_bytes(b"\xc9\x00\xff\xfe binary junk")
    result = CliRunner().invoke(main, ["links", str(src)])
    assert result.exit_code == 1
    assert "not a text file" in result.output
    assert "Traceback" not in result.output


def test_binary_links_file_is_a_clean_error(tmp_path: Path):
    src = _write_config(tmp_path)
    (tmp_path / ".links.yaml").write_bytes(b"\xc9\x00\xff\xfe binary junk")
    result = CliRunner().invoke(main, ["links", str(src)])
    assert result.exit_code == 1
    assert "not a text file" in result.output
    assert "Traceback" not in result.output


def test_links_output_aligns_urls_in_a_column(tmp_path: Path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    long_note = "All events (including travel)"
    assert runner.invoke(main, ["link", "add", "--note", long_note]).exit_code == 0
    assert runner.invoke(main, ["link", "add", "--note", "Party only"]).exit_code == 0
    result = runner.invoke(main, ["links"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert len(lines) == 2
    positions = {line.rindex("https://") for line in lines}
    assert len(positions) == 1
    assert "\t" not in result.output


def test_links_admin_appends_key_to_every_url(tmp_path: Path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["link", "add", "--note", "friends"]).exit_code == 0
    assert runner.invoke(main, ["link", "add", "--note", "family"]).exit_code == 0
    first = runner.invoke(main, ["links", "--admin"])
    assert first.exit_code == 0
    assert "minted an admin key" in first.output
    urls = [line.split()[-1] for line in first.output.splitlines() if "https://" in line]
    assert len(urls) == 2
    keys = {url.rsplit("?me=", 1)[1] for url in urls}
    assert len(keys) == 1
    key = keys.pop()
    assert key in (tmp_path / ".links.yaml").read_text()
    second = runner.invoke(main, ["link", "list", "--admin"])
    assert second.exit_code == 0
    assert "minted" not in second.output
    assert second.output.count(f"?me={key}") == 2


def test_links_without_admin_flag_omits_key(tmp_path: Path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["link", "add", "--note", "friends"]).exit_code == 0
    assert runner.invoke(main, ["links", "--admin"]).exit_code == 0
    result = runner.invoke(main, ["links"])
    assert result.exit_code == 0
    assert "?me=" not in result.output


def test_link_revoke_takes_token_first(tmp_path: Path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    add = CliRunner().invoke(main, ["link", "add", "--note", "friends"])
    assert add.exit_code == 0
    token = add.output.strip().rsplit("/i/", 1)[1].rstrip("/")
    result = CliRunner().invoke(main, ["link", "revoke", token])
    assert result.exit_code == 0
    assert f"revoked {token}" in result.output
