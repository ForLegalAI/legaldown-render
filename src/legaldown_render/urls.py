"""Reading URLs the way the renderer needs to: which schemes a link may use
(§8.7), whether an image path is a relative one (§2.3), and how to quote one in
a message.

The checks are syntactic, like the validator's (§2.3). They are shared by the
resolver, which decides what an image is, and by both writers, which decide
what a link is, so that the outputs agree.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote

#: The schemes a link may use: the web, mail and phone, file transfer, and a
#: few that name a thing rather than start a program. Nothing else is a link:
#: a protocol handler (``ms-msdt:``, ``search-ms:``, ``vscode:``, ``intent:``),
#: a pseudo-scheme (``C:\x``, ``localhost:8080``), script, ``file:``, and
#: ``data:`` render as their text, and the resolver reports it. §8.7 requires
#: links to be hyperlinks, but a renderer reads documents it did not write
#: (docs/concepts.md, principle 6), so it is an allow-list rather than a list
#: of what is known to be dangerous. A URL with no scheme (a relative path, a
#: ``#fragment``) is a link too. markdown-it's own ``validateLink`` is turned
#: off in the builder, so that the resolver can see and report an image path
#: that is not relative (§2.3); this list is the gate, and the HTML writer
#: applies it to every ``href`` and ``src`` it emits (a link, an image, a
#: value's link).
LINK_SCHEMES = frozenset({
    "http", "https", "mailto", "tel", "ftp", "ftps", "sftp", "sip", "sips", "sms", "urn", "geo", "news",
})

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
# What the WHATWG URL parser ignores before it reads a scheme: tabs and
# newlines anywhere, and C0 controls and spaces at either end. Checking the
# URL as a browser will read it means "java\tscript:" cannot slip through.
_URL_IGNORED_RE = re.compile(r"[\t\n\r]")
_URL_TRIMMED = "".join(chr(code) for code in range(0x21))


def _as_read(url: str) -> str:
    """*url* as a browser reads it, before the scheme."""
    return _URL_IGNORED_RE.sub("", url).strip(_URL_TRIMMED)


def url_scheme(url: str) -> str | None:
    """The lower-cased scheme of *url* as a browser reads it, or None."""
    scheme = _SCHEME_RE.match(_as_read(url))
    return scheme.group(1).lower() if scheme else None


def is_safe_href(href: str) -> bool:
    """True if *href* has no scheme, or one in :data:`LINK_SCHEMES`, judged as a
    browser parses the URL."""
    scheme = url_scheme(href)
    return scheme is None or scheme in LINK_SCHEMES


def is_link_target(href: str) -> bool:
    """True if a link to *href* is made: it names something, and its scheme is
    one links may use. An empty destination would be a link to the page itself.
    Both writers and the resolver's report use this one predicate."""
    return bool(_as_read(href)) and is_safe_href(href)


def displayed_url(href: str) -> str:
    """*href* as the document wrote it, to show where a link has no text: the
    percent-encoding markdown-it added is undone (``a%20b`` reads ``a b``),
    unless that would show a control or format character."""
    url = href.strip()
    shown = unquote(url)
    return url if any(unicodedata.category(c) in ("Cc", "Cf") for c in shown) else shown


def printable(text: str) -> str:
    """*text*, a path or URL from a document, made safe to quote in a message:
    every control character (C0, C1, DEL), Unicode format character (bidi
    controls, zero-width, ...), and line or paragraph separator is written as
    ``\\xNN`` or ``\\uNNNN``, so that it cannot move the cursor, set a title,
    or start a line of its own on a terminal."""
    return "".join(_escaped(c) for c in text)


def _escaped(c: str) -> str:
    if unicodedata.category(c) not in ("Cc", "Cf") and c not in "\u2028\u2029":
        return c
    code = ord(c)
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def is_empty_path(path: str) -> bool:
    """True if *path* names nothing: empty, or only what a browser ignores. As
    an image's source it would request the page itself."""
    return not _as_read(unquote(path))


def is_relative_path(path: str) -> bool:
    """True if *path*, an image's, is a relative path (§2.3). Not relative: a
    path with a URI scheme (``https://``, ``file://``, ``data:``), which
    includes a Windows drive path (``C:\\x``, ``C:/x``, a scheme as far as a
    parser can tell), and one that starts with ``/`` or ``\\`` (so also
    ``//host``). A leading ``../`` is relative: §2.3 defines no default
    document root. A percent-encoded character counts as the character, since
    a path may be encoded by the time it is read."""
    read = _as_read(unquote(path))
    return not read or (_SCHEME_RE.match(read) is None and read[0] not in "/\\")
