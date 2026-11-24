"""The HTML writer (§13.6).

It writes one self-contained HTML5 file, or only its ``<article>`` with
``standalone=False`` (the CLI's ``--fragment``): semantic markup
(``<section>``, ``<ol>``, ``<dfn>``, ``<a href>``) and a stylesheet generated
from the style template's presentation settings. Every label a reader sees —
"4.2", "(b)" — is real text in the markup rather than a CSS counter, so the
numbers are the ones resolution computed and survive copying and printing.

Safety (docs/architecture.md): every text node and attribute is escaped, raw
HTML from the source never reaches this writer (§8.7), and a link whose URL
is empty or uses a scheme outside the allow-list
(:data:`~legaldown_render.urls.LINK_SCHEMES`, judged as a browser reads the
URL: script, ``file:``, ``data:``, protocol handlers) renders as its text
alone, and the resolver reports it. Every other link is a hyperlink (§8.7).
This check is the gate for every ``href`` and ``src`` the writer emits (a link,
an image, a value's link): the builder turns markdown-it's own check off.
An image path that is not relative (§2.3) never gets here: resolution has
replaced the image with its alt text and reported it.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from html import escape

from ..errors import OptionsError
from ..style.model import HeadingStyle, Style
from ..tree import (
    AttachmentPart,
    Blank,
    Block,
    Choice,
    Code,
    CodeBlock,
    CrossRef,
    DefinedTerm,
    DraftingNote,
    Emphasis,
    FailureMarker,
    HardBreak,
    Image,
    Inline,
    Link,
    List,
    ListItem,
    Paragraph,
    Quote,
    RenderTree,
    Rule,
    Section,
    SignatureParty,
    SoftBreak,
    Strong,
    Table,
    TermRef,
    Text,
    Value,
    has_visible_content,
    plain_text,
    tree_anchors,
)
from ..urls import displayed_url, is_link_target, is_safe_href

__all__ = ["HtmlWriter", "check_id_prefix", "check_scope", "is_safe_href", "stylesheet"]

_ID_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def check_id_prefix(prefix: object) -> str | None:
    """Why *prefix* cannot start an HTML id, or None if it can. It is a letter
    and then letters, digits, ``-`` and ``_``, so that every prefixed id is
    valid and a ``#`` link to it needs no escaping; ``""`` is no prefix."""
    if not isinstance(prefix, str):
        return f"the HTML id prefix must be a string, not {type(prefix).__name__}"
    if prefix == "" or _ID_PREFIX.fullmatch(prefix):
        return None
    return (f"the HTML id prefix {prefix!r} is not usable: it must start with an ASCII letter and hold only letters, "
            "digits, '-' and '_'")

def _text(value: str) -> str:
    return escape(value, quote=False)


def _attr(value: str) -> str:
    return escape(value, quote=True)


