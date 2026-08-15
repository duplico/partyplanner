import base64
import re
import secrets
import unicodedata

TOKEN_BYTES = 12  # 96 bits
TOKEN_RE = re.compile(r"^[a-z2-7]{16,}$")


def mint_token() -> str:
    raw = secrets.token_bytes(TOKEN_BYTES)
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def derive_id(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive an id from title {title!r}; set an explicit id")
    return slug
