from datetime import date

import pytest
from ruamel.yaml import YAML

from partyplanner import config
from partyplanner.config import ConfigError
from partyplanner.render import render
from partyplanner.scaffold import (
    load_repo_config,
    scaffold_bootstrap,
    scaffold_occasion,
    zone_for_domain,
)


def _occasion(tmp_path, **overrides):
    kwargs = {
        "domain": "bbq-2026.events.example.com",
        "zone_id": "Z0123456789EXAMPLE",
        "role_arn": "arn:aws:iam::123456789012:role/partyplanner-deploy",
        "state_bucket": "my-tf-state",
    }
    kwargs.update(overrides)
    return scaffold_occasion(tmp_path, "bbq-2026", **kwargs)


def test_scaffold_occasion_writes_capsule_and_workflows(tmp_path):
    paths, kept = _occasion(tmp_path)
    assert kept == []
    assert [p.relative_to(tmp_path).as_posix() for p in paths] == [
        "occasions/bbq-2026/occasion.yaml",
        "occasions/bbq-2026/terraform/main.tf",
        ".github/workflows/deploy-bbq-2026.yml",
        ".github/workflows/destroy-bbq-2026.yml",
    ]


def test_scaffolded_occasion_yaml_is_valid_config(tmp_path):
    _occasion(tmp_path)
    occasion = config.load(tmp_path / "occasions" / "bbq-2026" / "occasion.yaml")
    assert occasion.domain == "bbq-2026.events.example.com"
    assert occasion.title == "Bbq 2026"
    assert occasion.events[0].when.date() > date.today()


def test_scaffolded_workflows_parse_and_wire_inputs(tmp_path):
    _occasion(tmp_path)
    yaml = YAML(typ="safe")
    deploy = yaml.load(tmp_path / ".github/workflows/deploy-bbq-2026.yml")
    destroy = yaml.load(tmp_path / ".github/workflows/destroy-bbq-2026.yml")
    for wf in (deploy, destroy):
        job = next(iter(wf["jobs"].values()))
        assert job["with"]["occasion_dir"] == "occasions/bbq-2026"
        assert job["with"]["tf_state_bucket"] == "my-tf-state"
        assert job["with"]["role_to_assume"].startswith("arn:aws:iam::")
        assert job["with"]["partyplanner_ref"] == "default"
        assert job["with"]["aws_region"] == "us-east-1"
        assert job["secrets"] == "inherit"
    assert deploy["on"]["push"]["paths"] == [
        "occasions/bbq-2026/**",
        "occasions/bbq-2026/.links.yaml",
    ]
    assert "workflow_dispatch" in deploy["on"]
    assert destroy["on"] == "workflow_dispatch"


def test_scaffolded_terraform_pins_ref_and_state_key(tmp_path):
    _occasion(tmp_path, ref="v0.1.0")
    tf = (tmp_path / "occasions/bbq-2026/terraform/main.tf").read_text()
    assert "modules/occasion?ref=v0.1.0" in tf
    assert 'bucket = "my-tf-state"' in tf
    assert 'key    = "occasions/bbq-2026/terraform.tfstate"' in tf
    assert 'zone_id = "Z0123456789EXAMPLE"' in tf


def test_scaffold_occasion_refuses_overwrite(tmp_path):
    _occasion(tmp_path)
    with pytest.raises(ConfigError, match="refusing to overwrite"):
        _occasion(tmp_path)


def test_scaffold_occasion_is_all_or_nothing(tmp_path):
    conflict = tmp_path / ".github" / "workflows" / "deploy-bbq-2026.yml"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("# preexisting\n")
    with pytest.raises(ConfigError, match="deploy-bbq-2026.yml"):
        _occasion(tmp_path)
    assert not (tmp_path / "occasions").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"domain": "Not A Domain"},
        {"zone_id": "not-a-zone"},
        {"role_arn": "arn:aws:s3:::bucket"},
        {"state_bucket": "Bad_Bucket"},
        {"state_bucket": "a..b"},
        {"state_bucket": "a.-b"},
        {"state_bucket": "192.168.1.1"},
        {"state_bucket": "xn--bucket"},
        {"state_bucket": "bucket-s3alias"},
        {"state_bucket": "ab"},
        {"timezone": "Not/AZone"},
        {"ref": "bad ref"},
    ],
)
def test_scaffold_occasion_rejects_bad_inputs(tmp_path, overrides):
    with pytest.raises(ConfigError):
        _occasion(tmp_path, **overrides)


