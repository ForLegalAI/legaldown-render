"""Reading URLs the way the renderer needs to: which schemes a link may use
(§8.7), whether a file reference is a relative path within the document root
(§2.3), and how to quote one in a message.

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
# A segment Windows reads as ``..``: it drops trailing dots and spaces (``.. ``, ``...``).
_PARENT_RE = re.compile(r"^\.\.[. ]*$")
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


def file_reference_problem(path: str, *, base: str | None = None, url: bool = False) -> str | None:
    """What is wrong with *path* as a file reference (§2.3), or None:

    * ``"not-relative"`` (``path-not-relative``): it has a URI scheme
      (``https://``, ``file://``, ``data:``), which includes a Windows drive
      path (``C:\\x``, ``C:/x``, a scheme as far as a parser can tell), or
      starts with ``/`` or ``\\`` (so also ``//host``), or has a control
      character (NUL, a newline, ...);
    * ``"outside-root"`` (``path-outside-root``): there is a document root
      (*base* is not None) and the path leaves it.

    *base* is the directory of the document relative to the root, ``/``-separated
    (``"."`` for a document at the root, ``"contracts/2025"``), or None where no
    root is configured: §2.3 defines no default root, so ``../shared/a.lgd`` is
    then valid. Both checks are lexical, as §2.3 says: nothing is resolved or
    read, so a symbolic link is not followed, and a path that does not exist is
    as good as any. An empty path names nothing and has no problem.

    How the path is read: a backslash is a separator (the renderer's reading,
    so that a path that goes up on Windows is not missed; it matters only with a
    root, a leading one is already not relative), and so are ``.. ``, ``...``
    and ``. `` with trailing dots and spaces, which Windows reads as ``..`` and
    ``.``. A path with a control character (NUL, a newline, ...) is not a file
    name a renderer can name safely, and is ``not-relative``. What a
    browser ignores before it reads the scheme (tabs and newlines, control
    characters and spaces at either end) is ignored in finding one. *url* says the path is consumed as a URL
    (an image's source, a link's target), where percent-encoding counts as the
    character it encodes (``%2e%2e`` is ``..``, ``%2f`` a separator), once; a
    path from frontmatter or an ``{{include:}}`` is a file name and is read as
    written, in which ``%2e%2e`` is a directory with that name."""
    decoded = unquote(path) if url else path
    read = _as_read(decoded)
    if not read:
        return None
    if _SCHEME_RE.match(read) or read[0] in "/\\" or any(unicodedata.category(c) == "Cc" for c in decoded):
        return "not-relative"
    if base is None:
        return None
    depth = 0 if base == "." else len([part for part in base.split("/") if part])
    for part in read.replace("\\", "/").split("/"):
        if _PARENT_RE.match(part):
            depth -= 1
            if depth < 0:
                return "outside-root"
        elif part.rstrip(" ") not in ("", "."):
            depth += 1
    return None


def is_relative_path(path: str) -> bool:
    """True if *path*, an image's, is a relative path (§2.3): see
    :func:`file_reference_problem`, which also reads a path as a URL. A leading
    ``../`` is relative here: it is the containment rule, with a document root
    configured, that keeps a path within it."""
    return file_reference_problem(path, url=True) != "not-relative"
