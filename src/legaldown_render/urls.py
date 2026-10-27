"""Reading URLs the way the renderer needs to: which schemes a link may use
(§8.7), and whether an image path is a relative one (§2.3).

The checks are syntactic, like the validator's (§2.3). They are shared by the
resolver, which decides what an image is, and by both writers, which decide
what a link is, so that the outputs agree.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

#: Schemes whose links are never made: script (``javascript:``, ``vbscript:``),
#: local files (``file:``), and ``data:``. This list is the gate: markdown-it's
#: own ``validateLink`` is turned off in the builder, so that the resolver can
#: see and report an image path that is not relative (§2.3), and the HTML
#: writer applies this list to every ``href`` and ``src`` it emits (a link, an
#: image, a value's link). Every other scheme (``ftp:``, ``sip:``, ``urn:``,
#: ...) is a hyperlink, since §8.7 requires links to be rendered as hyperlinks.
DENIED_SCHEMES = frozenset({"javascript", "vbscript", "file", "data"})

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
    """True unless *href* uses a scheme in :data:`DENIED_SCHEMES`, judged as a
    browser parses the URL."""
    return url_scheme(href) not in DENIED_SCHEMES


def is_link_target(href: str) -> bool:
    """True if a link to *href* is made: it names something, and is safe.
    An empty destination would be a link to the page itself."""
    return bool(_as_read(href)) and is_safe_href(href)


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
