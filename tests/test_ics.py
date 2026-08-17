from conftest import FIXTURES
from partyplanner import config
from partyplanner.ics import calendar_links, event_ics


def test_bbq_ics():
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    text = event_ics(occasion, occasion.events[0], "bbq")
    lines = text.split("\r\n")
    assert "BEGIN:VCALENDAR" in lines
    assert "UID:bbq@bbq.example.com" in lines
    # 15:00 America/Chicago (CDT, UTC-5) == 20:00 UTC; default 3h duration
    assert "DTSTART:20260620T200000Z" in lines
    assert "DTEND:20260620T230000Z" in lines
    assert "SUMMARY:BBQ — BBQ Saturday" in lines
    assert "LOCATION:123 Example Ave" in lines
    assert all(len(line.encode()) <= 75 for line in lines)


def test_ics_carriage_returns_normalized():
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    event = occasion.events[0].model_copy(update={"blurb": "line one\r\nline two\rline three"})
    text = event_ics(occasion, event, "bbq")
    unfolded = text.replace("\r\n ", "").replace("\r\n", "\n")
    assert "\r" not in unfolded
    assert "DESCRIPTION:line one\\nline two\\nline three" in unfolded


def test_ics_escaping_and_folding():
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    candy = next(e for e in occasion.events if e.id == "candy")
    text = event_ics(occasion, candy, "candy")
    assert "\\n" in text  # multi-line blurb escaped
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    unfolded = text.replace("\r\n ", "")
    assert "DESCRIPTION:Not a full party" in unfolded


def test_ics_explicit_end():
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    candy = next(e for e in occasion.events if e.id == "candy")
    text = event_ics(occasion, candy, "candy")
    lines = text.split("\r\n")
    # 17:00-20:00 America/Chicago (CDT, UTC-5) == 22:00-01:00 UTC
    assert "DTSTART:20261031T220000Z" in lines
    assert "DTEND:20261101T010000Z" in lines


def test_calendar_links_bbq():
    occasion = config.load(FIXTURES / "bbq" / "occasion.yaml")
    links = calendar_links(occasion, occasion.events[0])
    assert [entry["label"] for entry in links] == ["Google", "Outlook", "Yahoo"]
    google, outlook, yahoo = (entry["url"] for entry in links)
    # 15:00 America/Chicago (CDT, UTC-5) == 20:00 UTC; default 3h duration
    assert google.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE")
    assert "dates=20260620T200000Z%2F20260620T230000Z" in google
    assert "location=123+Example+Ave" in google
    assert outlook.startswith("https://outlook.live.com/calendar/0/action/compose?rru=addevent")
    assert "startdt=2026-06-20T20%3A00%3A00%2B00%3A00" in outlook
    assert yahoo.startswith("https://calendar.yahoo.com/?v=60")
    assert "st=20260620T200000Z&et=20260620T230000Z" in yahoo


def test_calendar_links_explicit_end():
    occasion = config.load(FIXTURES / "allhallowtide" / "occasion.yaml")
    candy = next(e for e in occasion.events if e.id == "candy")
    google = calendar_links(occasion, candy)[0]["url"]
    assert "dates=20261031T220000Z%2F20261101T010000Z" in google
