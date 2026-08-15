from __future__ import annotations

import csv
import datetime as dt
import shutil
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from .config import ConfigError, Occasion, require_complete
from .ics import event_ics

env = Environment(
    loader=PackageLoader("partyplanner", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt_when(start: dt.datetime, end: dt.datetime | None, occasion: Occasion) -> str:
    if start.tzinfo is None:
        start = start.replace(tzinfo=occasion.tz)

    def clock(t: dt.datetime) -> str:
        hour = t.hour % 12 or 12
        return f"{hour}:{t.minute:02d} {t.strftime('%p')}"

    text = f"{start.strftime('%A, %B')} {start.day}, {start.year} · {clock(start)}"
    if end is not None:
        if end.tzinfo is None:
            end = end.replace(tzinfo=occasion.tz)
        if end.date() == start.date():
            text += f"–{clock(end)}"
        else:
            text += f" – {end.strftime('%A')} {clock(end)}"
    return f"{text} {start.strftime('%Z')}"


def _copy_asset(src: Path, rel: str, img_dir: Path) -> str:
    dest = (img_dir / rel).resolve()
    if not dest.is_relative_to(img_dir.resolve()):
        raise ConfigError(f"asset path {rel!r} escapes the config directory")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return f"/assets/img/{dest.relative_to(img_dir.resolve()).as_posix()}"


def render(occasion: Occasion, config_dir: Path, out_dir: Path) -> None:
    require_complete(occasion)
    site = out_dir / "site"
    if site.exists():
        shutil.rmtree(site)
    (site / "assets").mkdir(parents=True)
    img_dir = site / "assets" / "img"

    static_dir = Path(__file__).parent / "templates" / "static"
    for f in static_dir.iterdir():
        shutil.copyfile(f, site / "assets" / f.name)
    (site / "assets" / "custom.css").touch()  # replaced by overrides/ when present

    (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n")

    def asset(rel: str | None) -> str | None:
        return _copy_asset(config_dir / rel, rel, img_dir) if rel else None

    occasion_photo = asset(occasion.photo)

    events = {}
    for event in occasion.events:
        event_id = event.id
        assert event_id is not None
        ics_path = None
        if event.when is not None:
            (site / "ics").mkdir(exist_ok=True)
            (site / "ics" / f"{event_id}.ics").write_text(event_ics(occasion, event, event_id))
            ics_path = f"/ics/{event_id}.ics"
        events[event_id] = {
            "id": event_id,
            "title": event.title,
            "when": _fmt_when(event.when, event.end, occasion) if event.when else None,
            "where": event.where,
            "blurb": event.blurb,
            "photo": asset(event.photo),
            "rsvp_open": event.rsvp == "open",
            "ics": ics_path,
        }

    base_ctx = {
        "occasion": occasion,
        "occasion_photo": occasion_photo,
        "og_image": f"https://{occasion.domain}{occasion_photo}" if occasion_photo else None,
        "og_description": (occasion.blurb or "You're invited.").strip().splitlines()[0],
    }

    landing_ctx = dict(base_ctx, landing=occasion.landing, landing_photo=None, embed_html=None)
    if occasion.landing:
        landing_ctx["landing_photo"] = asset(occasion.landing.photo)
        if occasion.landing.embed:
            landing_ctx["embed_html"] = (config_dir / occasion.landing.embed).read_text()
    (site / "index.html").write_text(env.get_template("landing.html.j2").render(landing_ctx))

    page_tpl = env.get_template("page.html.j2")
    pages: dict[tuple[str, ...], str] = {}
    rows = []
    for i, link in enumerate(occasion.links):
        scope = occasion.resolve_scope(link)
        if scope not in pages:
            pages[scope] = page_tpl.render(dict(base_ctx, events=[events[i] for i in scope]))
        token_dir = site / "i" / str(link.token)
        token_dir.mkdir(parents=True)
        (token_dir / "index.html").write_text(pages[scope])
        rows.append(
            {
                "prefill_name": link.prefill_name or "",
                "note": link.note or "",
                "scope": " ".join(scope),
                "url": f"https://{occasion.domain}/i/{link.token}/",
            }
        )

    with (out_dir / "links.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["prefill_name", "note", "scope", "url"])
        writer.writeheader()
        writer.writerows(rows)

    overrides = config_dir / "overrides"
    if overrides.is_dir():
        shutil.copytree(overrides, site, dirs_exist_ok=True)
