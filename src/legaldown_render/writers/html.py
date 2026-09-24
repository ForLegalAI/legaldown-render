"""The HTML writer (§13.6).

It writes one self-contained HTML5 file: semantic markup (``<section>``,
``<ol>``, ``<dfn>``, ``<a href>``) and a stylesheet generated from the style
template's presentation settings. Every label a reader sees — "4.2", "(b)" —
is real text in the markup rather than a CSS counter, so the numbers are the
ones resolution computed and survive copying and printing.

Safety (docs/architecture.md): every text node and attribute is escaped, raw
HTML from the source never reaches this writer (§8.7), and a link whose URL
uses a scheme outside :data:`SAFE_SCHEMES` renders as plain text.
"""
from __future__ import annotations

import re
from html import escape

from ..style.model import Style
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
    plain_text,
)

SAFE_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
# What the WHATWG URL parser ignores before it reads a scheme: tabs and
# newlines anywhere, and C0 controls and spaces at either end. Checking the
# URL as a browser will read it means "java\tscript:" cannot slip through.
_URL_IGNORED_RE = re.compile(r"[\t\n\r]")
_URL_TRIMMED = "".join(chr(code) for code in range(0x21))


def _text(value: str) -> str:
    return escape(value, quote=False)


def _attr(value: str) -> str:
    return escape(value, quote=True)


def is_safe_href(href: str) -> bool:
    """True for relative URLs, fragments, and the schemes in SAFE_SCHEMES,
    judged as a browser parses the URL."""
    scheme = _SCHEME_RE.match(_URL_IGNORED_RE.sub("", href).strip(_URL_TRIMMED))
    return scheme is None or scheme.group(1).lower() in SAFE_SCHEMES


class HtmlWriter:
    """Writes a resolved tree as HTML."""

    extension = "html"
    media_type = "text/html"

    def __init__(self, style: Style, *, standalone: bool = True, generator: str = "") -> None:
        self.style = style
        self.standalone = standalone
        self.generator = generator
        # Inside a link, references render as plain spans: HTML forbids
        # nested links.
        self._in_link = False

    # -- document -------------------------------------------------------------

    def write(self, tree: RenderTree) -> str:
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
                text = f'<a href="#{_attr(entry.anchor)}">{text}</a>'
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
            anchor = f' id="{_attr(section.anchor)}"' if section.anchor else ""
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
        anchor = f' id="{_attr(tree.attachments_anchor)}"' if tree.attachments_anchor else ""
        parts = [f'<section class="ld-attachments ld-separator-{separator}"{anchor}>',
                 f'<h2 class="ld-attachments-heading">{_text(tree.attachments_label)}</h2>' if tree.attachments_label
                 else ""]
        parts += [self.attachment(attachment) for attachment in tree.attachments]
        parts.append("</section>")
        return "\n".join(part for part in parts if part)

    def attachment(self, attachment: AttachmentPart) -> str:
        anchor = f' id="{_attr(attachment.anchor)}"' if attachment.anchor else ""
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
        anchor = f' id="{_attr(block.anchor)}"' if block.anchor else ""
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
        anchor = f' id="{_attr(item.anchor)}"' if item.anchor else ""
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
                if not is_safe_href(href):
                    return self.inlines(children)
                title_attr = f' title="{_attr(plain_text(title))}"' if title else ""
                outer, self._in_link = self._in_link, True
                content = self.inlines(children)
                self._in_link = outer
                return f'<a href="{_attr(href)}"{title_attr}>{content}</a>'
            case Image(src=src, children=children, title=title):
                alt = plain_text(children)
                if not is_safe_href(src):
                    return _text(alt)
                title_attr = f' title="{_attr(plain_text(title))}"' if title else ""
                return f'<img src="{_attr(src)}" alt="{_attr(alt)}"{title_attr}>'
            case CrossRef(text=text, target=target):
                if not target or self._in_link:
                    return f'<span class="ld-ref">{_text(text)}</span>'
                return f'<a class="ld-ref" href="#{_attr(target)}">{_text(text)}</a>'
            case TermRef(text=text, target=target, style=style):
                classes = f"ld-term ld-style-{style}"
                if target is None or self._in_link:
                    return f'<span class="{classes}">{_text(text)}</span>'
                return f'<a class="{classes}" href="#{_attr(target)}">{_text(text)}</a>'
            case DefinedTerm(children=children, anchor=anchor, style=style, defining=defining):
                tag = "dfn" if defining else "span"
                anchor_attr = f' id="{_attr(anchor)}"' if anchor else ""
                return f'<{tag} class="ld-defined ld-style-{style}"{anchor_attr}>{self.inlines(children)}</{tag}>'
            case Value(kind=kind, text=text, href=href):
                if href and is_safe_href(href) and not self._in_link:
                    return f'<a class="ld-value ld-{kind}" href="{_attr(href)}">{_text(text)}</a>'
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

