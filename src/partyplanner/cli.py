from __future__ import annotations

import sys
from pathlib import Path

import click

from . import config as config_mod
from .config import ConfigError


def _load(config_path: Path):
    try:
        return config_mod.load(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e


@click.group()
def main() -> None:
    """Config-first invitation sites."""


@main.command()
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def validate(config_path: Path) -> None:
    """Validate an occasion config."""
    occasion = _load(config_path)
    click.echo(f"ok: {occasion.title} ({len(occasion.events)} events, {len(occasion.links)} links)")


@main.command()
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def mint(config_path: Path) -> None:
    """Fill in missing event ids and link tokens, writing them back to the config."""
    try:
        notes = config_mod.mint(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    for note in notes:
        click.echo(note)
    if not notes:
        click.echo("nothing to mint")


@main.command()
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), default=Path("out"), show_default=True)
def render(config_path: Path, out: Path) -> None:
    """Render the static site, ICS files, and links.csv into --out."""
    from .render import render as do_render

    occasion = _load(config_path)
    try:
        do_render(occasion, config_path.parent, out)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"rendered {out / 'site'} and {out / 'links.csv'}")


@main.command()
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def links(config_path: Path) -> None:
    """Print the invitation links for an occasion."""
    occasion = _load(config_path)
    for link in occasion.links:
        label = link.prefill_name or link.note or "(unlabeled)"
        url = f"https://{occasion.domain}/i/{link.token}/" if link.token else "(no token yet)"
        click.echo(f"{label}\t{url}")


@main.command("sync-links")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
@click.option("--table", "table_name", required=True, help="Occasion DynamoDB table name.")
def sync_links(config_path: Path, table_name: str) -> None:
    """Upsert link records into DynamoDB and delete revoked/removed ones."""
    from .aws import sync_links as do_sync

    occasion = _load(config_path)
    try:
        config_mod.require_complete(occasion, config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    active, stale = do_sync(occasion, table_name)
    click.echo(f"synced {active} links, removed {stale} stale tokens")


@main.command("init")
@click.option("--dir", "root", type=click.Path(path_type=Path), default=Path("."))
@click.option("--zone", "zones", multiple=True, required=True, help="Domain to host (repeatable).")
@click.option("--budget-email", required=True, help="Email for budget alerts.")
@click.option("--budget-limit", default=10, show_default=True, help="Monthly budget in USD.")
@click.option("--github-repo", required=True, help="This events repo, as ORG/REPO (for OIDC).")
@click.option(
    "--state-bucket",
    envvar="TF_STATE_BUCKET",
    default=None,
    help="Terraform state bucket, recorded in partyplanner.yaml (default: $TF_STATE_BUCKET).",
)
@click.option("--branch", default="default", show_default=True, help="Deployable branch.")
@click.option("--region", default="us-east-1", show_default=True)
@click.option("--ref", default="default", show_default=True, help="partyplanner ref to pin.")
@click.option("--force", is_flag=True, help="Regenerate over existing files.")
def init_cmd(
    root: Path,
    zones: tuple[str, ...],
    budget_email: str,
    budget_limit: int,
    github_repo: str,
    state_bucket: str | None,
    branch: str,
    region: str,
    ref: str,
    force: bool,
) -> None:
    """Set up an events repo: bootstrap/main.tf and partyplanner.yaml."""
    from .scaffold import scaffold_bootstrap

    try:
        written, kept = scaffold_bootstrap(
            root,
            zones=list(zones),
            budget_email=budget_email,
            budget_limit=budget_limit,
            github_repo=github_repo,
            state_bucket=state_bucket,
            branch=branch,
            region=region,
            ref=ref,
            force=force,
        )
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    for path in written:
        click.echo(f"wrote {path}")
    for path in kept:
        click.echo(f"kept {path}")
    click.echo(
        "next: cd bootstrap && "
        'terraform init -backend-config="bucket=$TF_STATE_BUCKET" && terraform apply'
    )


@main.command("new")
@click.argument("name")
@click.option("--dir", "root", type=click.Path(path_type=Path), default=Path("."))
@click.option("--domain", required=True, help="Site domain, e.g. bbq-2026.events.example.com.")
@click.option("--zone-id", default=None, help="Route53 zone id (default: bootstrap outputs).")
@click.option("--role-arn", default=None, help="Deploy role ARN (default: bootstrap outputs).")
@click.option(
    "--state-bucket", default=None, help="Terraform state bucket (default: partyplanner.yaml)."
)
@click.option("--timezone", default=None, help="[default: partyplanner.yaml or America/Chicago]")
@click.option("--branch", default=None, help="Deployable branch [default: partyplanner.yaml].")
@click.option("--region", default=None, help="[default: partyplanner.yaml or us-east-1]")
@click.option("--ref", default=None, help="partyplanner ref to pin [default: partyplanner.yaml].")
@click.option("--force", is_flag=True, help="Regenerate over existing files (keeps occasion.yaml).")
def new_cmd(
    name: str,
    root: Path,
    domain: str,
    zone_id: str | None,
    role_arn: str | None,
    state_bucket: str | None,
    timezone: str | None,
    branch: str | None,
    region: str | None,
    ref: str | None,
    force: bool,
) -> None:
    """Write occasions/NAME (config stub, Terraform root) and its workflows.

    Shared settings come from partyplanner.yaml and the applied bootstrap
    root's Terraform outputs; the options above override them.
    """
    from .scaffold import scaffold_occasion

    try:
        written, kept = scaffold_occasion(
            root,
            name,
            domain=domain,
            zone_id=zone_id,
            role_arn=role_arn,
            state_bucket=state_bucket,
            timezone=timezone,
            branch=branch,
            region=region,
            ref=ref,
            force=force,
        )
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    for path in written:
        click.echo(f"wrote {path}")
    for path in kept:
        click.echo(f"kept {path}")
    click.echo(f"next: edit occasions/{name}/occasion.yaml, then partyplanner mint it")


@main.group(hidden=True)
def scaffold() -> None:
    """Deprecated aliases: use `init` and `new`."""


scaffold.add_command(init_cmd, "bootstrap")
scaffold.add_command(new_cmd, "occasion")


@main.command("export-rsvps")
@click.option("--table", "table_name", required=True, help="Occasion DynamoDB table name.")
def export_rsvps(table_name: str) -> None:
    """Dump all RSVPs as CSV to stdout."""
    from defusedcsv import csv

    from .aws import export_rsvps as do_export

    rows = do_export(table_name)
    fields = ["event_id", "name", "response", "party_size", "created_at", "updated_at", "via_token"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)


if __name__ == "__main__":
    main()
