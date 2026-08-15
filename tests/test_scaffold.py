import pytest
from ruamel.yaml import YAML

from partyplanner import config
from partyplanner.config import ConfigError
from partyplanner.scaffold import scaffold_bootstrap, scaffold_occasion


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
    paths = _occasion(tmp_path)
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
    assert deploy["on"]["push"]["paths"] == ["occasions/bbq-2026/**"]
    assert destroy["on"] == "workflow_dispatch"


def test_scaffolded_terraform_pins_ref_and_state_key(tmp_path):
    _occasion(tmp_path, ref="v0.1.0")
    tf = (tmp_path / "occasions/bbq-2026/terraform/main.tf").read_text()
    assert "modules/occasion?ref=v0.1.0" in tf
    assert 'key    = "occasions/bbq-2026/terraform.tfstate"' in tf
    assert 'zone_id = "Z0123456789EXAMPLE"' in tf


def test_scaffold_occasion_refuses_overwrite(tmp_path):
    _occasion(tmp_path)
    with pytest.raises(ConfigError, match="refusing to overwrite"):
        _occasion(tmp_path)


@pytest.mark.parametrize(
    "overrides",
    [
        {"domain": "Not A Domain"},
        {"zone_id": "not-a-zone"},
        {"role_arn": "arn:aws:s3:::bucket"},
        {"state_bucket": "Bad_Bucket"},
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
    paths = scaffold_bootstrap(
        tmp_path,
        zones=["allhallowtide.party", "events.example.com"],
        budget_email="you@example.com",
        budget_limit=10,
        github_repo="1512-ninja/events",
    )
    assert paths == [tmp_path / "bootstrap" / "main.tf"]
    tf = paths[0].read_text()
    assert 'zones            = ["allhallowtide.party", "events.example.com"]' in tf
    assert "repo:1512-ninja/events:ref:refs/heads/default" in tf
    assert 'key    = "bootstrap/terraform.tfstate"' in tf


def test_scaffold_bootstrap_rejects_bad_repo(tmp_path):
    with pytest.raises(ConfigError, match="github repo"):
        scaffold_bootstrap(
            tmp_path,
            zones=["events.example.com"],
            budget_email="you@example.com",
            budget_limit=10,
            github_repo="not-a-repo",
        )