_BASE_CSS = """\
.ld-document { margin: 0 auto; padding: 2rem 1rem; }
.ld-title { font-size: 1.5em; text-align: center; margin: 0 0 0.25em; }
.ld-subtitle { text-align: center; margin: 0 0 1.5em; }
.ld-header, .ld-party-details { display: grid; grid-template-columns: max-content 1fr; gap: 0 0.75em; margin: 0.5em 0; }
.ld-header dt, .ld-party-details dt { font-style: italic; }
.ld-header dd, .ld-party-details dd { margin: 0; }
.ld-parties { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 1rem 2rem; margin: 1.5em 0; }
.ld-side-label { font-weight: 700; margin: 0 0 0.25em; }
.ld-party-name { margin: 0; }
.ld-party + .ld-party { margin-top: 1em; }
.ld-heading { margin: 1.5em 0 0.5em; }
.ld-heading .ld-number { display: inline-block; min-width: 2.5em; }
.ld-p { margin: 0.6em 0; }
.ld-p > .ld-number { display: inline-block; min-width: 2.5em; }
.ld-list { margin: 0.5em 0; padding-left: 1.75em; }
.ld-enumerated { list-style: none; padding-left: 0; }
.ld-enumerated > li { display: flex; gap: 0.5em; margin: 0.3em 0; }
.ld-enumerated > li > .ld-label { flex: 0 0 auto; min-width: 2.5em; }
.ld-item { flex: 1 1 auto; min-width: 0; }
.ld-item > p:first-child { margin-top: 0; }
.ld-item > p:last-child { margin-bottom: 0; }
.ld-quote { margin: 0.75em 0; padding-left: 1em; border-left: 3px solid var(--ld-rule); }
.ld-table { border-collapse: collapse; margin: 1em 0; width: 100%; }
.ld-table th, .ld-table td { border: 1px solid var(--ld-rule); padding: 0.35em 0.6em; vertical-align: top; }
.ld-code { overflow-x: auto; padding: 0.75em; background: var(--ld-subtle); }
.ld-rule { border: 0; border-top: 1px solid var(--ld-rule); margin: 1.5em 0; }
a { color: var(--ld-link); }
.ld-ref, .ld-term, a.ld-value { text-decoration: none; }
.ld-ref:hover, .ld-term:hover, a.ld-value:hover { text-decoration: underline; }
dfn { font-style: normal; }
.ld-style-bold { font-weight: 700; }
.ld-style-italic { font-style: italic; }
.ld-style-underline { text-decoration: underline; }
.ld-style-small-caps { font-variant: small-caps; }
.ld-failure { background: #fde2e1; color: #8a1c14; padding: 0 0.15em; border-radius: 2px; }
.ld-blank { white-space: nowrap; }
.ld-prompt { font-style: italic; color: var(--ld-muted); }
.ld-drafting-note { margin: 1em 0; padding: 0.5em 1em; border-left: 4px solid #d9a400; background: #fff8e1; }
.ld-drafting-label { font-weight: 700; font-size: 0.9em; margin: 0.25em 0; }
.ld-condition { display: inline-block; font-size: 0.8em; font-family: system-ui, sans-serif; color: #5b3fa0; background: #efe9fb; border-radius: 3px; padding: 0 0.4em; }
p.ld-condition { margin: 0 0 0.5em; }
.ld-conditional { border-left: 2px dashed #b9a6e8; padding-left: 0.6em; }
.ld-choice { background: #efe9fb; border-radius: 3px; padding: 0 0.2em; }
.ld-attachments { margin-top: 3em; }
.ld-attachment-file { color: var(--ld-muted); }
.ld-signatures { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 2rem; margin-top: 3em; }
.ld-signature p { margin: 0.2em 0; }
.ld-signature-side { font-weight: 700; }
.ld-signatory { margin-top: 2.5em; }
.ld-signature-line { border-top: 1px solid currentColor; width: 80%; margin-bottom: 0.3em; }
@media print {
  .ld-document { max-width: none; padding: 0; }
  .ld-separator-page-break .ld-attachment { break-before: page; }
  .ld-signatures { break-inside: avoid; }
  a { color: inherit; }
}
"""


_CONTENTS_CSS = """\
.ld-contents { margin: 2em 0; }
.ld-contents-list { list-style: none; padding-left: 0; }
.ld-contents-list li { margin: 0.2em 0; }
.ld-contents-list a { color: inherit; text-decoration: none; }
.ld-contents-level-2 { padding-left: 1.5em; }
.ld-contents-level-3 { padding-left: 3em; }
.ld-contents-level-4 { padding-left: 4.5em; }
.ld-contents-level-5 { padding-left: 6em; }"""


def stylesheet(style: Style) -> str:
    """The CSS for *style*: base rules plus everything the style sets."""
    typography = style.typography
    lines = [
        ":root { --ld-rule: #c8c8c8; --ld-subtle: #f5f5f5; --ld-muted: #666; "
        f"--ld-link: {_css(typography.link_color)}; }}",
        f"body {{ margin: 0; font-family: {_css(typography.font_family)}; font-size: {_css(typography.font_size)}; "
        f"line-height: {_css(typography.line_height)}; color: {_css(typography.color)}; background: #fff; }}",
        f".ld-document {{ max-width: {_css(style.html.max_width)}; }}",
    ]
    if typography.justify:
        lines.append(".ld-p, .ld-item > p { text-align: justify; hyphens: auto; }")
    for level, heading in sorted(style.headings.items()):
        rules = [f"font-size: {_css(heading.size)}", f"font-weight: {_css(heading.weight)}",
                 f"text-align: {heading.align}", f"font-style: {'italic' if heading.italic else 'normal'}"]
        if heading.transform == "uppercase":
            rules.append("text-transform: uppercase")
        elif heading.transform == "small-caps":
            rules.append("font-variant: small-caps")
        lines.append(f".ld-level-{level} > .ld-heading {{ {'; '.join(rules)}; }}")
    if style.contents.enabled:
        lines.append(_CONTENTS_CSS)
    if style.attachments.separator == "rule":
        lines.append(".ld-attachment { border-top: 1px solid var(--ld-rule); padding-top: 1em; }")
    lines.append(f"@page {{ size: {_css(style.page.size)}; margin: {_css(style.page.margin)}; }}")
    css = _BASE_CSS + "\n".join(lines) + "\n"
    if style.html.extra_css:
        css += style.html.extra_css.rstrip() + "\n"
    return css


def _css(value: str) -> str:
    """A style value placed in CSS, stripped of characters that could end
    the declaration or the stylesheet."""
    return re.sub(r"[;{}<>]", "", value)
