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
    """Upsert link records into DynamoDB and delete revoked ones."""
    from .aws import sync_links as do_sync

    occasion = _load(config_path)
    try:
        config_mod.require_complete(occasion, config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    active, revoked = do_sync(occasion, table_name)
    click.echo(f"synced {active} links, removed {revoked} revoked tokens")


def _defang(value: object) -> object:
    """Neutralize spreadsheet formula injection in guest-controlled CSV cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@main.command("export-rsvps")
@click.option("--table", "table_name", required=True, help="Occasion DynamoDB table name.")
def export_rsvps(table_name: str) -> None:
    """Dump all RSVPs as CSV to stdout."""
    import csv

    from .aws import export_rsvps as do_export

    rows = do_export(table_name)
    fields = ["event_id", "name", "response", "party_size", "created_at", "updated_at", "via_token"]
    writer = csv.DictWriter(sys.stdout, fieldnames=fields)
    writer.writeheader()
    writer.writerows({k: _defang(v) for k, v in row.items()} for row in rows)


if __name__ == "__main__":
    main()
