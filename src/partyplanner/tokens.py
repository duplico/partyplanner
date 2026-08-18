import base64
import re
import secrets
import unicodedata

KEY_BYTES = 12  # 96 bits: edit/admin keys guard writes and stay full-strength
KEY_RE = re.compile(r"^[a-z2-7]{16,64}\Z")  # must match the RSVP Lambda's bounds

# Invitation tokens are the lowest-stakes secret (see docs/security-model.md),
# so their random tail is 50 bits and they may carry a host-chosen slug prefix
# for readability: `visitors-a7k2m6qexz`.
TOKEN_CHARS = 10  # 50 bits of base32
B32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_SLUG = 40
TOKEN_RE = re.compile(r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*-)?[a-z2-7]{10,64}\Z")
MAX_TOKEN = 80  # must match the RSVP Lambda's and CloudFront function's bounds


def mint_key() -> str:
    raw = secrets.token_bytes(KEY_BYTES)
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def mint_token(slug: str | None = None) -> str:
    tail = "".join(secrets.choice(B32_ALPHABET) for _ in range(TOKEN_CHARS))
    return f"{slug}-{tail}" if slug else tail


def derive_id(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:64].rstrip("-")
    if not slug:
        raise ValueError(f"cannot derive an id from title {title!r}; set an explicit id")
    return slug