def test_scaffold_occasion_rejects_bad_name(tmp_path):
    with pytest.raises(ConfigError, match="occasion name"):
        scaffold_occasion(
            tmp_path,
            "../escape",
            domain="bbq.example.com",
            zone_id="Z0123456789EXAMPLE",
            role_arn="arn:aws:iam::123456789012:role/partyplanner-deploy",
            state_bucket="my-tf-state",
        )


def test_scaffold_bootstrap_writes_root(tmp_path):
    paths, kept = scaffold_bootstrap(
        tmp_path,
        zones=["allhallowtide.party", "events.example.com"],
        budget_email="you@example.com",
        budget_limit=10,
        github_repo="1512-link/events",
        state_bucket="my-tf-state",
    )
    assert kept == []
    assert paths == [tmp_path / "bootstrap" / "main.tf", tmp_path / "partyplanner.yaml"]
    tf = paths[0].read_text()
    assert 'zones            = ["allhallowtide.party", "events.example.com"]' in tf
    assert "repo:1512-link/events:ref:refs/heads/default" in tf
    assert 'bucket = "my-tf-state"' in tf
    assert 'key    = "bootstrap/terraform.tfstate"' in tf
    assert load_repo_config(tmp_path) == {
        "state_bucket": "my-tf-state",
        "region": "us-east-1",
        "branch": "default",
        "ref": "default",
    }


def test_scaffold_bootstrap_without_bucket_leaves_it_commented(tmp_path):
    scaffold_bootstrap(
        tmp_path,
        zones=["events.example.com"],
        budget_email="you@example.com",
        budget_limit=10,
        github_repo="1512-link/events",
    )
    assert "state_bucket" not in load_repo_config(tmp_path)
    assert "# state_bucket:" in (tmp_path / "partyplanner.yaml").read_text()
    tf = (tmp_path / "bootstrap" / "main.tf").read_text()
    assert "bucket =" not in tf
    assert '-backend-config="bucket=$TF_STATE_BUCKET"' in tf


def test_scaffold_bootstrap_force_keeps_edited_repo_config(tmp_path):
    def _bootstrap(**kwargs):
        return scaffold_bootstrap(
            tmp_path,
            zones=["events.example.com"],
            budget_email="you@example.com",
            budget_limit=10,
            github_repo="1512-link/events",
            state_bucket="my-tf-state",
            **kwargs,
        )

    _bootstrap()
    config = tmp_path / "partyplanner.yaml"
    config.write_text("state_bucket: my-tf-state\nref: v0.2.0\n")
    written, kept = _bootstrap(force=True)
    assert kept == [config]
    assert config.read_text() == "state_bucket: my-tf-state\nref: v0.2.0\n"
    assert written == [tmp_path / "bootstrap" / "main.tf"]


def test_force_regenerated_files_keep_umask_permissions(tmp_path):
    _occasion(tmp_path)
    tf = tmp_path / "occasions/bbq-2026/terraform/main.tf"
    before = tf.stat().st_mode & 0o777
    _occasion(tmp_path, force=True)
    assert tf.stat().st_mode & 0o777 == before


def test_scaffold_bootstrap_rejects_bad_repo(tmp_path):
    with pytest.raises(ConfigError, match="github repo"):
        scaffold_bootstrap(
            tmp_path,
            zones=["events.example.com"],
            budget_email="you@example.com",
            budget_limit=10,
            github_repo="not-a-repo",
        )


def test_scaffold_bootstrap_accepts_id_pinned_repo(tmp_path):
    scaffold_bootstrap(
        tmp_path,
        zones=["events.example.com"],
        budget_email="you@example.com",
        budget_limit=10,
        github_repo="1512-ninja@168232576/events@1334792952",
        state_bucket="my-tf-state",
    )
    tf = (tmp_path / "bootstrap" / "main.tf").read_text()
    assert "repo:1512-ninja@168232576/events@1334792952:ref:refs/heads/default" in tf


def test_scaffold_bootstrap_rejects_nonpositive_budget(tmp_path):
    with pytest.raises(ConfigError, match="budget limit"):
        scaffold_bootstrap(
            tmp_path,
            zones=["events.example.com"],
            budget_email="you@example.com",
            budget_limit=0,
            github_repo="1512-link/events",
        )


