from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .tokens import TOKEN_RE, derive_id, mint_token

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 100


class ConfigError(ValueError):
    pass


class Landing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blurb: str | None = None
    photo: str | None = None
    embed: str | None = None  # path to an HTML fragment injected into the landing page


EVENT_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    title: str
    when: dt.datetime | None = None
    end: dt.datetime | None = None
    where: str | None = None
    blurb: str | None = None
    photo: str | None = None
    rsvp: Literal["open", "none"] = "open"

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str | None) -> str | None:
        if v is not None and not EVENT_ID_RE.match(v):
            raise ValueError(f"event id {v!r} must match [a-z0-9-] and be at most 64 chars")
        return v

    @field_validator("when", "end", mode="before")
    @classmethod
    def _parse_datetime(cls, v: object) -> object:
        if isinstance(v, str):
            return dt.datetime.fromisoformat(v)
        if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
            return dt.datetime(v.year, v.month, v.day)
        return v

    @model_validator(mode="after")
    def _check(self) -> Event:
        if self.rsvp == "open" and self.when is None:
            raise ValueError(f"event {self.title!r} has rsvp: open and needs a `when`")
        if self.end is not None and self.when is not None and self.end <= self.when:
            raise ValueError(f"event {self.title!r} ends before it starts")
        return self


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    scope: str | list[str] = "all"
    prefill_name: str | None = None
    note: str | None = None

    @field_validator("token")
    @classmethod
    def _check_token(cls, v: str | None) -> str | None:
        if v is not None and not TOKEN_RE.match(v):
            raise ValueError(f"token {v!r} is not lowercase base32 of at least 16 chars")
        return v


class Occasion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    domain: str
    timezone: str
    photo: str | None = None
    blurb: str | None = None
    landing: Landing | None = None
    events: list[Event] = Field(min_length=1)
    scopes: dict[str, list[str]] = Field(default_factory=dict)
    links: list[Link] = Field(default_factory=list)
    revoked: list[str] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v

    @model_validator(mode="after")
    def _check(self) -> Occasion:
        ids = [e.id for e in self.events if e.id is not None]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate event ids: {sorted(dupes)}")
        if any(e.id is None for e in self.events):
            titles = [e.title for e in self.events]
            if len(set(titles)) != len(titles):
                raise ValueError("event titles must be unique when ids are omitted")
            derived = [e.id or derive_id(e.title) for e in self.events]
            if len(set(derived)) != len(derived):
                raise ValueError(f"derived event ids collide: {derived}; set explicit ids")
        known = {e.id or derive_id(e.title) for e in self.events}
        for name, members in self.scopes.items():
            unknown = set(members) - known
            if unknown:
                raise ValueError(f"scope {name!r} references unknown events: {sorted(unknown)}")
        for link in self.links:
            self.resolve_scope(link)
            if link.token is not None and link.token in self.revoked:
                raise ValueError(
                    f"link token {link.token!r} is listed in `revoked:`; delete the token "
                    "from the link entry so a replacement is minted"
                )
        tokens = [link.token for link in self.links if link.token is not None]
        if len(set(tokens)) != len(tokens):
            raise ValueError("duplicate link tokens")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def event_ids(self) -> list[str]:
        return [e.id or derive_id(e.title) for e in self.events]

    def resolve_scope(self, link: Link) -> tuple[str, ...]:
        """Resolve a link's scope to an ordered tuple of event ids (config order)."""
        order = self.event_ids()
        if link.scope == "all":
            return tuple(order)
        if isinstance(link.scope, str):
            if link.scope not in self.scopes:
                raise ValueError(f"link references unknown scope preset {link.scope!r}")
            members = set(self.scopes[link.scope])
        else:
            unknown = set(link.scope) - set(order)
            if unknown:
                raise ValueError(f"link scope references unknown events: {sorted(unknown)}")
            members = set(link.scope)
        return tuple(i for i in order if i in members)

    def rsvp_open_ids(self) -> set[str]:
        return {e.id or derive_id(e.title) for e in self.events if e.rsvp == "open"}

    def latest_end(self) -> dt.datetime | None:
        """Latest event end (or start) as an aware datetime, for data TTL."""
        stamps = []
        for e in self.events:
            t = e.end or e.when
            if t is not None:
                stamps.append(t if t.tzinfo else t.replace(tzinfo=self.tz))
        return max(stamps) if stamps else None


def load_raw(path: Path) -> CommentedMap:
    with path.open() as f:
        data = yaml.load(f)
    if not isinstance(data, CommentedMap):
        raise ConfigError(f"{path}: expected a YAML mapping at the top level")
    return data


def parse(data: CommentedMap, path: Path | None = None) -> Occasion:
    try:
        return Occasion.model_validate(data)
    except ValueError as e:
        prefix = f"{path}: " if path else ""
        raise ConfigError(f"{prefix}{e}") from e


def load(path: Path) -> Occasion:
    return parse(load_raw(path), path)


def mint(path: Path) -> list[str]:
    """Fill in missing event ids and link tokens, writing back to the config file.

    Preserves comments and formatting. Returns human-readable notes of what changed.
    """
    data = load_raw(path)
    occasion = parse(data, path)  # validate before mutating
    notes: list[str] = []

    events = data.get("events")
    if isinstance(events, CommentedSeq):
        for node, event in zip(events, occasion.events, strict=True):
            if isinstance(node, CommentedMap) and "id" not in node:
                new_id = derive_id(event.title)
                node.insert(0, "id", new_id)
                notes.append(f"event {event.title!r}: id = {new_id}")

    links = data.get("links")
    if isinstance(links, CommentedSeq):
        for i, node in enumerate(links):
            if isinstance(node, CommentedMap) and "token" not in node:
                token = mint_token()
                node.insert(0, "token", token)
                label = node.get("prefill_name") or node.get("note") or f"link #{i + 1}"
                notes.append(f"link {label!r}: token = {token}")

    if notes:
        with path.open("w") as f:
            yaml.dump(data, f)
        parse(load_raw(path), path)  # re-validate what we wrote
    return notes


def require_complete(occasion: Occasion, path: Path | None = None) -> None:
    """Fail unless every event has an id and every link a token (i.e. `mint` was run)."""
    missing: list[str] = []
    missing += [f"event {e.title!r} has no id" for e in occasion.events if e.id is None]
    missing += [
        f"link {(link.prefill_name or link.note or f'#{i + 1}')!r} has no token"
        for i, link in enumerate(occasion.links)
        if link.token is None
    ]
    if missing:
        prefix = f"{path}: " if path else ""
        raise ConfigError(
            prefix
            + "; ".join(missing)
            + " — run `partyplanner mint` and commit the result before rendering"
        )
    if not occasion.links:
        raise ConfigError("no links defined; add at least one entry under `links:`")
