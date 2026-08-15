from __future__ import annotations

import datetime as dt

from .config import Event, Occasion

DEFAULT_DURATION = dt.timedelta(hours=3)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 line folding: max 75 octets per line."""
    out: list[str] = []
    raw = line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 sequence
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def _utc(t: dt.datetime, occasion: Occasion) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=occasion.tz)
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def event_ics(occasion: Occasion, event: Event, event_id: str) -> str:
    assert event.when is not None
    start = event.when
    end = event.end or start + DEFAULT_DURATION
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//partyplanner//EN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{event_id}@{occasion.domain}",
        f"DTSTAMP:{now}",
        f"DTSTART:{_utc(start, occasion)}",
        f"DTEND:{_utc(end, occasion)}",
        f"SUMMARY:{_escape(f'{event.title} — {occasion.title}')}",
    ]
    if event.where:
        lines.append(f"LOCATION:{_escape(event.where)}")
    if event.blurb:
        lines.append(f"DESCRIPTION:{_escape(event.blurb.strip())}")
    lines += [
        f"URL:https://{occasion.domain}/",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
