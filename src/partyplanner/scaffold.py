"""Generate the consumer (events) repo skeleton from templates.

The generated files are the same roots documented in docs/bootstrapping.md:
a bootstrap Terraform root (hosted zones, budget alarm, OIDC deploy role) and
per-occasion capsules (occasion.yaml stub, Terraform root, deploy/destroy
workflows).
"""

from __future__ import annotations

import re
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader

from .config import ConfigError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
ZONE_ID_RE = re.compile(r"^Z[A-Z0-9]{1,31}\Z")
ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/[\w+=,.@/-]+\Z")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
HOSTNAME_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,128}\Z")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d\Z")

_env = Environment(
    loader=PackageLoader("partyplanner", "scaffold"),
    autoescape=False,  # output is HCL/YAML, not HTML
    keep_trailing_newline=True,
)


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.match(value):
        raise ConfigError(f"{what} {value!r} is not valid")
    return value


def _write(path: Path, content: str) -> Path:
    if path.exists():
        raise ConfigError(f"refusing to overwrite existing {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def scaffold_bootstrap(
    root: Path,
    *,
    zones: list[str],
    budget_email: str,
    budget_limit: int,
    github_repo: str,
    branch: str = "default",
    region: str = "us-east-1",
    ref: str = "default",
) -> list[Path]:
    """Write bootstrap/main.tf; returns the paths written."""
    for zone in zones:
        _check(HOSTNAME_RE, zone, "zone")
    _check(GITHUB_REPO_RE, github_repo, "github repo")
    _check(REF_RE, branch, "branch")
    _check(REGION_RE, region, "region")
    _check(REF_RE, ref, "ref")
    if "@" not in budget_email:
        raise ConfigError(f"budget email {budget_email!r} is not valid")
    content = _env.get_template("bootstrap_main.tf.j2").render(
        zones=zones,
        budget_email=budget_email,
        budget_limit=budget_limit,
        github_repo=github_repo,
        branch=branch,
        region=region,
        ref=ref,
    )
    return [_write(root / "bootstrap" / "main.tf", content)]


def scaffold_occasion(
    root: Path,
    name: str,
    *,
    domain: str,
    zone_id: str,
    role_arn: str,
    state_bucket: str,
    timezone: str = "America/Chicago",
    branch: str = "default",
    region: str = "us-east-1",
    ref: str = "default",
) -> list[Path]:
    """Write an occasion capsule + its workflows; returns the paths written."""
    _check(NAME_RE, name, "occasion name")
    _check(HOSTNAME_RE, domain, "domain")
    _check(ZONE_ID_RE, zone_id, "zone id")
    _check(ROLE_ARN_RE, role_arn, "role arn")
    _check(BUCKET_RE, state_bucket, "state bucket")
    _check(REF_RE, branch, "branch")
    _check(REGION_RE, region, "region")
    _check(REF_RE, ref, "ref")
    try:
        ZoneInfo(timezone)
    except (KeyError, ValueError) as e:
        raise ConfigError(f"unknown timezone {timezone!r}") from e
    occasion_dir = root / "occasions" / name
    workflows = root / ".github" / "workflows"
    ctx = {
        "name": name,
        "title": name.replace("-", " ").title(),
        "domain": domain,
        "zone_id": zone_id,
        "role_arn": role_arn,
        "state_bucket": state_bucket,
        "timezone": timezone,
        "when": "2026-06-20 15:00",
        "branch": branch,
        "region": region,
        "ref": ref,
    }

    def render(template: str) -> str:
        return _env.get_template(template).render(**ctx)

    return [
        _write(occasion_dir / "occasion.yaml", render("occasion.yaml.j2")),
        _write(occasion_dir / "terraform" / "main.tf", render("occasion_main.tf.j2")),
        _write(workflows / f"deploy-{name}.yml", render("deploy_workflow.yml.j2")),
        _write(workflows / f"destroy-{name}.yml", render("destroy_workflow.yml.j2")),
    ]
