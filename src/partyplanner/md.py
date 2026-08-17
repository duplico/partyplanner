from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
from markupsafe import Markup

# "js-default" escapes raw HTML instead of passing it through, and soft line
# breaks stay inside one paragraph (blank lines start a new one).
_md = MarkdownIt("js-default")

_TAG_RE = re.compile(r"<[^>]+>")


def md_html(text: str) -> Markup:
    """Render Markdown to HTML that is safe to inject (raw HTML is escaped)."""
    return Markup(_md.render(text))


def md_plain(text: str) -> str:
    """Markdown stripped to plain text, for meta descriptions and the like."""
    return html.unescape(_TAG_RE.sub("", _md.renderInline(text)))