class HtmlWriter:
    """Writes a resolved tree as HTML."""

    extension = "html"
    media_type = "text/html"

    def __init__(self, style: Style, *, standalone: bool = True, generator: str = "", id_prefix: str = "") -> None:
        self.style = style
        self.standalone = standalone
        self.generator = generator
        #: Put before every ``id`` and every ``href="#..."`` to one of them, so
        #: that a page embedding the output keeps its own ids: the tree's
        #: anchors stay as they are. :func:`check_id_prefix` says what is usable.
        if (problem := check_id_prefix(id_prefix)) is not None:
            raise OptionsError(problem)
        self.id_prefix = id_prefix
        # The anchors of the tree being written (:meth:`write` sets them): a
        # ``#`` link the author wrote is prefixed only when it names one of
        # them. None outside :meth:`write`, when the methods for a block or
        # inlines are called on their own: every ``#`` link but a bare ``#``
        # is then taken to name one.
        self._anchors: frozenset[str] | None = None
        # Inside a link, references render as plain spans: HTML forbids
        # nested links.
        self._in_link = False

    def _id(self, anchor: str | None) -> str:
        """The ``id`` attribute of *anchor*, with the prefix; nothing for none."""
        return f' id="{_attr(self.id_prefix + anchor)}"' if anchor else ""

    def _href(self, anchor: str) -> str:
        """The escaped ``href`` value of a link to *anchor*, an anchor of the tree."""
        return _attr("#" + self.id_prefix + anchor)

    def _url(self, href: str) -> str:
        """The escaped ``href`` value of a URL: a ``#`` link to an anchor of the
        tree (an ``{{attach:}}``, or one the author wrote) gets the prefix; any
        other, a bare ``#`` and a ``#`` link to an id of the host page included,
        is left as it is."""
        fragment = href[1:] if href.startswith("#") else ""
        if fragment and (self._anchors is None or fragment in self._anchors):
            return self._href(fragment)
        return _attr(href)

    # -- document -------------------------------------------------------------

    def write(self, tree: RenderTree) -> str:
        self._anchors = tree_anchors(tree)
        try:
            return self._write(tree)
        finally:
            self._anchors = None

    def _write(self, tree: RenderTree) -> str:
        parts: list[str] = [f'<article class="ld-document{" ld-template" if tree.is_template else ""}">']
        parts.append(self.title_block(tree))
        if tree.contents:
            parts.append(self.contents(tree))
        if tree.preamble:
            parts.append('<div class="ld-preamble">')
            parts += [self.block(block) for block in tree.preamble]
            parts.append("</div>")
        parts += self.sections(tree.sections)
        if tree.attachments:
            parts.append(self.attachments(tree))
        if tree.signatures:
            parts.append(self.signatures(tree.signatures, tree.signature_labels, tree.colon))
        parts.append("</article>")
        body = "\n".join(part for part in parts if part)
        if not self.standalone:
            return body + "\n"
        generator = f'\n<meta name="generator" content="{_attr(self.generator)}">' if self.generator else ""
        return (
            "<!DOCTYPE html>\n"
            f'<html lang="{_attr(tree.language)}">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{_text(plain_text(tree.title))}</title>{generator}\n"
            f"<style>\n{stylesheet(self.style)}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n"
        )

    def title_block(self, tree: RenderTree) -> str:
        parts = ['<header class="ld-title-block">', f'<h1 class="ld-title">{self.inlines(tree.title)}</h1>']
        if tree.subtitle:
            parts.append(f'<p class="ld-subtitle">{self.inlines(tree.subtitle)}</p>')
        if tree.header:
            parts.append('<dl class="ld-header">')
            for label, value in tree.header:
                parts.append(f"<dt>{_text(label)}</dt><dd>{self.inlines(value)}</dd>")
            parts.append("</dl>")
        if tree.sides:
            parts.append('<div class="ld-parties">')
            for side in tree.sides:
                parts.append(f'<div class="ld-side">\n<p class="ld-side-label">{self.inlines(side.label)}</p>')
                for party in side.parties:
                    parts.append(f'<div class="ld-party">\n<p class="ld-party-name">{self.inlines(party.name)}</p>')
                    if party.details:
                        parts.append('<dl class="ld-party-details">')
                        for label, value in party.details:
                            parts.append(f"<dt>{_text(label)}</dt><dd>{self.inlines(value)}</dd>")
                        parts.append("</dl>")
                    parts.append("</div>")
                parts.append("</div>")
            parts.append("</div>")
        parts.append("</header>")
        return "\n".join(parts)

    def contents(self, tree: RenderTree) -> str:
        """The table of contents: one link per entry, indented by level."""
        label = tree.contents_label
        parts = [f'<nav class="ld-contents" aria-label="{_attr(label)}">' if label else '<nav class="ld-contents">']
        if label:
            parts.append(f'<h2 class="ld-contents-heading">{_text(label)}</h2>')
        parts.append('<ol class="ld-contents-list">')
        for entry in tree.contents:
            number = f'<span class="ld-number">{_text(entry.label)}</span> ' if entry.label else ""
            # unlinked() has taken every link out of the title.
            text = f'{number}<span class="ld-contents-text">{self.inlines(entry.title)}</span>'
            if entry.anchor:
                text = f'<a href="{self._href(entry.anchor)}">{text}</a>'
            if entry.condition_label:
                text += f' <span class="ld-condition">{_text(entry.condition_label)}</span>'
            parts.append(f'<li class="ld-contents-level-{entry.level}">{text}</li>')
        parts += ["</ol>", "</nav>"]
        return "\n".join(parts)

    def sections(self, sections: tuple[Section, ...]) -> list[str]:
        """Sections nested by level, each a ``<section>`` that closes before
        the next heading of the same or a higher level."""
        parts: list[str] = []
        open_levels: list[int] = []
        for section in sections:
            while open_levels and open_levels[-1] >= section.level:
                open_levels.pop()
                parts.append("</section>")
            open_levels.append(section.level)
            classes = f"ld-section ld-level-{section.level}" + (" ld-conditional" if section.condition_label else "")
            anchor = self._id(section.anchor)
            parts.append(f'<section class="{classes}"{anchor}>')
            parts.append(self.heading(section))
            if section.condition_label:
                parts.append(f'<p class="ld-condition">{_text(section.condition_label)}</p>')
            parts += [self.block(block) for block in section.blocks]
        parts += ["</section>"] * len(open_levels)
        return parts

    def heading(self, section: Section) -> str:
        number = f'<span class="ld-number">{_text(section.label)}</span> ' if section.label else ""
        content = f'{number}<span class="ld-heading-text">{self.inlines(section.title)}</span>'
        # Levels 1-5 (the resolver clamps them, §4.1) under the title's <h1>.
        tag = f"h{section.level + 1}"
        return f'<{tag} class="ld-heading">{content}</{tag}>'

    def attachments(self, tree: RenderTree) -> str:
        separator = self.style.attachments.separator
        anchor = self._id(tree.attachments_anchor)
        parts = [f'<section class="ld-attachments ld-separator-{separator}"{anchor}>',
                 f'<h2 class="ld-attachments-heading">{_text(tree.attachments_label)}</h2>' if tree.attachments_label
                 else ""]
        parts += [self.attachment(attachment) for attachment in tree.attachments]
        parts.append("</section>")
        return "\n".join(part for part in parts if part)

    def attachment(self, attachment: AttachmentPart) -> str:
        anchor = self._id(attachment.anchor)
        parts = [f'<section class="ld-attachment"{anchor}>', f'<h3 class="ld-attachment-title">{self.inlines(attachment.title)}</h3>']
        if attachment.condition_label:
            parts.append(f'<p class="ld-condition">{_text(attachment.condition_label)}</p>')
        if attachment.file:
            parts.append(f'<p class="ld-attachment-file">{_text(attachment.file)}</p>')
        parts.append("</section>")
        return "\n".join(parts)

    def signatures(self, signatures: tuple[SignatureParty, ...], labels: dict[str, str], colon: str) -> str:
        parts = [f'<section class="ld-signatures" aria-label="{_attr(labels["heading"])}">']
        for signature in signatures:
            parts.append('<div class="ld-signature">')
            parts.append(f'<p class="ld-signature-side">{self.inlines(signature.side)}</p>')
            parts.append(f'<p class="ld-signature-party">{self.inlines(signature.name)}</p>')
            for name, title in signature.signatories or (((), ()),):
                parts.append('<div class="ld-signatory"><div class="ld-signature-line"></div>')
                parts.append(f'<p>{_text(labels["name"] + colon)}{self.inlines(name)}</p>')
                parts.append(f'<p>{_text(labels["title"] + colon)}{self.inlines(title)}</p></div>')
            date, place = (_text((labels[key] + colon).rstrip(" ")) for key in ("date", "place"))
            parts.append(f"<p>{date}</p>\n<p>{place}</p>")
            parts.append("</div>")
        parts.append("</section>")
        return "\n".join(parts)

    # -- blocks ---------------------------------------------------------------

    def block(self, block: Block) -> str:
        match block:
            case Paragraph():
                return self.paragraph(block)
            case List():
                return self.list(block)
            case Quote(blocks=blocks):
                inner = "\n".join(self.block(child) for child in blocks)
                return f'<blockquote class="ld-quote">\n{inner}\n</blockquote>'
            case DraftingNote(blocks=blocks, label=label):
                inner = "\n".join(self.block(child) for child in blocks)
                heading = f'<p class="ld-drafting-label">{_text(label)}</p>\n' if label else ""
                return f'<aside class="ld-drafting-note">\n{heading}{inner}\n</aside>'
            case CodeBlock(text=text, info=info):
                language = f' class="language-{_attr(info.split()[0])}"' if info else ""
                return f'<pre class="ld-code"><code{language}>{_text(text)}</code></pre>'
            case Table():
                return self.table(block)
            case Rule():
                return '<hr class="ld-rule">'
        return ""

    def paragraph(self, block: Paragraph, prefix: str = "") -> str:
        classes = "ld-p" + (" ld-conditional" if block.condition_label else "")
        anchor = self._id(block.anchor)
        if block.condition_label:
            prefix += f'<span class="ld-condition">{_text(block.condition_label)}</span> '
        if block.label:
            prefix += f'<span class="ld-number">{_text(block.label)}</span> '
        return f'<p class="{classes}"{anchor}>{prefix}{self.inlines(block.inlines)}</p>'

    def list(self, block: List) -> str:
        if block.enumerated:
            parts = ['<ol class="ld-list ld-enumerated">']
        else:
            parts = ['<ol class="ld-list">' if block.ordered else '<ul class="ld-list">']
        parts += [self.item(item) for item in block.items]
        parts.append("</ol>" if block.enumerated or block.ordered else "</ul>")
        return "\n".join(parts)

    def item(self, item: ListItem) -> str:
        classes = ' class="ld-conditional"' if item.condition_label else ""
        anchor = self._id(item.anchor)
        label = f'<span class="ld-label">{_text(item.label)}</span>' if item.label else ""
        condition = f'<span class="ld-condition">{_text(item.condition_label)}</span> ' if item.condition_label else ""
        blocks = list(item.blocks)
        parts: list[str] = []
        if blocks and isinstance(blocks[0], Paragraph):
            # The condition leads the item's first line rather than a line of its own.
            parts.append(self.paragraph(blocks.pop(0), prefix=condition))
        elif condition:
            parts.append(f"<p>{condition}</p>")
        parts += [self.block(child) for child in blocks]
        body = "\n".join(parts)
        return f'<li{classes}{anchor}>{label}<div class="ld-item">{body}</div></li>'

    def table(self, table: Table) -> str:
        def align(index: int) -> str:
            value = table.align[index] if index < len(table.align) else ""
            return f' style="text-align: {value}"' if value else ""

        parts = ['<table class="ld-table">', "<thead><tr>"]
        parts += [f"<th{align(i)}>{self.inlines(cell)}</th>" for i, cell in enumerate(table.header)]
        parts.append("</tr></thead>")
        if table.rows:
            parts.append("<tbody>")
            for row in table.rows:
                parts.append("<tr>" + "".join(f"<td{align(i)}>{self.inlines(cell)}</td>" for i, cell in enumerate(row)) + "</tr>")
            parts.append("</tbody>")
        parts.append("</table>")
        return "\n".join(parts)

    # -- inlines --------------------------------------------------------------

    def inlines(self, inlines: tuple[Inline, ...]) -> str:
        return "".join(self.inline(inline) for inline in inlines)

    def inline(self, inline: Inline) -> str:
        match inline:
            case Text(text=text):
                return _text(text)
            case SoftBreak():
                return "\n"
            case HardBreak():
                return "<br>\n"
            case Emphasis(children=children):
                return f"<em>{self.inlines(children)}</em>"
            case Strong(children=children):
                return f"<strong>{self.inlines(children)}</strong>"
            case Code(text=text):
                return f"<code>{_text(text)}</code>"
            case Link(href=href, children=children, title=title):
                # A link with no text shows its URL (§8.7: it is still a hyperlink).
                shown = children if has_visible_content(children) else (Text(displayed_url(href)),)
                if not is_link_target(href):
                    return self.inlines(children)
                title_attr = f' title="{_attr(plain_text(title))}"' if title else ""
                outer, self._in_link = self._in_link, True
                content = self.inlines(shown)
                self._in_link = outer
                return f'<a href="{self._url(href)}"{title_attr}>{content}</a>'
            case Image(src=src, children=children, title=title):
                alt = plain_text(children)
                if not is_safe_href(src):
                    return _text(alt)
                title_attr = f' title="{_attr(plain_text(title))}"' if title else ""
                return f'<img src="{_attr(src)}" alt="{_attr(alt)}"{title_attr}>'
            case CrossRef(text=text, target=target):
                if not target or self._in_link:
                    return f'<span class="ld-ref">{_text(text)}</span>'
                return f'<a class="ld-ref" href="{self._href(target)}">{_text(text)}</a>'
            case TermRef(text=text, target=target, style=style):
                classes = f"ld-term ld-style-{style}"
                if target is None or self._in_link:
                    return f'<span class="{classes}">{_text(text)}</span>'
                return f'<a class="{classes}" href="{self._href(target)}">{_text(text)}</a>'
            case DefinedTerm(children=children, anchor=anchor, style=style, defining=defining):
                tag = "dfn" if defining else "span"
                anchor_attr = self._id(anchor)
                return f'<{tag} class="ld-defined ld-style-{style}"{anchor_attr}>{self.inlines(children)}</{tag}>'
            case Value(kind=kind, text=text, href=href):
                if href and is_safe_href(href) and not self._in_link:
                    return f'<a class="ld-value ld-{kind}" href="{self._url(href)}">{_text(text)}</a>'
                return f'<span class="ld-value ld-{kind}">{_text(text)}</span>'
            case Blank(id=identifier, text=text, prompt=prompt):
                title_attr = f' title="{_attr(prompt)}"' if prompt else ""
                shown = f' <span class="ld-prompt">({_text(prompt)})</span>' if prompt else ""
                return f'<span class="ld-blank" data-placeholder="{_attr(identifier)}"{title_attr}>{_text(text)}</span>{shown}'
            case Choice(question=question, options=options, separator=separator):
                inner = _text(separator).join(f'<span class="ld-option">{_text(option)}</span>' for option in options)
                return f'<span class="ld-choice" data-question="{_attr(question)}">[{inner}]</span>'
            case FailureMarker(text=text):
                return f'<mark class="ld-failure">{_text(text)}</mark>'
        return ""


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

