"""Local preview: render the site to a temp dir and serve it with an
in-memory stand-in for the RSVP API.

The stand-in mirrors the production Lambda's contract (GET /api/state,
POST /api/rsvp: scope enforcement, name normalization, casefolded upsert,
prefill suppression) but keeps RSVPs in memory — nothing touches AWS and
nothing persists after the server stops.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .aws import link_items
from .config import Occasion
from .render import render
from .tokens import derive_id, mint_token

RESPONSES = ("yes", "maybe", "no")
MAX_NAME = 40
MAX_PARTY = 10
MAX_BODY = 2048
EVENT_ID_RE = re.compile(r"^[a-z0-9-]{1,64}\Z")


def completed(occasion: Occasion) -> tuple[Occasion, list[str]]:
    """A copy with preview-only ids/tokens filled in wherever `mint` hasn't run.

    Returns the completed occasion and notes describing what was filled in.
    """
    data = occasion.model_dump()
    notes: list[str] = []
    for event in data["events"]:
        if event["id"] is None:
            event["id"] = derive_id(event["title"])
    for i, link in enumerate(data["links"]):
        if link["token"] is None:
            link["token"] = mint_token()
            label = link["prefill_name"] or link["note"] or f"link #{i + 1}"
            notes.append(f"unminted link {label!r}: using a preview-only token")
    if not data["links"]:
        data["links"] = [{"token": mint_token(), "note": "preview-only link (scope: all)"}]
        notes.append("no links in config: added a preview-only link with scope `all`")
    return Occasion.model_validate(data), notes


class PreviewError(ValueError):
    pass


class PreviewStore:
    """In-memory equivalent of the occasion's DynamoDB table."""

    def __init__(self, occasion: Occasion) -> None:
        self.links = {item["pk"].removeprefix("LINK#"): item for item in link_items(occasion)}
        self.rsvps: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()  # requests run in ThreadingHTTPServer threads

    def state(self, token: str) -> dict:
        link = self.links.get(token)
        if link is None:
            raise PreviewError("unknown link")
        with self._lock:
            snapshot = [(eid, dict(row)) for (eid, _), row in self.rsvps.items()]
        events: dict[str, list[dict]] = {}
        prefill = link.get("prefill_name")
        for event_id in link["rsvp_events"]:
            rows = [row for eid, row in snapshot if eid == event_id]
            rows.sort(key=lambda r: (RESPONSES.index(r["response"]), r["name"].casefold()))
            events[event_id] = rows
            if prefill and any(r["name"].casefold() == prefill.casefold() for r in rows):
                prefill = None
        return {"events": events, "prefill": prefill}

    def rsvp(self, body: dict) -> None:
        token = body.get("token")
        link = self.links.get(token) if isinstance(token, str) else None
        if link is None:
            raise PreviewError("unknown link")
        event_id = body.get("event_id")
        if not isinstance(event_id, str) or not EVENT_ID_RE.match(event_id):
            raise PreviewError("bad event_id")
        if event_id not in link["rsvp_events"]:
            raise PreviewError("this link cannot RSVP to that event")
        name = body.get("name")
        if not isinstance(name, str):
            raise PreviewError("bad name")
        name = " ".join(name.split())
        if not 1 <= len(name) <= MAX_NAME or not name.isprintable():
            raise PreviewError(f"name must be 1-{MAX_NAME} printable characters")
        response = body.get("response")
        if response not in RESPONSES:
            raise PreviewError("response must be yes, maybe, or no")
        party_size = body.get("party_size", 0)
        if not isinstance(party_size, int) or isinstance(party_size, bool):
            raise PreviewError("bad party_size")
        if not 0 <= party_size <= MAX_PARTY:
            raise PreviewError(f"party_size must be 0-{MAX_PARTY}")
        with self._lock:
            self.rsvps[(event_id, name.casefold())] = {
                "name": name,
                "response": response,
                "party_size": party_size,
            }


class PreviewHandler(SimpleHTTPRequestHandler):
    store: PreviewStore  # set by make_server

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            if url.path.endswith("/state"):
                token = parse_qs(url.query).get("t", [""])[0]
                try:
                    self._json(200, self.store.state(token))
                except PreviewError as e:
                    self._json(400, {"error": str(e)})
            else:
                self._json(404, {"error": "not found"})
            return
        super().do_GET()

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if not (url.path.startswith("/api/") and url.path.endswith("/rsvp")):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(400, {"error": "body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise PreviewError("invalid JSON")
            self.store.rsvp(body)
            self._json(200, {"ok": True})
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
        except PreviewError as e:
            self._json(400, {"error": str(e)})


def make_server(occasion: Occasion, site_dir: Path, port: int) -> ThreadingHTTPServer:
    handler = type(
        "BoundPreviewHandler",
        (PreviewHandler,),
        {"store": PreviewStore(occasion)},
    )
    return ThreadingHTTPServer(("127.0.0.1", port), partial(handler, directory=str(site_dir)))


def preview_urls(occasion: Occasion, base: str) -> list[tuple[str, str]]:
    """(label, local URL) pairs: the landing page plus every link's page."""
    urls = [("landing page", f"{base}/")]
    for i, link in enumerate(occasion.links):
        label = link.prefill_name or link.note or f"link #{i + 1}"
        scope = link.scope if isinstance(link.scope, str) else " ".join(link.scope)
        urls.append((f"{label} (scope: {scope})", f"{base}/i/{link.token}/"))
    return urls


def run_preview(
    occasion: Occasion,
    config_dir: Path,
    port: int,
    open_browser: bool,
    echo=print,
) -> None:
    occasion, notes = completed(occasion)
    for note in notes:
        echo(note)
    with tempfile.TemporaryDirectory(prefix="partyplanner-preview-") as tmp:
        out_dir = Path(tmp)
        render(occasion, config_dir, out_dir)
        server = make_server(occasion, out_dir / "site", port)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        rendered_at = dt.datetime.now().strftime("%H:%M:%S")
        echo(f"previewing {occasion.title!r} (rendered {rendered_at}; Ctrl+C to stop)")
        for label, url in preview_urls(occasion, base):
            echo(f"  {label}\t{url}")
        if open_browser:
            import webbrowser

            webbrowser.open(f"{base}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
