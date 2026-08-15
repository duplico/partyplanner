from __future__ import annotations

import datetime as dt

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
