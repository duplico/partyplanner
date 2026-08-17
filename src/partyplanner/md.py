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
    """Markdown stripped to unescaped plain text.

    NOT safe to inject into HTML directly — only use where the consumer
    escapes it (e.g. an autoescaped Jinja template attribute).
    """
    return html.unescape(_TAG_RE.sub("", _md.renderInline(text)))