#: One rule of the stylesheet: its selectors (a selector list, one entry each)
#: and its declarations, written as they appear between the braces.
_Rule = tuple[tuple[str, ...], str]

#: The document's own rule: the scope every embedded rule is placed under.
_ROOT = ".ld-document"

# The base rules, in the order a standalone page writes them. They are data,
# not CSS text, so that the embedded sheet places every selector under the
# document (see stylesheet()) without reading CSS.
_BASE_RULES: tuple[_Rule, ...] = (
    ((".ld-document",), "margin: 0 auto; padding: 2rem 1rem;"),
    ((".ld-title",), "font-size: 1.5em; text-align: center; margin: 0 0 0.25em;"),
    ((".ld-subtitle",), "text-align: center; margin: 0 0 1.5em;"),
    ((".ld-header", ".ld-party-details"),
     "display: grid; grid-template-columns: max-content 1fr; gap: 0 0.75em; margin: 0.5em 0;"),
    ((".ld-header dt", ".ld-party-details dt"), "font-style: italic;"),
    ((".ld-header dd", ".ld-party-details dd"), "margin: 0;"),
    ((".ld-parties",), "display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 1rem 2rem; "
                       "margin: 1.5em 0;"),
    ((".ld-side-label",), "font-weight: 700; margin: 0 0 0.25em;"),
    ((".ld-party-name",), "margin: 0;"),
    ((".ld-party + .ld-party",), "margin-top: 1em;"),
    ((".ld-heading",), "margin: 1.5em 0 0.5em;"),
    ((".ld-heading .ld-number",), "display: inline-block; min-width: 2.5em;"),
    ((".ld-p",), "margin: 0.6em 0;"),
    ((".ld-p > .ld-number",), "display: inline-block; min-width: 2.5em;"),
    ((".ld-list",), "margin: 0.5em 0; padding-left: 1.75em;"),
    ((".ld-enumerated",), "list-style: none; padding-left: 0;"),
    ((".ld-enumerated > li",), "display: flex; gap: 0.5em; margin: 0.3em 0;"),
    ((".ld-enumerated > li > .ld-label",), "flex: 0 0 auto; min-width: 2.5em;"),
    ((".ld-item",), "flex: 1 1 auto; min-width: 0;"),
    ((".ld-item > p:first-child",), "margin-top: 0;"),
    ((".ld-item > p:last-child",), "margin-bottom: 0;"),
    ((".ld-quote",), "margin: 0.75em 0; padding-left: 1em; border-left: 3px solid var(--ld-rule);"),
    ((".ld-table",), "border-collapse: collapse; margin: 1em 0; width: 100%;"),
    ((".ld-table th", ".ld-table td"), "border: 1px solid var(--ld-rule); padding: 0.35em 0.6em; vertical-align: top;"),
    ((".ld-code",), "overflow-x: auto; padding: 0.75em; background: var(--ld-subtle);"),
    ((".ld-rule",), "border: 0; border-top: 1px solid var(--ld-rule); margin: 1.5em 0;"),
    (("a",), "color: var(--ld-link);"),
    ((".ld-ref", ".ld-term", "a.ld-value"), "text-decoration: none;"),
    ((".ld-ref:hover", ".ld-term:hover", "a.ld-value:hover"), "text-decoration: underline;"),
    (("dfn",), "font-style: normal;"),
    ((".ld-style-bold",), "font-weight: 700;"),
    ((".ld-style-italic",), "font-style: italic;"),
    ((".ld-style-underline",), "text-decoration: underline;"),
    ((".ld-style-small-caps",), "font-variant: small-caps;"),
    ((".ld-failure",), "background: #fde2e1; color: #8a1c14; padding: 0 0.15em; border-radius: 2px;"),
    ((".ld-blank",), "white-space: nowrap;"),
    ((".ld-prompt",), "font-style: italic; color: var(--ld-muted);"),
    ((".ld-drafting-note",), "margin: 1em 0; padding: 0.5em 1em; border-left: 4px solid #d9a400; background: #fff8e1;"),
    ((".ld-drafting-label",), "font-weight: 700; font-size: 0.9em; margin: 0.25em 0;"),
    ((".ld-condition",), "display: inline-block; font-size: 0.8em; font-family: system-ui, sans-serif; color: #5b3fa0; "
                         "background: #efe9fb; border-radius: 3px; padding: 0 0.4em;"),
    (("p.ld-condition",), "margin: 0 0 0.5em;"),
    ((".ld-conditional",), "border-left: 2px dashed #b9a6e8; padding-left: 0.6em;"),
    ((".ld-choice",), "background: #efe9fb; border-radius: 3px; padding: 0 0.2em;"),
    ((".ld-attachments",), "margin-top: 3em;"),
    ((".ld-attachment-file",), "color: var(--ld-muted);"),
    ((".ld-signatures",), "display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 2rem; "
                          "margin-top: 3em;"),
    ((".ld-signature p",), "margin: 0.2em 0;"),
    ((".ld-signature-side",), "font-weight: 700;"),
    ((".ld-signatory",), "margin-top: 2.5em;"),
    ((".ld-signature-line",), "border-top: 1px solid currentColor; width: 80%; margin-bottom: 0.3em;"),
)

