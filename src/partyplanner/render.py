from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from defusedcsv import csv
from jinja2 import Environment, PackageLoader

from .config import ConfigError, Occasion, require_complete
from .ics import calendar_links, event_ics
from .md import md_html, md_plain

env = Environment(
    loader=PackageLoader("partyplanner", "templates"),
    autoescape=True,
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


def _theme_css(occasion: Occasion) -> str | None:
    if occasion.theme is None:
        return None
    lines = [
        f"  --{name}: {value};"
        for name, value in occasion.theme.model_dump().items()
        if value is not None
    ]
    if not lines:
        return None
    return ":root {\n" + "\n".join(lines) + "\n}"


def _contained(base: Path, rel: str) -> Path:
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ConfigError(f"path {rel!r} escapes {base}")
    return path


def _copy_asset(src: Path, rel: str, img_dir: Path) -> str:
    dest = _contained(img_dir, rel)
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
        return _copy_asset(_contained(config_dir, rel), rel, img_dir) if rel else None

    occasion_photo = asset(occasion.photo)

    favicon = "/assets/favicon.svg"
    if occasion.favicon:
        src = _contained(config_dir, occasion.favicon)
        favicon = f"/favicon{src.suffix.lower()}"
        shutil.copyfile(src, site / favicon.lstrip("/"))

    events = {}
    ics_texts = {}
    for event in occasion.events:
        event_id = event.id
        assert event_id is not None
        if event.when is not None:
            ics_texts[event_id] = event_ics(occasion, event, event_id)
        events[event_id] = {
            "id": event_id,
            "title": event.title,
            "when": _fmt_when(event.when, event.end, occasion) if event.when else None,
            "where": event.where,
            "blurb": md_html(event.blurb) if event.blurb else None,
            "photo": asset(event.photo),
            "accent": event.accent,
            "rsvp_open": event.rsvp == "open",
            "calendar_links": calendar_links(occasion, event) if event.when is not None else [],
            # Relative to the /i/<token>/ page, so calendar files stay behind
            # the capability URL instead of a guessable site-wide path.
            "ics": f"{event_id}.ics" if event.when is not None else None,
        }

    blurb_first_line = (occasion.blurb or "").strip().splitlines()
    base_ctx = {
        "occasion": occasion,
        "occasion_photo": occasion_photo,
        "occasion_blurb": md_html(occasion.blurb) if (occasion.blurb or "").strip() else None,
        "favicon": favicon,
        "theme_css": _theme_css(occasion),
        "og_image": f"https://{occasion.domain}{occasion_photo}" if occasion_photo else None,
        "og_description": (md_plain(blurb_first_line[0]) if blurb_first_line else "")
        or "You're invited.",
    }

    landing_ctx = dict(
        base_ctx, landing=occasion.landing, landing_blurb=None, landing_photo=None, embed_html=None
    )
    if occasion.landing:
        if occasion.landing.blurb:
            landing_ctx["landing_blurb"] = md_html(occasion.landing.blurb)
        landing_ctx["landing_photo"] = asset(occasion.landing.photo)
        if occasion.landing.embed:
            landing_ctx["embed_html"] = _contained(config_dir, occasion.landing.embed).read_text()
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
        for event_id in scope:
            if event_id in ics_texts:
                (token_dir / f"{event_id}.ics").write_text(ics_texts[event_id])
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
        for src in sorted(overrides.rglob("*")):
            rel = src.relative_to(overrides)
            resolved = _contained(overrides, str(rel))
            if resolved.is_dir():
                continue
            dest = site / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved, dest)
