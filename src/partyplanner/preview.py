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
import os
import re
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .aws import link_items
from .config import ConfigError, Occasion, load
from .render import render
from .tokens import TOKEN_RE, derive_id, mint_token

RESPONSES = ("yes", "maybe", "no")
MAX_NAME = 40
MAX_PARTY = 10
MAX_BODY = 2048
EVENT_ID_RE = re.compile(r"^[a-z0-9-]{1,64}\Z")


def _link_key(note: str | None, prefill_name: str | None, scope: str | list[str]) -> tuple:
    return (note, prefill_name, tuple(scope) if isinstance(scope, list) else scope)


def completed(occasion: Occasion, previous: Occasion | None = None) -> tuple[Occasion, list[str]]:
    """A copy with preview-only ids/tokens filled in wherever `mint` hasn't run.

    Returns the completed occasion and notes describing what was filled in.
    Pass the last completed occasion as `previous` to keep preview-only tokens
    (and thus their URLs) stable across hot reloads.
    """
    prev_links = previous.links if previous is not None else []
    data = occasion.model_dump()
    notes: list[str] = []
    for event in data["events"]:
        if event["id"] is None:
            event["id"] = derive_id(event["title"])
    used = {link["token"] for link in data["links"] if link["token"] is not None}
    prev_tokens: dict[tuple, list[str]] = {}
    for pl in prev_links:
        if pl.token is not None:
            prev_tokens.setdefault(_link_key(pl.note, pl.prefill_name, pl.scope), []).append(
                pl.token
            )
    for i, link in enumerate(data["links"]):
        if link["token"] is None:
            key = _link_key(link["note"], link["prefill_name"], link["scope"])
            reusable = [t for t in prev_tokens.get(key, []) if t not in used]
            if reusable:
                link["token"] = reusable[0]
                prev_tokens[key].remove(reusable[0])
            else:
                link["token"] = mint_token()
                label = link["prefill_name"] or link["note"] or f"link #{i + 1}"
                notes.append(f"unminted link {label!r}: using a preview-only token")
            used.add(link["token"])
    if not data["links"]:
        token = prev_links[0].token if prev_links and prev_links[0].token else mint_token()
        data["links"] = [{"token": token, "note": "preview-only link (scope: all)"}]
        if previous is None:
            notes.append("no links in config: added a preview-only link with scope `all`")
    if data["admin_key"] is None:
        data["admin_key"] = previous.admin_key if previous is not None else mint_token()
        if previous is None:
            notes.append("no admin key: using a preview-only one")
    return Occasion.model_validate(data), notes


class PreviewError(ValueError):
    pass


class PreviewForbidden(PreviewError):
    pass


class PreviewStore:
    """In-memory equivalent of the occasion's DynamoDB table."""

    def __init__(self, occasion: Occasion) -> None:
        self.links: dict[str, dict] = {}
        self.admin_keys: set[str] = set()
        self.rsvps: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()  # requests run in ThreadingHTTPServer threads
        self.update(occasion)

    def update(self, occasion: Occasion) -> None:
        """Swap in the occasion's current links/admin key, keeping RSVPs."""
        items = link_items(occasion)
        self.links = {
            item["pk"].removeprefix("LINK#"): item
            for item in items
            if item["pk"].startswith("LINK#")
        }
        self.admin_keys = {
            item["pk"].removeprefix("ADMIN#") for item in items if item["pk"].startswith("ADMIN#")
        }

    def state(self, token: str, me: str = "") -> dict:
        link = self.links.get(token)
        if link is None:
            raise PreviewError("unknown link")
        with self._lock:
            snapshot = [(eid, dict(row)) for (eid, _), row in self.rsvps.items()]
        events: dict[str, list[dict]] = {}
        prefill = link.get("prefill_name")
        for event_id in link["rsvp_events"]:
            rows = [
                {
                    "name": row["name"],
                    "response": row["response"],
                    "party_size": row["party_size"],
                    "mine": bool(me) and row["edit_key"] == me,
                }
                for eid, row in snapshot
                if eid == event_id
            ]
            rows.sort(key=lambda r: (RESPONSES.index(r["response"]), r["name"].casefold()))
            events[event_id] = rows
            if prefill and any(r["name"].casefold() == prefill.casefold() for r in rows):
                prefill = None
        return {"events": events, "prefill": prefill, "admin": me in self.admin_keys}

    def rsvp(self, body: dict) -> dict:
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
        me = body.get("me")
        if me is not None and (not isinstance(me, str) or not TOKEN_RE.match(me)):
            raise PreviewError("bad me")
        admin = me in self.admin_keys
        remove = body.get("remove", False)
        if not isinstance(remove, bool):
            raise PreviewError("bad remove")
        if remove:
            with self._lock:
                existing = self.rsvps.get((event_id, name.casefold()))
                owner_key = existing["edit_key"] if existing else None
                if not (admin or (owner_key and owner_key == me)):
                    raise PreviewForbidden(
                        "only that RSVP's private edit link (or the host) can remove it"
                    )
                self.rsvps.pop((event_id, name.casefold()), None)
            return {"ok": True}
        response = body.get("response")
        if response not in RESPONSES:
            raise PreviewError("response must be yes, maybe, or no")
        party_size = body.get("party_size", 0)
        if not isinstance(party_size, int) or isinstance(party_size, bool):
            raise PreviewError("bad party_size")
        if not 0 <= party_size <= MAX_PARTY:
            raise PreviewError(f"party_size must be 0-{MAX_PARTY}")
        with self._lock:
            existing = self.rsvps.get((event_id, name.casefold()))
            owner_key = existing["edit_key"] if existing else None
            if owner_key and owner_key != me and not admin:
                raise PreviewForbidden(
                    "that name already has an RSVP here — use your private edit link to change it"
                )
            edit_key = owner_key or (me if me and not admin else mint_token())
            self.rsvps[(event_id, name.casefold())] = {
                "name": name,
                "response": response,
                "party_size": party_size,
                "edit_key": edit_key,
            }
        return {"ok": True, "me": edit_key}