@pytest.mark.parametrize(
    "email",
    ["no-at-sign", 'a"@example.com', "${aws:x}@example.com", "you@example", "a b@example.com"],
)
def test_scaffold_bootstrap_rejects_bad_email(tmp_path, email):
    with pytest.raises(ConfigError, match="budget email"):
        scaffold_bootstrap(
            tmp_path,
            zones=["events.example.com"],
            budget_email=email,
            budget_limit=10,
            github_repo="1512-link/events",
        )


def _write_repo_config(tmp_path, **extra):
    lines = {"state_bucket": "cfg-tf-state", "region": "us-west-2", "ref": "v0.2.0", **extra}
    (tmp_path / "partyplanner.yaml").write_text("".join(f"{k}: {v}\n" for k, v in lines.items()))


def test_load_repo_config_rejects_invalid_yaml(tmp_path):
    (tmp_path / "partyplanner.yaml").write_text("state_bucket: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_repo_config(tmp_path)


def test_repo_config_roundtrips_numeric_looking_values(tmp_path):
    scaffold_bootstrap(
        tmp_path,
        zones=["events.example.com"],
        budget_email="you@example.com",
        budget_limit=10,
        github_repo="1512-link/events",
        state_bucket="123456",
        ref="2026.1",
    )
    config = load_repo_config(tmp_path)
    assert config["state_bucket"] == "123456"
    assert config["ref"] == "2026.1"


def test_scaffold_occasion_reads_repo_config(tmp_path):
    _write_repo_config(tmp_path)
    scaffold_occasion(
        tmp_path,
        "bbq-2026",
        domain="bbq-2026.events.example.com",
        zone_id="Z0123456789EXAMPLE",
        role_arn="arn:aws:iam::123456789012:role/partyplanner-deploy",
    )
    yaml = YAML(typ="safe")
    job = next(iter(yaml.load(tmp_path / ".github/workflows/deploy-bbq-2026.yml")["jobs"].values()))
    assert job["with"]["tf_state_bucket"] == "cfg-tf-state"
    assert job["with"]["aws_region"] == "us-west-2"
    assert job["with"]["partyplanner_ref"] == "v0.2.0"


def test_scaffold_occasion_flags_override_repo_config(tmp_path):
    _write_repo_config(tmp_path)
    _occasion(tmp_path, ref="v9")
    tf = (tmp_path / "occasions/bbq-2026/terraform/main.tf").read_text()
    assert "modules/occasion?ref=v9" in tf
    assert 'bucket = "my-tf-state"' in tf


def test_scaffold_occasion_without_state_bucket_anywhere(tmp_path):
    with pytest.raises(ConfigError, match="state bucket"):
        scaffold_occasion(
            tmp_path,
            "bbq-2026",
            domain="bbq.example.com",
            zone_id="Z0123456789EXAMPLE",
            role_arn="arn:aws:iam::123456789012:role/partyplanner-deploy",
        )


def test_scaffold_occasion_resolves_from_bootstrap_outputs(tmp_path, monkeypatch):
    _write_repo_config(tmp_path)
    outputs = {
        "deploy_role_arn": "arn:aws:iam::123456789012:role/partyplanner-deploy",
        "zone_ids": {
            "example.com": "ZAAAAAAAAAAAAAAAAA",
            "events.example.com": "Z0123456789EXAMPLE",
        },
    }
    monkeypatch.setattr("partyplanner.scaffold.bootstrap_outputs", lambda root: outputs)
    scaffold_occasion(tmp_path, "bbq-2026", domain="bbq-2026.events.example.com")
    tf = (tmp_path / "occasions/bbq-2026/terraform/main.tf").read_text()
    assert 'zone_id = "Z0123456789EXAMPLE"' in tf  # longest zone suffix wins


def test_scaffold_occasion_missing_bootstrap_root(tmp_path):
    _write_repo_config(tmp_path)
    with pytest.raises(ConfigError, match="no bootstrap root"):
        scaffold_occasion(tmp_path, "bbq-2026", domain="bbq.example.com")


def test_zone_for_domain():
    zones = {"example.com": "Z1", "events.example.com": "Z2"}
    assert zone_for_domain(zones, "example.com") == "Z1"
    assert zone_for_domain(zones, "a.example.com") == "Z1"
    assert zone_for_domain(zones, "a.events.example.com") == "Z2"
    with pytest.raises(ConfigError, match="no bootstrap zone"):
        zone_for_domain(zones, "other.net")
    with pytest.raises(ConfigError, match="no bootstrap zone"):
        zone_for_domain(zones, "notexample.com")


def test_scaffold_occasion_force_regenerates_but_keeps_config(tmp_path):
    _occasion(tmp_path)
    occasion_yaml = tmp_path / "occasions/bbq-2026/occasion.yaml"
    occasion_yaml.write_text("title: Hand Edited\n")
    written, kept = _occasion(tmp_path, ref="v0.2.0", force=True)
    assert kept == [occasion_yaml]
    assert occasion_yaml.read_text() == "title: Hand Edited\n"
    assert occasion_yaml not in written
    tf = (tmp_path / "occasions/bbq-2026/terraform/main.tf").read_text()
    assert "modules/occasion?ref=v0.2.0" in tf


def test_scaffold_occasion_example_emits_kitchen_sink(tmp_path):
    written, kept = _occasion(tmp_path, example=True)
    assert kept == []
    occasion_dir = tmp_path / "occasions/bbq-2026"
    text = (occasion_dir / "occasion.yaml").read_text()
    assert "kitchen-sink example" in text
    assert "allhallowtide.example.com" not in text
    assert (occasion_dir / "assets" / "hero.svg") in written
    assert (occasion_dir / "assets" / "favicon.svg") in written
    assert (occasion_dir / "stream-embed.html") in written
    assert (occasion_dir / "overrides" / "assets" / "custom.css") in written
    assert not (occasion_dir / ".links.yaml").exists()
    occasion = config.load(occasion_dir / "occasion.yaml")
    assert occasion.domain == "bbq-2026.events.example.com"
    assert occasion.landing is not None
    assert any(e.rsvp == "none" for e in occasion.events)
    assert occasion.scopes


def test_scaffold_occasion_example_renders(tmp_path):
    _occasion(tmp_path, example=True, timezone="America/New_York")
    occasion_dir = tmp_path / "occasions/bbq-2026"
    assert "timezone: America/New_York\n" in (occasion_dir / "occasion.yaml").read_text()
    config.add_link(occasion_dir / "occasion.yaml", "full", note="group chat")
    occasion = config.load(occasion_dir / "occasion.yaml")
    render(occasion, occasion_dir, tmp_path / "out")
    landing = (tmp_path / "out" / "site" / "index.html").read_text()
    assert "fake-stream-embed" in landing


def test_scaffold_occasion_example_force_keeps_edited_content(tmp_path):
    _occasion(tmp_path, example=True)
    occasion_dir = tmp_path / "occasions/bbq-2026"
    hero = occasion_dir / "assets" / "hero.svg"
    hero.write_text("<svg>mine</svg>\n")
    written, kept = _occasion(tmp_path, example=True, force=True)
    assert hero in kept
    assert occasion_dir / "occasion.yaml" in kept
    assert hero.read_text() == "<svg>mine</svg>\n"
    assert occasion_dir / "terraform" / "main.tf" in written


def test_scaffold_numeric_occasion_name_yields_valid_config(tmp_path):
    scaffold_occasion(
        tmp_path,
        "2026",
        domain="2026.allhallowtide.party",
        zone_id="Z0123456789EXAMPLE",
        role_arn="arn:aws:iam::123456789012:role/partyplanner-deploy",
        state_bucket="my-tf-state",
    )
    occasion = config.load(tmp_path / "occasions" / "2026" / "occasion.yaml")
    assert occasion.title == "2026"


def test_cli_init_and_new_with_deprecated_scaffold_aliases(tmp_path):
    from click.testing import CliRunner

    from partyplanner.cli import main

    runner = CliRunner()
    init_args = [
        "--dir",
        str(tmp_path),
        "--zone",
        "events.example.com",
        "--budget-email",
        "you@example.com",
        "--github-repo",
        "1512-ninja/events",
        "--state-bucket",
        "my-tf-state",
    ]
    result = runner.invoke(main, ["init", *init_args])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "partyplanner.yaml").exists()

    result = runner.invoke(main, ["scaffold", "bootstrap", *init_args, "--force"])
    assert result.exit_code == 0, result.output

    new_args = [
        "bbq-2026",
        "--dir",
        str(tmp_path),
        "--domain",
        "bbq-2026.events.example.com",
        "--zone-id",
        "Z0123456789EXAMPLE",
        "--role-arn",
        "arn:aws:iam::123456789012:role/partyplanner-deploy",
    ]
    result = runner.invoke(main, ["new", *new_args])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "occasions/bbq-2026/occasion.yaml").exists()

    result = runner.invoke(main, ["scaffold", "occasion", *new_args, "--force"])
    assert result.exit_code == 0, result.output
    assert "scaffold" not in runner.invoke(main, ["--help"]).output
