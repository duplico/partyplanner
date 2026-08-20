"""Generate the consumer (events) repo skeleton from templates.

The generated files are the same roots documented in docs/bootstrapping.md:
a bootstrap Terraform root (hosted zones, budget alarm, OIDC deploy role) and
per-occasion capsules (occasion.yaml stub, Terraform root, deploy/destroy
workflows).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .config import ConfigError

REPO_CONFIG = "partyplanner.yaml"
REPO_CONFIG_KEYS = ("state_bucket", "region", "branch", "ref", "timezone")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")
# optionally ID-pinned (`owner@id/repo@id`), for orgs issuing pinned OIDC subs
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+(@\d+)?/[A-Za-z0-9_.-]+(@\d+)?\Z")
ZONE_ID_RE = re.compile(r"^Z[A-Z0-9]{1,31}\Z")
ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role/[\w+=,.@/-]+\Z")
BUCKET_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\Z")
IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}\Z")
BUCKET_RESERVED_PREFIXES = ("xn--", "sthree-", "amzn-s3-demo-")
BUCKET_RESERVED_SUFFIXES = ("-s3alias", "--ol-s3", "--x-s3", "--table-s3")
HOSTNAME_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,128}\Z")
REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d\Z")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\Z")

_env = Environment(
    loader=PackageLoader("partyplanner", "scaffold"),
    autoescape=False,  # output is HCL/YAML, not HTML
    keep_trailing_newline=True,
)

# The canonical kitchen-sink example occasion, shipped in the package (also
# rendered in CI and exercised by the test suite).
EXAMPLE_DIR = Path(__file__).parent / "example"
_EXAMPLE_HEADER = """\
# The kitchen-sink example occasion: every config feature is demonstrated
# (theme, favicon, landing page with photo/embed, markdown blurbs, where_url,
# per-event photos/accents, rsvp: none, RSVP caps, assets overrides). Trim it
# down to your event, then run `partyplanner link add` to create invitation
# links (written to .links.yaml beside this file). Commit both.
"""


def _example_occasion_yaml(*, domain: str, timezone: str) -> str:
    lines = (EXAMPLE_DIR / "occasion.yaml").read_text(encoding="utf-8").splitlines(keepends=True)
    while lines and lines[0].startswith("#"):
        del lines[0]
    for i, line in enumerate(lines):
        if line.startswith("domain:"):
            lines[i] = f"domain: {domain}\n"
        elif line.startswith("timezone:"):
            lines[i] = f"timezone: {timezone}\n"
    return _EXAMPLE_HEADER + "".join(lines)


def _example_files(occasion_dir: Path) -> list[tuple[Path, str]]:
    """The example's supporting files (assets, embed, overrides).

    occasion.yaml is emitted separately (with the domain/timezone swapped in)
    and .links.yaml is never copied: its fixture tokens are fake, and every
    occasion must mint its own."""
    files: list[tuple[Path, str]] = []
    for src in sorted(EXAMPLE_DIR.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(EXAMPLE_DIR)
        if rel.parts[0] in {"occasion.yaml", ".links.yaml"}:
            continue
        files.append((occasion_dir / rel, src.read_text(encoding="utf-8")))
    return files


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.match(value):
        raise ConfigError(f"{what} {value!r} is not valid")
    return value


def _check_bucket(value: str) -> str:
    ok = (
        3 <= len(value) <= 63
        and BUCKET_RE.match(value)
        and not IPV4_RE.match(value)
        and not value.startswith(BUCKET_RESERVED_PREFIXES)
        and not value.endswith(BUCKET_RESERVED_SUFFIXES)
    )
    if not ok:
        raise ConfigError(f"state bucket {value!r} is not a valid S3 bucket name")
    return value


def _write_all(
    files: list[tuple[Path, str]],
    *,
    force: bool = False,
    preserve: frozenset[Path] = frozenset(),
) -> tuple[list[Path], list[Path]]:
    """Write the given files, returning (written, kept).

    Without force this is all-or-nothing: existence is checked up front,
    files are opened exclusively (never truncating something that appeared
    since the check), and files written so far are removed if a later write
    fails. With force, files are replaced atomically (temp file + rename) —
    except paths in `preserve` that already exist, which are kept as-is."""
    if not force:
        existing = [path for path, _ in files if path.exists()]
        if existing:
            listing = ", ".join(str(p) for p in existing)
            raise ConfigError(f"refusing to overwrite existing {listing} (--force to regenerate)")
        written: list[Path] = []
        try:
            for path, content in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as f:
                    f.write(content)
                written.append(path)
        except OSError as e:
            for path in written:
                path.unlink(missing_ok=True)
            raise ConfigError(f"scaffold write failed, nothing created: {e}") from e
        return [path for path, _ in files], []
    kept = [path for path, _ in files if path in preserve and path.exists()]
    replaced: list[Path] = []
    for path, content in files:
        if path in kept:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except OSError as e:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise ConfigError(f"scaffold write failed at {path}: {e}") from e
        replaced.append(path)
    return replaced, kept


def load_repo_config(root: Path) -> dict[str, str]:
    """Read shared defaults from partyplanner.yaml at the repo root."""
    path = root / REPO_CONFIG
    if not path.exists():
        return {}
    try:
        data = YAML(typ="safe").load(path) or {}
    except YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a YAML mapping")
    out: dict[str, str] = {}
    for key in REPO_CONFIG_KEYS:
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ConfigError(f"{path}: {key} must be a string")
        out[key] = value
    return out


def bootstrap_outputs(root: Path) -> dict:
    """Read the bootstrap root's Terraform outputs (deploy_role_arn, zone_ids)."""
    bootstrap_dir = root / "bootstrap"
    if not (bootstrap_dir / "main.tf").exists():
        raise ConfigError(
            f"no bootstrap root at {bootstrap_dir}; run `partyplanner init`"
            " and apply it, or pass --zone-id and --role-arn explicitly"
        )
    try:
        proc = subprocess.run(
            ["terraform", f"-chdir={bootstrap_dir}", "output", "-json"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as e:
        raise ConfigError(
            "terraform not found on PATH; pass --zone-id and --role-arn explicitly"
        ) from e
    except subprocess.CalledProcessError as e:
        raise ConfigError(f"terraform output failed:\n{e.stderr.strip()}") from e
    outputs = json.loads(proc.stdout)
    if not isinstance(outputs, dict):
        raise ConfigError("unexpected terraform output format")
    return {key: value.get("value") for key, value in outputs.items() if isinstance(value, dict)}


def zone_for_domain(zone_ids: dict[str, str], domain: str) -> str:
    """Pick the hosted zone the domain belongs to (longest-suffix match)."""
    matches = [z for z in zone_ids if domain == z or domain.endswith("." + z)]
    if not matches:
        raise ConfigError(
            f"no bootstrap zone matches domain {domain!r}"
            f" (zones: {sorted(zone_ids)}); pass --zone-id explicitly"
        )
    return zone_ids[max(matches, key=len)]


def _repo_config_content(*, state_bucket: str | None, region: str, branch: str, ref: str) -> str:
    bucket_line = (
        f'state_bucket: "{state_bucket}"\n'
        if state_bucket
        else "# state_bucket: your-tf-state-bucket\n"
    )
    return (
        "# Written by `partyplanner init`; read by `partyplanner new` for\n"
        "# shared defaults, so occasions only need a name and a --domain.\n"
        "# Safe to edit.\n"
        + bucket_line
        + f'region: "{region}"\n'
        + f'branch: "{branch}"\n'
        + f'ref: "{ref}"\n'
    )


def scaffold_bootstrap(
    root: Path,
    *,
    zones: list[str],
    budget_email: str,
    budget_limit: int,
    github_repo: str,
    state_bucket: str | None = None,
    branch: str = "default",
    region: str = "us-east-1",
    ref: str = "default",
    force: bool = False,
) -> tuple[list[Path], list[Path]]:
    """Write bootstrap/main.tf and partyplanner.yaml; returns (written, kept)."""
    for zone in zones:
        _check(HOSTNAME_RE, zone, "zone")
    _check(GITHUB_REPO_RE, github_repo, "github repo")
    if state_bucket is not None:
        _check_bucket(state_bucket)
    _check(REF_RE, branch, "branch")
    _check(REGION_RE, region, "region")
    _check(REF_RE, ref, "ref")
    _check(EMAIL_RE, budget_email, "budget email")
    if budget_limit < 1:
        raise ConfigError(f"budget limit {budget_limit!r} must be a positive number of USD")
    content = _env.get_template("bootstrap_main.tf.j2").render(
        zones=zones,
        budget_email=budget_email,
        budget_limit=budget_limit,
        github_repo=github_repo,
        state_bucket=state_bucket,
        branch=branch,
        region=region,
        ref=ref,
    )
    repo_config = _repo_config_content(
        state_bucket=state_bucket, region=region, branch=branch, ref=ref
    )
    return _write_all(
        [
            (root / "bootstrap" / "main.tf", content),
            (root / REPO_CONFIG, repo_config),
        ],
        force=force,
        preserve=frozenset({root / REPO_CONFIG}),
    )


def scaffold_occasion(
    root: Path,
    name: str,
    *,
    domain: str,
    zone_id: str | None = None,
    role_arn: str | None = None,
    state_bucket: str | None = None,
    timezone: str | None = None,
    branch: str | None = None,
    region: str | None = None,
    ref: str | None = None,
    example: bool = False,
    force: bool = False,
) -> tuple[list[Path], list[Path]]:
    """Write an occasion capsule + its workflows; returns (written, kept).

    With example, occasion.yaml is the kitchen-sink example (plus its assets)
    instead of the minimal stub. Values not passed explicitly come from
    partyplanner.yaml at the repo root and (for zone_id/role_arn) the
    bootstrap root's Terraform outputs."""
    repo_config = load_repo_config(root)
    state_bucket = state_bucket or repo_config.get("state_bucket")
    timezone = timezone or repo_config.get("timezone") or "America/Chicago"
    branch = branch or repo_config.get("branch") or "default"
    region = region or repo_config.get("region") or "us-east-1"
    ref = ref or repo_config.get("ref") or "default"
    if state_bucket is None:
        raise ConfigError(
            f"no state bucket: pass --state-bucket or set state_bucket in {REPO_CONFIG}"
        )
    if zone_id is None or role_arn is None:
        outputs = bootstrap_outputs(root)
        if role_arn is None:
            role_arn = outputs.get("deploy_role_arn")
            if not isinstance(role_arn, str):
                raise ConfigError(
                    "bootstrap outputs have no deploy_role_arn"
                    " (has it been applied?); pass --role-arn explicitly"
                )
        if zone_id is None:
            zone_ids = outputs.get("zone_ids")
            if not isinstance(zone_ids, dict):
                raise ConfigError(
                    "bootstrap outputs have no zone_ids"
                    " (has it been applied?); pass --zone-id explicitly"
                )
            zone_id = zone_for_domain(zone_ids, domain)
    _check(NAME_RE, name, "occasion name")
    _check(HOSTNAME_RE, domain, "domain")
    _check(ZONE_ID_RE, zone_id, "zone id")
    _check(ROLE_ARN_RE, role_arn, "role arn")
    _check_bucket(state_bucket)
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
        "when": f"{date.today() + timedelta(days=30):%Y-%m-%d} 15:00",
        "branch": branch,
        "region": region,
        "ref": ref,
    }

    def render(template: str) -> str:
        return _env.get_template(template).render(**ctx)

    occasion_yaml = (
        _example_occasion_yaml(domain=domain, timezone=timezone)
        if example
        else render("occasion.yaml.j2")
    )
    extras = _example_files(occasion_dir) if example else []
    return _write_all(
        [
            (occasion_dir / "occasion.yaml", occasion_yaml),
            *extras,
            (occasion_dir / "terraform" / "main.tf", render("occasion_main.tf.j2")),
            (workflows / f"deploy-{name}.yml", render("deploy_workflow.yml.j2")),
            (workflows / f"destroy-{name}.yml", render("destroy_workflow.yml.j2")),
        ],
        force=force,
        # never regenerate over hand-edited content (config holds minted
        # ids/tokens; example assets are the user's to replace)
        preserve=frozenset({occasion_dir / "occasion.yaml", *(path for path, _ in extras)}),
    )