class ReloadState:
    """Monotonic render counter polled by the injected auto-refresh script."""

    def __init__(self) -> None:
        self.version = 0


RELOAD_SCRIPT = (
    "<script>(function(){var v=%d;setInterval(function(){"
    "fetch('/__preview__/version').then(function(r){return r.json()})"
    ".then(function(d){if(d.version!==v){location.reload();}}).catch(function(){});"
    "},1000);})();</script>"
)


class PreviewHandler(SimpleHTTPRequestHandler):
    store: PreviewStore  # set by make_server
    reload_state: ReloadState | None = None

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
        if url.path == "/__preview__/version":
            if self.reload_state is None:
                self._json(404, {"error": "not found"})
            else:
                self._json(200, {"version": self.reload_state.version})
            return
        if url.path.startswith("/api/"):
            if url.path.endswith("/state"):
                params = parse_qs(url.query)
                token = params.get("t", [""])[0]
                me = params.get("me", [""])[0]
                if me and not TOKEN_RE.match(me):
                    me = ""
                try:
                    self._json(200, self.store.state(token, me))
                except PreviewError as e:
                    self._json(400, {"error": str(e)})
            else:
                self._json(404, {"error": "not found"})
            return
        if self.reload_state is not None and self._serve_html_with_reload(url.path):
            return
        super().do_GET()

    def _serve_html_with_reload(self, url_path: str) -> bool:
        """Serve an HTML file with the auto-refresh script injected."""
        if not (url_path.endswith("/") or url_path.endswith(".html")):
            return False
        path = Path(self.translate_path(url_path))
        if path.is_dir():
            path = path / "index.html"
        if not path.is_file():
            return False
        # Version before content: a racing reload then costs at most one
        # extra refresh, never a stale page with a too-new baseline.
        script = (RELOAD_SCRIPT % self.reload_state.version).encode()
        try:
            content = path.read_bytes()
        except OSError:
            return False  # swapped out mid-reload; let the default handler 404
        if b"</body>" in content:
            content = content.replace(b"</body>", script + b"</body>", 1)
        else:
            content += script
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)
        return True

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
            self._json(200, self.store.rsvp(body))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
        except PreviewForbidden as e:
            self._json(403, {"error": str(e)})
        except PreviewError as e:
            self._json(400, {"error": str(e)})


def make_server(
    occasion: Occasion,
    site_dir: Path,
    port: int,
    host: str = "127.0.0.1",
    store: PreviewStore | None = None,
    reload_state: ReloadState | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundPreviewHandler",
        (PreviewHandler,),
        {"store": store or PreviewStore(occasion), "reload_state": reload_state},
    )
    return ThreadingHTTPServer((host, port), partial(handler, directory=str(site_dir)))


def preview_urls(occasion: Occasion, base: str) -> list[tuple[str, str]]:
    """(label, local URL) pairs: the landing page plus every link's page."""
    urls = [("landing page", f"{base}/")]
    for i, link in enumerate(occasion.links):
        label = link.prefill_name or link.note or f"link #{i + 1}"
        scope = link.scope if isinstance(link.scope, str) else " ".join(link.scope)
        urls.append((f"{label} (scope: {scope})", f"{base}/i/{link.token}/"))
    if occasion.admin_key and occasion.links:
        urls.append(
            (
                "admin view (edit/remove any RSVP)",
                f"{base}/i/{occasion.links[0].token}/?me={occasion.admin_key}",
            )
        )
    return urls