#: The rules inside ``@media print``.
_PRINT_RULES: tuple[_Rule, ...] = (
    ((".ld-document",), "max-width: none; padding: 0;"),
    ((".ld-attachments.ld-separator-page-break",), "break-before: page;"),
    ((".ld-separator-page-break .ld-attachment + .ld-attachment",), "break-before: page;"),
    ((".ld-attachments-heading",), "break-after: avoid;"),
    ((".ld-signatures",), "break-inside: avoid;"),
    (("a",), "color: inherit;"),
)

#: The print rules of the embedded sheet: the document drops its width limit,
#: and its padding stays the host's, as on screen.
_EMBEDDED_PRINT_RULES: tuple[_Rule, ...] = (
    ((_ROOT,), "max-width: none;"),
    *(rule for rule in _PRINT_RULES if rule[0] != (_ROOT,)),
)

_CONTENTS_RULES: tuple[_Rule, ...] = (
    ((".ld-contents",), "margin: 2em 0;"),
    ((".ld-contents-list",), "list-style: none; padding-left: 0;"),
    ((".ld-contents-list li",), "margin: 0.2em 0;"),
    ((".ld-contents-list a",), "color: inherit; text-decoration: none;"),
    ((".ld-contents-level-2",), "padding-left: 1.5em;"),
    ((".ld-contents-level-3",), "padding-left: 3em;"),
    ((".ld-contents-level-4",), "padding-left: 4.5em;"),
    ((".ld-contents-level-5",), "padding-left: 6em;"),
)

