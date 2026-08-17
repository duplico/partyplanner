from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

from icalendar import Calendar
from icalendar import Event as CalendarEvent

from .config import Event, Occasion

DEFAULT_DURATION = dt.timedelta(hours=3)


def _utc(t: dt.datetime, occasion: Occasion) -> dt.datetime:
    if t.tzinfo is None:
        t = t.replace(tzinfo=occasion.tz)
    return t.astimezone(dt.timezone.utc)


def _text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def event_ics(occasion: Occasion, event: Event, event_id: str) -> str:
    assert event.when is not None
    start = event.when
    end = event.end or start + DEFAULT_DURATION

    vevent = CalendarEvent()
    vevent.add("uid", f"{event_id}@{occasion.domain}")
    vevent.add("dtstamp", dt.datetime.now(dt.timezone.utc))
    vevent.add("dtstart", _utc(start, occasion))
    vevent.add("dtend", _utc(end, occasion))
    vevent.add("summary", _text(f"{event.title} — {occasion.title}"))
    if event.where:
        vevent.add("location", _text(event.where))
    if event.blurb:
        vevent.add("description", _text(event.blurb.strip()))
    vevent.add("url", f"https://{occasion.domain}/")

    cal = Calendar()
    cal.add("prodid", "-//partyplanner//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")
    cal.add_component(vevent)
    return cal.to_ical().decode("utf-8")


def _stamp(t: dt.datetime, occasion: Occasion) -> str:
    return _utc(t, occasion).strftime("%Y%m%dT%H%M%SZ")


def calendar_links(occasion: Occasion, event: Event) -> list[dict[str, str]]:
    """Web add-to-calendar URLs for the providers with URL templates."""
    assert event.when is not None
    start = event.when
    end = event.end or start + DEFAULT_DURATION
    title = f"{event.title} — {occasion.title}"
    details = _text(event.blurb.strip()) if event.blurb else ""

    google = urlencode(
        {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{_stamp(start, occasion)}/{_stamp(end, occasion)}",
            **({"details": details} if details else {}),
            **({"location": event.where} if event.where else {}),
        }
    )
    outlook = urlencode(
        {
            "path": "/calendar/action/compose",
            "rru": "addevent",
            "subject": title,
            "startdt": _utc(start, occasion).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "enddt": _utc(end, occasion).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **({"body": details} if details else {}),
            **({"location": event.where} if event.where else {}),
        }
    )
    yahoo = urlencode(
        {
            "v": "60",
            "title": title,
            "st": _stamp(start, occasion),
            "et": _stamp(end, occasion),
            **({"desc": details} if details else {}),
            **({"in_loc": event.where} if event.where else {}),
        }
    )
    return [
        {"label": "Google", "url": f"https://calendar.google.com/calendar/render?{google}"},
        {
            "label": "Outlook",
            "url": f"https://outlook.live.com/calendar/0/deeplink/compose?{outlook}",
        },
        {"label": "Yahoo", "url": f"https://calendar.yahoo.com/?{yahoo}"},
    ]