class Reloader:
    """Re-render when files under the config dir change.

    RSVPs and preview-only tokens survive reloads; the browser refreshes
    itself via the injected script polling /__preview__/version.
    """

    def __init__(
        self,
        config_path: Path,
        out_dir: Path,
        store: PreviewStore,
        reload_state: ReloadState,
        base: str,
        occasion: Occasion,
        echo=print,
    ) -> None:
        self.config_path = config_path
        self.config_dir = config_path.parent
        self.out_dir = out_dir
        self.store = store
        self.reload_state = reload_state
        self.base = base
        self.occasion = occasion
        self.echo = echo

    def snapshot(self) -> dict[str, int]:
        """Mtimes of files under the config dir, skipping dot-directories
        (.git, .terraform, …) whose churn would cause spurious reloads.
        Dot-files like .links.yaml are still watched.
        """
        files: dict[str, int] = {}
        for root, dirs, names in os.walk(self.config_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                p = Path(root) / name
                try:
                    files[str(p)] = p.stat().st_mtime_ns
                except OSError:
                    continue
        return files

    def reload(self) -> bool:
        """Re-render the site; returns True on success.

        Renders into a staging dir and swaps it in only on success, so a
        broken edit never disturbs the currently served site.
        """
        urls_before = preview_urls(self.occasion, self.base)
        staging = self.out_dir / ".staging"
        try:
            fresh, _ = completed(load(self.config_path), previous=self.occasion)
            shutil.rmtree(staging, ignore_errors=True)
            render(fresh, self.config_dir, staging)
        except (ValueError, OSError) as e:
            shutil.rmtree(staging, ignore_errors=True)
            self.echo(f"reload failed (fix and save again): {e}")
            return False
        site = self.out_dir / "site"
        old = self.out_dir / ".old-site"
        try:
            shutil.rmtree(old, ignore_errors=True)
            site.rename(old)
            (staging / "site").rename(site)
        except OSError as e:
            if old.exists() and not site.exists():
                old.rename(site)
            shutil.rmtree(staging, ignore_errors=True)
            self.echo(f"reload failed (fix and save again): {e}")
            return False
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        self.occasion = fresh
        self.store.update(fresh)
        self.reload_state.version += 1
        self.echo(f"re-rendered at {dt.datetime.now():%H:%M:%S}")
        urls = preview_urls(fresh, self.base)
        if urls != urls_before:
            for label, url in urls:
                self.echo(f"  {label}\t{url}")
        return True

    def watch(self, stop: threading.Event, interval: float = 1.0) -> None:
        sig = self.snapshot()
        while not stop.wait(interval):
            new = self.snapshot()
            if new != sig:
                sig = new
                try:
                    self.reload()
                except Exception as e:  # keep watching: a dead watcher ends hot reload
                    self.echo(f"reload failed (fix and save again): {e}")


def run_preview(
    occasion: Occasion,
    config_path: Path,
    port: int,
    open_browser: bool,
    echo=print,
    host: str = "127.0.0.1",
) -> None:
    config_dir = config_path.parent
    occasion, notes = completed(occasion)
    for note in notes:
        echo(note)
    with tempfile.TemporaryDirectory(prefix="partyplanner-preview-") as tmp:
        out_dir = Path(tmp)
        render(occasion, config_dir, out_dir)
        store = PreviewStore(occasion)
        reload_state = ReloadState()
        try:
            server = make_server(occasion, out_dir / "site", port, host, store, reload_state)
        except OSError as e:
            raise ConfigError(f"cannot serve on {host} port {port}: {e}") from e
        display_host = "127.0.0.1" if host == "0.0.0.0" else host
        base = f"http://{display_host}:{server.server_address[1]}"
        if host != "127.0.0.1":
            echo(f"listening on {host} — reachable from other machines on the network")
        rendered_at = dt.datetime.now().strftime("%H:%M:%S")
        echo(f"previewing {occasion.title!r} (rendered {rendered_at}; Ctrl+C to stop)")
        echo("watching for changes — edits re-render and refresh the browser")
        for label, url in preview_urls(occasion, base):
            echo(f"  {label}\t{url}")
        reloader = Reloader(config_path, out_dir, store, reload_state, base, occasion, echo)
        stop = threading.Event()
        watcher = threading.Thread(target=reloader.watch, args=(stop,), daemon=True)
        watcher.start()
        if open_browser:
            import webbrowser

            webbrowser.open(f"{base}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            watcher.join(timeout=5)
            server.server_close()