# A scope (stylesheet(scope=)): compounds of an optional type selector, then
# classes and ids, joined by a descendant space or " > ". Nothing else, so
# that the scope cannot leave its place in the selector or the sheet.
_SCOPE_COMPOUND = r"(?:[A-Za-z][A-Za-z0-9-]*(?:[.#][A-Za-z_][A-Za-z0-9_-]*)*|(?:[.#][A-Za-z_][A-Za-z0-9_-]*)+)"
_SCOPE = re.compile(rf"{_SCOPE_COMPOUND}(?: (?:> )?{_SCOPE_COMPOUND})*")


def check_scope(scope: object) -> str | None:
    """Why *scope* cannot be the scope of an embedded stylesheet, or None if
    it can. It is one selector of type, class and id selectors
    (``.preview``, ``#doc-42``, ``main .pane > .preview``), joined by a space
    or `` > ``; ``""`` is no scope."""
    if not isinstance(scope, str):
        return f"the stylesheet scope must be a string, not {type(scope).__name__}"
    if scope == "" or _SCOPE.fullmatch(scope):
        return None
    return (f"the stylesheet scope {scope!r} is not usable: it must be one selector of type, class and id selectors "
            "(such as '.preview' or 'main #doc-42'), joined by a space or ' > '")


def _rule(rule: _Rule, place: Callable[[str], str]) -> str:
    selectors, declarations = rule
    return f"{', '.join(place(selector) for selector in selectors)} {{ {declarations} }}"


