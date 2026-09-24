"""The plain-text writer (§13.6, OPTIONAL).

It prints numbers, designations, and display text with no layout noise, so it
doubles as the test oracle for resolution (docs/architecture.md, Testing).
Line breaks inside a paragraph are joined, so output does not depend on how
the source was wrapped.
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

_INDENT = "    "


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
                text = inline_text(children)
                parts.append(text if href.startswith("#") or href == text else f"{text} <{href}>")
            case Image(children=children):
                parts.append(inline_text(children))
            case Choice(options=options, separator=separator):
                parts.append("[" + separator.join(options) + "]")
    return "".join(parts)


class TextWriter:
    """Writes a resolved tree as plain text."""

    extension = "txt"
    media_type = "text/plain"

    def write(self, tree: RenderTree) -> str:
        chunks: list[str] = []
        title = inline_text(tree.title).strip()
        if title:
            chunks.append(title)
        subtitle = inline_text(tree.subtitle).strip()
        if subtitle:
            chunks.append(subtitle)
        if tree.header:
            chunks.append("\n".join(f"{label}{tree.colon}{inline_text(value)}" for label, value in tree.header))
        for side in tree.sides:
            lines = [inline_text(side.label)]
            for party in side.parties:
                lines.append(_INDENT + inline_text(party.name))
                lines += [f"{_INDENT}{label}{tree.colon}{inline_text(value)}" for label, value in party.details]
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
            chunks.append("---")
            chunks.append(tree.attachments_label)
            for attachment in tree.attachments:
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
                return "\n".join(self.list(block, 0))
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

    def list(self, block: List, depth: int) -> list[str]:
        lines: list[str] = []
        indent = _INDENT * depth
        for item in block.items:
            marker = item.label if item.label else "-"
            if item.condition_label:
                marker = f"{marker} [{item.condition_label}]"
            first = True
            for child in item.blocks:
                if isinstance(child, List):
                    if first:
                        # The item opens with a nested list: its own label
                        # still comes first, on a line of its own.
                        lines.append(indent + marker)
                        first = False
                    lines += self.list(child, depth + 1)
                    continue
                text = self.block(child)
                if first:
                    lines.append(_prefix(f"{marker} {text}", indent, hanging=" " * (len(marker) + 1)))
                    first = False
                else:
                    lines.append(_prefix(text, indent + " " * (len(marker) + 1)))
            if first:
                lines.append(indent + marker)
        return lines

    def table(self, table: Table) -> str:
        header = [inline_text(cell) for cell in table.header]
        rows = [[inline_text(cell) for cell in row] for row in table.rows]
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
        lines += ["| " + " | ".join(row) + " |" for row in rows]
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
    get *prefix* plus *hanging* instead."""
    lines = text.split("\n")
    rest = prefix + hanging if hanging is not None else prefix
    return "\n".join([prefix + lines[0]] + [(rest + line) if line else line for line in lines[1:]])
