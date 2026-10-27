"""The plain-text writer (§13.6, OPTIONAL).

It prints numbers, designations, and display text with no layout noise, so it
doubles as the test oracle for resolution (docs/architecture.md, Testing).
Line breaks inside a paragraph are joined, so output does not depend on how
the source was wrapped.

Structure survives the plain layout: one quote stays one quote (every line of
it, blank ones included, carries the ``>`` marker), the blocks of a list item
are set apart by blank lines and indented under the item's label, and a table
is a pipe table whose cells escape ``|`` and whose separator row carries the
column alignment.
"""
from __future__ import annotations

from ..tree import (
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
    Paragraph,
    Quote,
    RenderTree,
    Rule,
    SignatureParty,
    SoftBreak,
    Strong,
    Table,
    TermRef,
    Text,
    Value,
)
from ..urls import is_link_target

_INDENT = "    "
_ADDRESS_SCHEMES = ("mailto:", "tel:")


def _link_text(href: str, text: str) -> str:
    """A link as plain text: its text, followed by ``<url>`` unless the URL is
    no more than the text says. The same links are links as in HTML
    (:func:`~legaldown_render.urls.is_link_target`); one that is not shows its
    text alone, and one with no text shows its URL."""
    if not is_link_target(href):
        return text
    url = href.strip()
    if not text.strip():
        return url
    address = url
    for prefix in _ADDRESS_SCHEMES:
        if url.lower().startswith(prefix):
            address = url[len(prefix):]
    # An internal link has no URL worth printing; an autolink's text is its URL.
    return text if url.startswith("#") or text in (url, address) else f"{text} <{url}>"


def _labelled(label: str, colon: str, value: str) -> str:
    """A ``label: value`` row. A style that blanks the label (§13.5) leaves the
    value alone rather than a bare ``: value``."""
    return f"{label}{colon}{value}" if label else value


def inline_text(inlines: tuple[Inline, ...]) -> str:
    parts: list[str] = []
    for inline in inlines:
        match inline:
            case Text(text=text) | Code(text=text) | CrossRef(text=text) | TermRef(text=text) \
                    | Value(text=text) | FailureMarker(text=text):
                parts.append(text)
            case Blank(text=text, prompt=prompt):
                parts.append(f"{text} ({prompt})" if prompt else text)
            case SoftBreak():
                parts.append(" ")
            case HardBreak():
                parts.append("\n")
            case Emphasis(children=children) | Strong(children=children) | DefinedTerm(children=children):
                parts.append(inline_text(children))
            case Link(href=href, children=children):
                parts.append(_link_text(href, inline_text(children)))
            case Image(children=children):
                parts.append(inline_text(children))
            case Choice(options=options, separator=separator):
                parts.append("[" + separator.join(options) + "]")
    return "".join(parts)


