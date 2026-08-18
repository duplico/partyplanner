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
@click.option("--port", default=8000, show_default=True, help="Local port (0 picks a free one).")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address; 0.0.0.0 exposes the preview beyond loopback (e.g. WSL2 to Windows).",
)
@click.option("--open", "open_browser", is_flag=True, help="Open the landing page in a browser.")
def preview(config_path: Path, port: int, host: str, open_browser: bool) -> None:
    """Render and serve the site locally, with an in-memory RSVP API.

    Prints a local URL for the landing page and each invitation link so every
    view can be checked. Unminted events/links get preview-only ids/tokens
    (the config file is never modified). RSVPs work but live in memory only.
    Edits to the config or its assets re-render and refresh the browser.
    """
    from .preview import run_preview

    occasion = _load(config_path)
    try:
        run_preview(occasion, config_path, port, open_browser, echo=click.echo, host=host)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e


def _print_links(occasion) -> None:
    for link in occasion.links:
        label = link.prefill_name or link.note or "(unlabeled)"
        url = f"https://{occasion.domain}/i/{link.token}/" if link.token else "(no token yet)"
        click.echo(f"{label}\t{url}")


@main.command()
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def links(config_path: Path) -> None:
    """Print the invitation links for an occasion."""
    _print_links(_load(config_path))


@main.group()
def link() -> None:
    """Manage generated invitation links (stored in .links.yaml beside the config)."""


@link.command("add")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--scope",
    default="all",
    show_default=True,
    help="`all`, a named scope from the config, or comma-separated event ids.",
)
@click.option("--note", default=None, help="Who/what this link is for.")
@click.option("--prefill", "prefill_name", default=None, help="Pre-fill the RSVP name field.")
@click.option(
    "--slug",
    default=None,
    help="Friendly URL prefix: --slug visitors mints /i/visitors-<token>/.",
)
def link_add(
    config_path: Path,
    scope: str,
    note: str | None,
    prefill_name: str | None,
    slug: str | None,
) -> None:
    """Mint a new invitation link into .links.yaml (commit the result)."""
    occasion = _load(config_path)
    scope_value: str | list[str] = scope
    if scope != "all" and scope not in occasion.scopes:
        scope_value = [s.strip() for s in scope.split(",") if s.strip()]
        if not scope_value:
            raise click.ClickException(
                "--scope must be `all`, a named scope, or comma-separated event ids"
            )
    try:
        new = config_mod.add_link(
            config_path, scope_value, note=note, prefill_name=prefill_name, slug=slug
        )
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    label = new.prefill_name or new.note or "(unlabeled)"
    click.echo(f"{label}\thttps://{occasion.domain}/i/{new.token}/")


@link.command("admin")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def link_admin(config_path: Path) -> None:
    """Print the occasion's admin key, minting one into .links.yaml if needed.

    Append `?me=<key>` to any invitation URL to edit or remove anyone's RSVP.
    Keep it private — anyone with the key can change every RSVP.
    """
    occasion = _load(config_path)
    try:
        key, minted = config_mod.ensure_admin_key(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    if minted:
        click.echo("minted an admin key (commit .links.yaml and redeploy to activate it)")
    click.echo(f"admin key: {key}")
    if occasion.links and occasion.links[0].token:
        click.echo(f"e.g. https://{occasion.domain}/i/{occasion.links[0].token}/?me={key}")


@link.command("revoke")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
@click.argument("token")
def link_revoke(config_path: Path, token: str) -> None:
    """Move a link's token to `revoked:` so the URL 404s on the next deploy."""
    try:
        config_mod.revoke_link(config_path, token)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"revoked {token}")


@link.command("list")
@click.argument("config_path", type=click.Path(exists=True, path_type=Path))
def link_list(config_path: Path) -> None:
    """Print the invitation links for an occasion."""
    _print_links(_load(config_path))


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
@click.option(
    "--github-repo",
    required=True,
    help="This events repo, as ORG/REPO (for OIDC); ORG@ID/REPO@ID if your org"
    " issues ID-pinned subject claims.",
)
@click.option(
    "--state-bucket",
    envvar="TF_STATE_BUCKET",
    default=None,
    help="Terraform state bucket, recorded in partyplanner.yaml and the"
    " generated backend blocks (default: $TF_STATE_BUCKET).",
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
    backend_config = "" if state_bucket else ' -backend-config="bucket=$TF_STATE_BUCKET"'
    click.echo(f"next: cd bootstrap && terraform init{backend_config} && terraform apply")


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
    click.echo(
        f"next: edit occasions/{name}/occasion.yaml, `partyplanner mint` it, then "
        "`partyplanner link add` to create invitation links"
    )


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