def _print_block(place: Callable[[str], str], rules: tuple[_Rule, ...] = _PRINT_RULES) -> str:
    return "@media print {\n" + "".join(f"  {_rule(rule, place)}\n" for rule in rules) + "}"


def _heading_levels(style: Style) -> list[tuple[str, HeadingStyle]]:
    """The heading levels the sheet writes, in numeric order, as the digits of
    their class name (``ld-level-1``).

    The loader reads every level as a number. A key of a Style built by hand
    becomes part of a selector, so it is written only when it is a number or
    a string of digits (``"1"`` writes ``.ld-level-1``, as it always did); any
    other key (``True``, ``1.5``, ``"1, body"``) is left out. Those never wrote
    a rule that matched a heading."""
    levels: list[tuple[int, str, HeadingStyle]] = []
    for key, heading in style.headings.items():
        if isinstance(key, int) and not isinstance(key, bool):
            levels.append((int(key), str(int(key)), heading))
        elif isinstance(key, str) and re.fullmatch(r"[0-9]+", key, re.ASCII):
            levels.append((int(key), str.__str__(key), heading))
    levels.sort(key=lambda level: level[0])
    return [(digits, heading) for _, digits, heading in levels]


def stylesheet(style: Style, *, standalone: bool = True, scope: str = "") -> str:
    """The CSS for *style*: base rules plus everything the style sets.

    With ``standalone=True`` (the default) it is the sheet of a complete page,
    the one :class:`HtmlWriter` writes into its ``<style>``: it sets the page's
    ``body``, ``:root`` variables and ``@page`` too.

    With ``standalone=False`` it is the sheet for the ``<article>`` alone
    (``HtmlWriter(standalone=False)``), placed in a host page: every rule is
    under ``.ld-document``, the variables and the typography are set on it,
    and there is no ``body``, ``:root``, ``html`` or ``@page`` rule, so it
    styles nothing of the page outside the document. Every rule gains the
    same specificity, so its rules override each other as in the standalone
    sheet; the one difference is that the document's own rule comes before
    the print rules, so that in print its width limit is lifted. The page's
    margin and the document's padding (a page gutter, on screen and in
    print) are left out.

    ``html.extra_css`` is appended as written in both modes: it is the style
    author's own CSS, and is not scoped. In the embedded sheet a bare selector
    in it reaches the host page, and its overrides of the base rules lose to
    them on specificity (``.ld-title { … }`` against ``.ld-document
    .ld-title``), and ``:root`` variables in it do not reach the document,
    whose own rule sets them; to apply there, its rules must be written under
    ``.ld-document``. A host should give the embedded sheet a ``<style>``
    element of its own, so that a value that does not parse (a trusted
    style's ``foo(``) cannot take the host's rules after it with it.

    *scope*, with ``standalone=False`` only, places the document under one
    more selector, ``:where(scope) .ld-document`` (specificity unchanged), so
    that documents of different styles can share a page. :func:`check_scope`
    says what is usable; anything else is an :class:`~..errors.OptionsError`.

    The guarantees are the API; the exact text of either sheet is not.
    """
    if (problem := check_scope(scope)) is not None:
        raise OptionsError(problem)
    if not isinstance(standalone, bool):
        raise OptionsError(f"standalone must be True or False, not {standalone!r}")
    if scope and standalone:
        raise OptionsError("a stylesheet scope is for the embedded sheet only (standalone=False)")
    typography = style.typography
    variables = ("--ld-rule: #c8c8c8; --ld-subtle: #f5f5f5; --ld-muted: #666; "
                 f"--ld-link: {_css(typography.link_color)};")
    text = (f"font-family: {_css(typography.font_family)}; font-size: {_css(typography.font_size)}; "
            f"line-height: {_css(typography.line_height)}; color: {_css(typography.color)}; background: #fff;")
    max_width = f"max-width: {_css(style.html.max_width)};"

    generated: list[_Rule] = []
    if typography.justify:
        generated.append(((".ld-p", ".ld-item > p"), "text-align: justify; hyphens: auto;"))
    for level, heading in _heading_levels(style):
        rules = [f"font-size: {_css(heading.size)}", f"font-weight: {_css(heading.weight)}",
                 f"text-align: {_css(str(heading.align))}", f"font-style: {'italic' if heading.italic else 'normal'}"]
        if heading.transform == "uppercase":
            rules.append("text-transform: uppercase")
        elif heading.transform == "small-caps":
            rules.append("font-variant: small-caps")
        generated.append(((f".ld-level-{level} > .ld-heading",), f"{'; '.join(rules)};"))
    if style.contents.enabled:
        generated += _CONTENTS_RULES
    if style.attachments.separator == "rule":
        generated.append(((".ld-attachment",), "border-top: 1px solid var(--ld-rule); padding-top: 1em;"))

    if standalone:
        def place(selector: str) -> str:
            return selector
        parts = [_rule(rule, place) for rule in _BASE_RULES]
        parts.append(_print_block(place))
        parts.append(f":root {{ {variables} }}")
        parts.append(f"body {{ margin: 0; {text} }}")
        parts.append(f".ld-document {{ {max_width} }}")
        parts += [_rule(rule, place) for rule in generated]
        parts.append(f"@page {{ size: {_css(style.page.size)}; margin: {_css(style.page.margin)}; }}")
    else:
        # str.__str__: the value check_scope matched, whatever a str subclass formats as.
        root = f":where({str.__str__(scope)}) {_ROOT}" if scope else _ROOT

        def place(selector: str) -> str:
            # The document's own rule is the root; every other selector is placed under it.
            return root if selector == _ROOT else f"{root} {selector}"
        # One rule for the document itself, first, so that the print rule for
        # it (in the block below) comes after it and takes effect.
        parts = [f"{root} {{ {variables} {text} margin: 0 auto; {max_width} }}"]
        parts += [_rule(rule, place) for rule in _BASE_RULES if rule[0] != (_ROOT,)]
        parts.append(_print_block(place, _EMBEDDED_PRINT_RULES))
        parts += [_rule(rule, place) for rule in generated]
    css = "\n".join(parts) + "\n"
    if style.html.extra_css:
        # The loader refuses ``</style``; any other ``</`` is made ``<\/`` (the same
        # inside a CSS string), so that a Style built by hand cannot end the element.
        css += style.html.extra_css.rstrip().replace("</", "<\\/") + "\n"
    return css


def _css(value: str) -> str:
    """A style value placed in CSS, stripped of what could end the declaration,
    open a comment, or end the ``<style>`` element: ``;{}<>\\@``, control
    characters and line breaks, and the comment delimiters ``/*`` and ``*/``
    (removed until none is left, since removing one can join its neighbours
    into another). A single ``/`` stays, as ``calc(100% / 3)`` needs it.

    The style loader already refuses such values (:func:`~..style.model.css_problem`);
    this is the last line for :func:`stylesheet` given a :class:`Style` built by
    hand."""
    value = re.sub(r"[;{}<>\\@\x00-\x1f\x7f-\x9f\u2028\u2029]", "", value)
    while "/*" in value or "*/" in value:
        value = value.replace("/*", "").replace("*/", "")
    return value