class TextWriter:
    """Writes a resolved tree as plain text.

    *attachment_separator* is the style's ``attachments.separator`` (§13.8):
    ``none`` prints nothing between attachments; ``rule`` and ``page-break``
    both print a ``---`` line before each attachment, as plain text has no
    pages to break. The default is the default style's value.
    """

    extension = "txt"
    media_type = "text/plain"

    def __init__(self, attachment_separator: str = "page-break") -> None:
        self.attachment_separator = attachment_separator

    def write(self, tree: RenderTree) -> str:
        chunks: list[str] = []
        title = inline_text(tree.title).strip()
        if title:
            chunks.append(title)
        subtitle = inline_text(tree.subtitle).strip()
        if subtitle:
            chunks.append(subtitle)
        if tree.header:
            chunks.append("\n".join(_labelled(label, tree.colon, inline_text(value)) for label, value in tree.header))
        for side in tree.sides:
            lines = [inline_text(side.label)]
            for party in side.parties:
                lines.append(_INDENT + inline_text(party.name))
                lines += [_INDENT + _labelled(label, tree.colon, inline_text(value)) for label, value in party.details]
            chunks.append("\n".join(lines))
        if tree.contents:
            lines = [tree.contents_label] if tree.contents_label else []
            for entry in tree.contents:
                line = _INDENT * (entry.level - 1) + " ".join(filter(None, (entry.label, inline_text(entry.title))))
                if entry.condition_label:
                    line += f" [{entry.condition_label}]"
                lines.append(line)
            chunks.append("\n".join(lines))
        chunks += [self.block(block) for block in tree.preamble]
        for section in tree.sections:
            heading = " ".join(filter(None, (section.label, inline_text(section.title))))
            chunks.append(heading)
            if section.condition_label:
                chunks.append(f"[{section.condition_label}]")
            chunks += [self.block(block) for block in section.blocks]
        if tree.attachments:
            chunks.append(tree.attachments_label)
            for attachment in tree.attachments:
                if self.attachment_separator != "none":
                    chunks.append("---")
                lines = [inline_text(attachment.title)]
                if attachment.condition_label:
                    lines.append(f"[{attachment.condition_label}]")
                if attachment.file:
                    lines.append(attachment.file)
                chunks.append("\n".join(lines))
        if tree.signatures:
            chunks.append("---")
            chunks += [self.signature(signature, tree.signature_labels, tree.colon) for signature in tree.signatures]
        return "\n\n".join(chunk for chunk in chunks if chunk) + "\n"

    def block(self, block: Block) -> str:
        match block:
            case Paragraph(inlines=inlines, label=label, condition_label=condition):
                text = inline_text(inlines)
                if label:
                    text = f"{label} {text}"
                return f"[{condition}] {text}" if condition else text
            case List():
                return "\n".join(self.list(block, ""))
            case Quote(blocks=blocks):
                return _prefix("\n\n".join(self.block(child) for child in blocks), "> ")
            case DraftingNote(blocks=blocks, label=label):
                body = "\n\n".join(self.block(child) for child in blocks)
                return _prefix(f"[{label}]\n{body}" if label else body, "> ")
            case CodeBlock(text=text):
                return _prefix(text.rstrip("\n"), _INDENT)
            case Table():
                return self.table(block)
            case Rule():
                return "---"
        return ""

    def list(self, block: List, indent: str) -> list[str]:
        """The lines of *block*, each item's label at *indent*. Everything
        else in an item, a nested list included, lines up with its text, which
        starts one column after the item's own label (or bullet): a condition
        label (§15.8) does not move it. Blocks of an item are set apart by a
        blank line, except that a nested list stays tight under the paragraph
        or list it follows."""
        lines: list[str] = []
        for item in block.items:
            label = item.label if item.label else "-"
            marker = f"{label} [{item.condition_label}]" if item.condition_label else label
            content = indent + " " * (len(label) + 1)
            previous: Block | None = None
            for child in item.blocks:
                if previous is not None and not (isinstance(child, List) and isinstance(previous, Paragraph | List)):
                    lines.append("")
                if isinstance(child, List):
                    if previous is None:
                        # The item opens with a nested list: its own label
                        # still comes first, on a line of its own.
                        lines.append(indent + marker)
                    lines += self.list(child, content)
                elif previous is None:
                    lines.append(_prefix(f"{marker} {self.block(child)}", indent, hanging=" " * (len(label) + 1)))
                else:
                    lines.append(_prefix(self.block(child), content))
                previous = child
            if previous is None:
                lines.append(indent + marker)
        return lines

    def table(self, table: Table) -> str:
        """A pipe table, unpadded. ``|`` in a cell is escaped so a cell stays
        a cell; the separator row marks each column's alignment."""
        def cell(inlines: tuple[Inline, ...]) -> str:
            return inline_text(inlines).replace("|", "\\|")

        rule = {"left": ":---", "center": ":---:", "right": "---:"}
        align = table.align + ("",) * (len(table.header) - len(table.align))
        lines = ["| " + " | ".join(cell(c) for c in table.header) + " |",
                 "|" + "|".join(rule.get(a, "---") for a in align) + "|"]
        lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in table.rows]
        return "\n".join(lines)

    def signature(self, signature: SignatureParty, labels: dict[str, str], colon: str) -> str:
        lines = [inline_text(signature.side), inline_text(signature.name)]
        # With no declared representative, one blank signing line.
        for name, title in signature.signatories or (((), ()),):
            lines += [
                "",
                "_" * 30,
                f"{labels['name']}{colon}{inline_text(name)}".rstrip(),
                f"{labels['title']}{colon}{inline_text(title)}".rstrip(),
            ]
        lines += [f"{labels['date']}{colon}".rstrip(" "), f"{labels['place']}{colon}".rstrip(" ")]
        return "\n".join(lines)


def _prefix(text: str, prefix: str, *, hanging: str | None = None) -> str:
    """Every line of *text* prefixed; with *hanging*, lines after the first
    get *prefix* plus *hanging* instead. A blank line gets the prefix with its
    trailing space trimmed: ``>`` keeps a quote one quote, an indent leaves the
    line empty."""
    lines = text.split("\n")
    rest = prefix + hanging if hanging is not None else prefix
    return "\n".join([prefix + lines[0]] + [(rest + line) if line else rest.rstrip() for line in lines[1:]])
