"""Stage 4: resolve the render tree (docs/architecture.md, docs/decisions/0003).

Resolution computes everything the specification leaves to render time and
writes it into the tree, so that writers only lay it out:

* **structure** — section numbers and designations (§13.1), list and
  paragraph labels (§13.2), and a collision-free output anchor for every
  section, item, paragraph, definition, and attachment (§5.6);
* **inlines** — every directive becomes display text: references (§13.3),
  terms (§13.4), field specs (§13.5), parties and sides (§3.6), attachments
  (§6.4), and the template view of ``{{choose:}}`` (§15.8). Anything that
  does not resolve becomes its bracketed failure marker (§11.5);
* **frontmatter** — the title block, attachment placeholders (§13.8), and
  signature blocks (§2.2).

The validator has already reported every document-level Error for the
failures shown here, so the resolver only adds the diagnostics that need a
style template or a renderer: ``ref-not-enumerated``, ``render-ref-ambiguous``,
and ``render-not-processed`` for constructs beyond the Rendering level (§17.5),
``render-link-refused`` for a link whose URL scheme is not one links may use
(§8.7), ``render-term-too-long`` for a term whose text is longer than a
``{{term:}}`` may show, ``render-term-limit`` for terms that refer to each other too deeply or too often,
and ``path-not-relative`` for an image whose path is not relative (§2.3), which
the validator does not check yet.

Every one of them names the file and the line it is at (§16.9): the first line
of the block, list item or heading being resolved, or the line of a frontmatter
key (``diagnostics.KeyLines``), tracked in :attr:`Resolver.line`. A finding in
a term's text is at the term's definition, since that is where the text is
worked out whatever reference asked for it first.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, replace
from typing import Any, NamedTuple
from urllib.parse import unquote

from legaldown import Diagnostic, Document, Party, ValidationResult
from legaldown.grammar import (
    ALWAYS,
    DECISION_QUESTION_TYPES,
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    VALID_PLACEHOLDER_TYPES,
    Presence,
    condition_problem,
    exclusive,
    is_positive_numeric,
    is_valid_iso_date,
    is_valid_money_amount,
    parse_condition,
    slugify_identifier,
)
from legaldown.syntax import Directive

from ..build import plain_inlines
from ..diagnostics import KeyLines, covered_by, repeats
from ..style import Style, effective_labels
from ..style.model import LevelFormat
from ..tree import (
    AttachmentPart,
    Blank,
    Block,
    Choice,
    CodeBlock,
    ContentsEntry,
    CrossRef,
    DefinedTerm,
    DefinitionSource,
    DirectiveSource,
    DraftingNote,
    Emphasis,
    FailureMarker,
    Image,
    Inline,
    Link,
    List,
    ListItem,
    Paragraph,
    PartyInfo,
    Quote,
    RenderTree,
    Section,
    SideInfo,
    SignatureParty,
    Strong,
    Table,
    TermRef,
    Text,
    Value,
    iter_located_body_inlines,
    iter_tree_inlines,
    map_tree_inlines,
    plain_text,
    unlinked,
)
from ..urls import is_empty_path, is_link_target, is_relative_path, printable
from .numbering import RENUMBERED, extend, fill, format_counter, heading_levels
from .values import DURATION_UNITS, Formatter

#: Definition anchors are prefixed with a character no identifier can hold,
#: so they can never collide with a section or item anchor (§5.6).
DEFINITION_ANCHOR_PREFIX = "def:"
#: The attachments heading's anchor, with a colon for the same reason.
ATTACHMENTS_ANCHOR = "ld:attachments"

#: Markers for a directive that breaks the §11.2 grammar: it carries no
#: arguments, so its type's marker is shown without a value.
_MALFORMED = {
    "ref": "[BROKEN REF]",
    "term": "[UNDEFINED]",
    "date": "[INVALID DATE]",
    "money": "[INVALID AMOUNT]",
    "party": "[INVALID PARTY]",
    "side": "[INVALID SIDE]",
    "duration": "[INVALID DURATION]",
    "field": "[INVALID FIELD]",
    "placeholder": "[INVALID PLACEHOLDER]",
    "attach": "[UNKNOWN ATTACHMENT]",
    "choose": "[INVALID CHOICE]",
    "include": "[NOT PROCESSED: include]",
}


#: The longest plain text a ``{{term:}}`` may show, in characters. No real
#: defined term comes near it; one longer is a term that reads other terms
#: that read other terms (each reference repeating the text of the next, so a
#: short document can ask for a text of 2**n words), and its reference shows
#: the definition id instead (``render-term-too-long``). Checked as each term
#: text is built, so no text grows past the source plus the nested references
#: times this.
_MAX_TERM_TEXT = 1000

#: The most inline nodes resolved, in one document, inside a term text that is
#: worked out again (a second or later text of a term, for another set of open
#: terms: only terms that name each other cause these), nested ones included;
#: and the most terms open inside one another. Past either, a ``{{term:}}``
#: shows its label or definition id and the Warning ``render-term-limit`` is
#: reported. A term's first text costs nothing (it is bounded by the number of
#: terms), so this bounds the extra work cycles can cause to a small multiple of
#: the document, where k terms each naming all k would otherwise take time
#: exponential in k, and a long chain of terms would exhaust the stack. No real
#: document comes near them.
_MAX_TERM_WORK = 200_000
_MAX_TERM_DEPTH = 32

#: How many texts (one per different set of open terms around it) are kept for
#: one term, so that a pathological tangle of cycles cannot fill memory.
_TERM_TEXTS_PER_TERM = 32


class _TermText(NamedTuple):
    """A term's text, the terms it named, and those it was cut short at
    because an enclosing term had them open (see ``Resolver._while_open``)."""

    text: str
    seen: frozenset[str]
    cut: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Target:
    """What a ``{{ref:}}`` to an anchor resolves to."""

    designation: str
    anchor: str | None
    #: False for an item or paragraph whose list or paragraphs the style does
    #: not number: the designation is then its section's (§6.3).
    enumerated: bool = True
    #: The numbered unit the anchor is on, for render-ref-ambiguous.
    slot: _Slot | None = None


class Resolver:
    def __init__(
        self,
        tree: RenderTree,
        document: Document,
        result: ValidationResult,
        style: Style,
        formatter: Formatter,
    ) -> None:
        self.tree = tree
        self.document = document
        self.metadata = document.metadata
        self.result = result
        self.style = style
        self.formatter = formatter
        self.labels = effective_labels(style.labels, tree.language)
        self.textual = style.numbering.scheme == "none"
        self.diagnostics: list[Diagnostic] = []
        # The line (§16.9) of what is being resolved, which a diagnostic
        # names: see _at.
        self.line: int | None = None
        # The lines of frontmatter keys (once the document is checked against its source).
        self.key_line = KeyLines(document)
        # A definition id -> the line of its first {{def:}} in the body (see
        # _survey), where its term's text is worked out.
        self.term_lines: dict[str, int | None] = {}
        self.targets: dict[str, _Target] = {}
        self.used_anchors: set[str] = set()
        # For render-ref-ambiguous: every designation a numbered unit — a
        # section, a paragraph, a list item — reads as, with each unit's slot.
        self.designation_slots: dict[str, list[_Slot]] = {}
        questions = self.metadata.questions
        self.questions: dict[str, Any] = questions if isinstance(questions, dict) else {}
        self.inconsistent_placeholders: set[str] = set()
        # A definition id -> the first {{def:}} of it in the body (see _survey),
        # and -> the plain text of its term as rendered there, worked out the
        # first time a {{term:}} needs it (see _term_text).
        self.term_sources: dict[str, DefinitionSource] = {}
        self.term_texts: dict[str, list[_TermText]] = {}
        # The same for the text-only pass before the targets are known, and
        # for the validator's term text where a definition is not in the body.
        self.term_texts_early: dict[str, list[_TermText]] = {}
        self.term_texts_plain: dict[str, list[_TermText]] = {}
        # The definitions whose term text is being worked out, so that a
        # {{term:}} inside a term, directly or through other terms, cannot
        # send the computation round in a circle.
        self._terms_open: list[str] = []
        # Since the text now being worked out began (see _while_open): every
        # term a {{term:}} named, and those of them it was cut short at
        # because they were open.
        self._seen: set[str] = set()
        self._cut: set[str] = set()
        # Inline nodes resolved inside repeat computations (see _while_open).
        self._term_work = 0
        self._repeating = 0
        # False until every target is registered and the placeholder types are
        # known (run() sets it just before the inlines are resolved).
        self._targets_known = False
        self.attachment_anchors: dict[str, str | None] = {}
        self.section_presences: list[Presence] = []

    # -- entry point ----------------------------------------------------------

    def run(self) -> RenderTree:
        self._survey()
        attachments = self._attachments()
        sections = self._number_sections()
        preamble = tuple(self._structure(block, section=None, depth=0, presence=ALWAYS) for block in self.tree.preamble)
        sections = tuple(
            replace(section, blocks=self._structure_section(section, presence))
            for section, presence in zip(sections, self.section_presences, strict=True)
        )
        self._targets_known = True
        resolve = self._resolve_inlines
        # In document order, so that the first of several definitions of a
        # term is the one that keeps its anchor.
        preamble = tuple(self._resolve_block(block) for block in preamble)
        sections = tuple(self._resolve_section(section) for section in sections)
        attachments = tuple(self._resolve_attachment(index, a) for index, a in enumerate(attachments))
        with self._at(self.key_line("title")):
            title = resolve(self.tree.title)
        with self._at(self.key_line("subtitle")):
            subtitle = resolve(self.tree.subtitle)
        resolved = replace(
            self.tree,
            title=title,
            subtitle=subtitle,
            locale=self.formatter.tag,
            preamble=preamble,
            sections=sections,
            header=self._header(),
            sides=self._sides() if self.style.title_block.parties else (),
            attachments=attachments,
            attachments_label=self.labels.attachments or "",
            contents=self._contents(sections, attachments),
            contents_label=self.labels.contents or "",
            colon=self.labels.colon or "",
            attachments_anchor=ATTACHMENTS_ANCHOR if attachments else "",
            signatures=self._signatures(),
            signature_labels={
                "date": self.labels.signature_date or "",
                "place": self.labels.signature_place or "",
                "name": self.labels.signature_name or "",
                "title": self.labels.signature_title or "",
                "heading": self.labels.signatures or "",
            },
        )
        # A {{term:}} links to its definition only if that definition was
        # given an anchor: definitions in titles or alt text are not, and a
        # definition may live in an attachment or an amended original.
        return map_tree_inlines(resolved, self._settle_term_target)

    def _resolve_section(self, section: Section) -> Section:
        with self._at(section.line):
            return replace(
                section,
                title=self._resolve_inlines(section.title),
                blocks=tuple(self._resolve_block(block) for block in section.blocks),
                condition_label=self._condition(section.condition),
            )

    def _resolve_attachment(self, index: int, attachment: AttachmentPart) -> AttachmentPart:
        with self._at(self.key_line("attachments", index, "title")):
            return replace(attachment, title=self._resolve_inlines(attachment.title))

    def _contents(
        self, sections: tuple[Section, ...], attachments: tuple[AttachmentPart, ...],
    ) -> tuple[ContentsEntry, ...]:
        """The table of contents, when the style enables it: every section
        whose number is at most *depth* levels deep, indented by that depth,
        then the attachment placeholders under the attachments heading."""
        settings = self.style.contents
        if not settings.enabled:
            return ()
        entries = [
            ContentsEntry(section.level, section.label, unlinked(section.title), section.anchor,
                          section.condition_label)
            for section in sections
            if section.level <= settings.depth
        ]
        if settings.attachments and attachments:
            # Under the attachments heading, unless a style blanks its label.
            level = 1
            if self.labels.attachments:
                entries.append(ContentsEntry(1, None, (Text(self.labels.attachments),), ATTACHMENTS_ANCHOR))
                level = 2
            entries += [
                ContentsEntry(level, None, unlinked(attachment.title), attachment.anchor, attachment.condition_label)
                for attachment in attachments
            ]
        return tuple(entries)

    def _settle_term_target(self, inline: Inline) -> Inline:
        if isinstance(inline, TermRef) and inline.target and inline.target not in self.used_anchors:
            return replace(inline, target=None)
        return inline

    def _survey(self) -> None:
        """One pass over the tree before resolving: which placeholder ids
        are used with conflicting types (§10.7), and which definition each
        defined term reads as, for ``{{term:}}`` (§7.3): the term's own text is worked out
        later, by :meth:`_term_text`, when its references can be resolved."""
        types: dict[str, set[str]] = {}
        for inline in iter_tree_inlines(self.tree):
            if isinstance(inline, DefinitionSource):
                # The first definition of an id is the one the validator's
                # lookup and the anchor keep.
                self.term_sources.setdefault(self._definition_id(inline), inline)
            elif isinstance(inline, DirectiveSource):
                directive = inline.directive
                if directive.name == "placeholder" and directive.positional and not directive.malformed:
                    types.setdefault(directive.positional, set()).add(self._placeholder_type(directive))
        self.inconsistent_placeholders = {pid for pid, found in types.items() if len(found) > 1}
        for inline, line in iter_located_body_inlines(self.tree):
            if isinstance(inline, DefinitionSource):
                self.term_lines.setdefault(self._definition_id(inline), line)

    @contextmanager
    def _at(self, line: int | None) -> Iterator[None]:
        """Name *line* in the diagnostics reported meanwhile; None for a
        finding with no line (a document built without a source)."""
        outer, self.line = self.line, line
        try:
            yield
        finally:
            self.line = outer

    def _here(self, line: int | None) -> AbstractContextManager[None]:
        """:meth:`_at` for a unit that may have no line of its own, which
        then has the line of the unit around it."""
        return self._at(self.line if line is None else line)

    # -- anchors --------------------------------------------------------------

    def _anchor(self, name: str, *, generated: bool = False) -> str | None:
        """An output anchor for *name*, or None when an earlier unit already
        took it — alternatives in a template view share an identifier
        (§15.4), and the first keeps the anchor. A name from the document
        never holds a colon (an Error the validator reports): that form is
        kept for the renderer's own *generated* anchors, so none can clash."""
        if not name or name in self.used_anchors or (":" in name and not generated):
            return None
        self.used_anchors.add(name)
        return name

    def _register(self, identifier: str, target: _Target) -> None:
        if identifier and identifier not in self.targets:
            self.targets[identifier] = target

    # -- structure: numbering, labels, anchors ---------------------------------

    def _number_sections(self) -> list[Section]:
        """Number the sections, and record each one's presence (§15.3).

        The numbers are the validator's own (``result.index.sections``),
        so a rendered number and a reference to it always agree with the
        validator: alternatives share a number (§15.8), and a skipped
        heading level counts as 1. The style only formats them, the n-th
        part of a number ("2.1" has two) with the n-th level format. A
        section's rendered level is how many parts its number has, so its
        number format, heading, and heading style always go together.
        """
        levels = heading_levels(self.style.numbering)
        presences: dict[int, Presence] = {}  # level -> presence of the open section
        out: list[Section] = []
        for section, indexed in zip(self.tree.sections, self.result.index.sections, strict=True):
            # The heading's own level, clamped to 1-5 (§4.1), decides which
            # sections enclose it, and so its presence (§15.3).
            level = min(max(section.level, 1), 5)
            enclosing = max((lvl for lvl in presences if lvl < level), default=None)
            presences = {lvl: p for lvl, p in presences.items() if lvl < level}
            presences[level] = (presences[enclosing] if enclosing is not None else ALWAYS) | self._presence(section.condition)
            self.section_presences.append(presences[level])
            parts = [int(part) for part in indexed.number.split(".")]
            if self.textual:
                with self._at(section.line):
                    label, designation = None, self._title_text(section.title)
            else:
                designation = ""
                for position, counter in enumerate(parts):
                    fmt = levels[min(position, len(levels) - 1)]
                    designation = extend(designation, fill(fmt.ref, n=format_counter(counter, fmt.counter)),
                                         textual=False)
                fmt = levels[min(len(parts), len(levels)) - 1]
                label = fill(fmt.label, n=format_counter(parts[-1], fmt.counter), path=designation)
            anchor = self._anchor(section.identifier)
            # Sections with one number are alternatives (the validator's).
            slot = _Slot(_SECTIONS, indexed.number, presences[level], "section", section.identifier)
            self._record(designation, slot)
            self._register(section.identifier, _Target(designation, anchor, slot=slot))
            out.append(replace(section, level=min(len(parts), 5), label=label, designation=designation,
                               anchor=anchor))
        return out

    def _presence(self, condition: str) -> Presence:
        """The presence a unit's own *condition* adds: empty when there is
        none, or when it is not a valid test of a declared decision question
        (condition-invalid, which the validator reports)."""
        if not condition or condition_problem(condition, self.questions) is not None:
            return ALWAYS
        parsed = parse_condition(condition)
        return frozenset({parsed}) if parsed else ALWAYS

    def _exclusive(self, first: Presence, second: Presence) -> bool:
        """True if units with these presences can never both appear (§15.4)."""
        return exclusive(first, second, self.questions)

    def _title_text(self, title: tuple[Inline, ...]) -> str:
        """A heading's text, as the ``none`` scheme designates it (§13.3).

        A text-only pass: it gives out no anchors, and it leaves references
        out wherever they are, since their targets are not all known yet.
        Other directives show their display text; a defined term its term.
        """
        def text(inlines: tuple[Inline, ...]) -> str:
            parts: list[str] = []
            for inline in inlines:
                if self._repeating:
                    self._term_work += 1
                match inline:
                    case DirectiveSource(directive=directive):
                        if directive.name != "ref":
                            resolved = self._directive(directive)
                            parts.append(plain_text((resolved,)) if resolved is not None else "")
                    case DefinitionSource(children=children) | Emphasis(children=children) \
                            | Strong(children=children) | Link(children=children) | Image(children=children):
                        parts.append(text(children))
                    case _:
                        parts.append(plain_text((inline,)))
            return "".join(parts)

        return " ".join(text(title).split())

    def _structure_section(self, section: Section, presence: Presence) -> tuple[Block, ...]:
        paragraphs = _Counter(self._exclusive)
        out: list[Block] = []
        for block in section.blocks:
            if isinstance(block, Paragraph) and block.top_level:
                # Alternative paragraphs share a number, as sections do (§15.8).
                own = self._own_presence(block.condition, presence)
                number = paragraphs.next(block.anchor_id, own)
                block = self._number_paragraph(block, section, paragraphs, number,
                                               presence if own is None else own)
            out.append(self._structure(block, section=section, depth=0, presence=presence))
        return tuple(out)

    def _own_presence(self, condition: str, enclosing: Presence) -> Presence | None:
        """A conditional unit's full presence, or None when it has no valid
        condition of its own (and so cannot be an alternative)."""
        own = self._presence(condition)
        return enclosing | own if own else None

    def _number_paragraph(self, block: Paragraph, section: Section, counter: _Counter, number: int,
                          presence: Presence) -> Paragraph:
        numbering = self.style.paragraphs
        base = section.designation or ""
        if numbering.numbered:
            fmt = numbering.format
            n = format_counter(number, fmt.counter)
            designation = extend(base, fill(fmt.ref, n=n, section=base), textual=self.textual)
            label = fill(fmt.label, n=n, section=base, path=designation)
            slot = _Slot(counter, number, presence, "paragraph", block.anchor_id)
            target = _Target(designation, None, slot=slot)
            self._record(designation, slot)
        else:
            label, target = None, _Target(base, None, enumerated=False)
        anchor = self._anchor(block.anchor_id)
        if block.anchor_id:
            self._register(block.anchor_id, replace(target, anchor=anchor))
        return replace(block, label=label, anchor=anchor)

    def _structure(self, block: Block, *, section: Section | None, depth: int, presence: Presence,
                   quoted: bool = False) -> Block:
        """*block* numbered and labelled. *quoted*: inside a quote, whose
        items the validator does not treat as units (they hold no marker)."""
        match block:
            case List():
                return self._structure_list(block, section=section, depth=depth,
                                            parent=section.designation if section else "", presence=presence,
                                            quoted=quoted)
            case Quote(blocks=blocks):
                return replace(block, blocks=tuple(self._structure(child, section=section, depth=depth,
                                                                   presence=presence, quoted=True)
                                                   for child in blocks))
            case DraftingNote(blocks=blocks):
                return replace(block, blocks=tuple(self._structure(child, section=section, depth=depth,
                                                                   presence=presence, quoted=True)
                                                   for child in blocks),
                               label=self.labels.drafting_note)
            case _:
                return block

    def _structure_list(self, block: List, *, section: Section | None, depth: int, parent: str,
                        presence: Presence, counters: dict[tuple[str, str] | None, _Counter] | None = None,
                        quoted: bool = False) -> List:
        """*block* numbered under *parent*. Sibling lists nested in the same
        item share *counters*, one per way of writing a designation (counter
        style and reference form): a second list whose designations would
        read like the first's goes on from it, so none repeats."""
        enumeration = self.style.enumeration
        fmt: LevelFormat | None
        if block.ordered and enumeration.ordered == "renumber":
            fmt = RENUMBERED
        elif block.ordered or enumeration.enabled:
            fmt = enumeration.levels[depth % len(enumeration.levels)] if enumeration.levels else None
        else:
            fmt = None
        base = section.designation if section else ""
        items: list[ListItem] = []
        if counters is None:
            counter = _Counter(self._exclusive)
        else:
            key = (fmt.counter, fmt.ref) if fmt is not None else None
            counter = counters.setdefault(key, _Counter(self._exclusive))
            counter.new_list()
        for item in block.items:
            index = counter.next(item.anchor_id, self._own_presence(item.condition, presence))
            # An item's nested blocks are present only when the item is.
            inner = presence | self._presence(item.condition)
            if fmt is not None:
                n = format_counter(index, fmt.counter)
                designation = extend(parent, fill(fmt.ref, n=n, section=base), textual=self.textual)
                label = fill(fmt.label, n=n, section=base, path=designation)
                target = _Target(designation, None)
                if not quoted:
                    slot = _Slot(counter, index, inner, "item", item.anchor_id)
                    target = replace(target, slot=slot)
                    self._record(designation, slot)
            else:
                designation, label, target = parent, None, _Target(base, None, enumerated=False)
            anchor = self._anchor(item.anchor_id)
            if item.anchor_id and section is not None:
                self._register(item.anchor_id, replace(target, anchor=anchor))
            siblings: dict[tuple[str, str] | None, _Counter] = {}
            children = tuple(
                self._structure_list(child, section=section, depth=depth + 1, parent=designation, presence=inner,
                                     counters=siblings, quoted=quoted)
                if isinstance(child, List)
                else self._structure(child, section=section, depth=depth + 1, presence=inner, quoted=quoted)
                for child in item.blocks
            )
            items.append(replace(item, blocks=children, label=label, anchor=anchor))
        return List(block.ordered, tuple(items), enumerated=fmt is not None)

    # -- blocks ---------------------------------------------------------------

    def _resolve_block(self, block: Block) -> Block:
        with self._here(getattr(block, "line", None)):
            return self._resolved_block(block)

    def _resolved_block(self, block: Block) -> Block:
        resolve = self._resolve_inlines
        match block:
            case Paragraph():
                return replace(block, inlines=resolve(block.inlines), condition_label=self._condition(block.condition))
            case List(items=items):
                return replace(block, items=tuple(self._resolve_item(item) for item in items))
            case Quote(blocks=blocks):
                return replace(block, blocks=tuple(self._resolve_block(child) for child in blocks))
            case DraftingNote(blocks=blocks):
                return replace(block, blocks=tuple(self._resolve_block(child) for child in blocks))
            case Table(header=header, rows=rows):
                return replace(block, header=tuple(resolve(cell) for cell in header),
                               rows=tuple(tuple(resolve(cell) for cell in row) for row in rows))
            case CodeBlock():
                return block
            case _:
                return block

    def _resolve_item(self, item: ListItem) -> ListItem:
        with self._here(item.line):
            return replace(item, blocks=tuple(self._resolve_block(child) for child in item.blocks),
                           condition_label=self._condition(item.condition))

    def _condition(self, condition: str) -> str | None:
        if not condition:
            return None
        return fill_condition(self.labels.condition or "{condition}", condition)

    # -- inlines --------------------------------------------------------------

    def _resolve_inlines(self, inlines: tuple[Inline, ...]) -> tuple[Inline, ...]:
        out: list[Inline] = []
        for inline in inlines:
            resolved = self._resolve_inline(inline)
            if isinstance(resolved, tuple):
                out.extend(resolved)
            elif resolved is not None:
                out.append(resolved)
        return tuple(out)

    def _resolve_inline(self, inline: Inline) -> Inline | tuple[Inline, ...] | None:
        if self._repeating:
            self._term_work += 1
        match inline:
            case DirectiveSource(directive=directive):
                return self._directive(directive)
            case DefinitionSource(children=children):
                identifier = self._definition_id(inline)
                return DefinedTerm(self._resolve_inlines(children),
                                   self._anchor(DEFINITION_ANCHOR_PREFIX + identifier, generated=True),
                                   self.style.definitions.style)
            case Emphasis(children=children):
                return Emphasis(self._resolve_inlines(children))
            case Strong(children=children):
                return Strong(self._resolve_inlines(children))
            case Link(href=href, children=children, title=title):
                if href.strip() and not is_link_target(href):
                    self._link_refused(href)
                return replace(inline, children=self._resolve_inlines(children),
                               title=self._resolve_inlines(_attribute_text(title)))
            case Image(src=src, children=children, title=title):
                alt = self._resolve_inlines(_attribute_text(children))
                if is_empty_path(src):
                    # No path, no picture: <img src=""> would request the page
                    # itself. §2.3 says nothing of an empty path, so no rule fires.
                    return alt
                if not is_relative_path(src):
                    # §2.3: an image path is relative and has no URI scheme, so a
                    # remote or absolute image is never fetched or embedded. Its
                    # alt text stands in; the validator (0.4) does not check
                    # image paths, so the renderer reports the Core rule.
                    self._not_relative(src)
                    return alt
                return replace(inline, children=alt, title=self._resolve_inlines(_attribute_text(title)))
            case _:
                return inline

    def _definition_id(self, source: DefinitionSource) -> str:
        return source.directive.positional or slugify_identifier(source.term)

    def _directive(self, directive: Directive) -> Inline | None:
        name = directive.name
        if name not in _HANDLERS:
            return FailureMarker(f"[UNKNOWN DIRECTIVE: {name}]")
        if directive.malformed:
            return FailureMarker(_MALFORMED[name]) if name in _MALFORMED else None
        return _HANDLERS[name](self, directive, directive.positional or "")

    def _ref(self, directive: Directive, target_id: str) -> Inline:
        target = self.targets.get(target_id)
        if target is None:
            return FailureMarker(f"[BROKEN REF: {target_id}]")
        if not target.enumerated:
            self._warn(
                "ref-not-enumerated",
                f"'{{{{ref: {target_id}}}}}' targets an item or paragraph that the style does not number; "
                f"it renders as its section's designation, '{target.designation}' (§6.3).",
            )
        elif target.slot is not None and (clashes := self._clashes(target.slot, target.designation)):
            self._warn("render-ref-ambiguous", self._ambiguity_message(target_id, target, clashes))
        text = self.style.references.format.replace("{designation}", target.designation)
        return CrossRef(text, target.anchor or "")

    def _record(self, designation: str, slot: _Slot) -> None:
        """Note that a numbered unit reads as *designation* (render-ref-ambiguous)."""
        self.designation_slots.setdefault(designation, []).append(slot)

    def _clashes(self, slot: _Slot, designation: str) -> list[_Slot]:
        """The other numbered units that may read as *designation*, the one
        *slot*'s unit reads as, in a document where both appear. Its own
        alternatives share its number and are not others; a second use of
        its identifier is left to the validator (anchor-duplicate). A hint,
        not a proof: units under exclusive conditions never appear together
        (§15.4), but where the reference itself stands is not considered."""
        slots = self.designation_slots.get(designation, [])
        own = [other.presence for other in slots if other.key == slot.key]
        return [
            other for other in slots
            if other.key != slot.key and not (slot.anchor and other.anchor == slot.anchor)
            and any(not self._exclusive(presence, other.presence) for presence in own)
        ]

    def _ambiguity_message(self, target_id: str, target: _Target, clashes: list[_Slot]) -> str:
        """render-ref-ambiguous, with advice for what clashes."""
        start = f"'{{{{ref: {target_id}}}}}' renders as '{target.designation}', which "
        kinds = {target.slot.kind} | {other.kind for other in clashes} if target.slot else set()
        if kinds == {"item"}:
            return (start + "another list item also reads as: each list starts again at its first number. "
                    "Make them one list, or refer to the item in words.")
        if "section" in kinds and self.textual:
            return (start + "another heading also reads as: the none scheme refers to a section by its "
                    "heading text. Rename one, or refer to it in words.")
        names = sorted({{"section": "a section", "paragraph": "a numbered paragraph", "item": "a list item"}[kind]
                        for kind in (other.kind for other in clashes)})
        return (start + f"{' or '.join(names)} also reads as. Refer to it in words, or change the style's "
                "numbering so that they differ.")

    def _term(self, directive: Directive, definition_id: str) -> Inline:
        term = self.result.index.definition_lookup.get(definition_id)
        if term is None:
            return FailureMarker(f"[UNDEFINED: {definition_id}]")
        label = directive.params.get("label")
        if definition_id in self._terms_open:
            # A term that refers to itself, directly or through others: the
            # reference shows its label, else the id, and goes no further.
            text = label or definition_id
            self._seen.add(definition_id)
            self._cut.add(definition_id)
        else:
            self._seen.add(definition_id)
            # The term as rendered at its definition; the validator's own
            # text (an empty term reads as its id, a definition not in the
            # body) is the fallback.
            text = label or self._term_text(definition_id)
            if text == "":
                text = self._plain_open(definition_id, term)
            if text is None:
                text = definition_id  # past the limits (see _while_open)
        # Settled once everything is resolved (see _settle_term_target).
        target = DEFINITION_ANCHOR_PREFIX + definition_id
        return TermRef(text, target, self.style.definitions.term_style)

    def _term_text(self, definition_id: str) -> str | None:
        """The plain text of the term at the first definition of *definition_id*
        in the body, as the reader sees it there: its Markdown, references,
        placeholders and other terms resolved exactly as at the definition
        (§7.3), not the validator's raw ``anchor.term``. Empty when the
        definition is not in the body.

        Worked out the first time it is needed and kept, which is once every
        target is registered and the placeholder types are known. Before that
        (a heading read as text by the ``none`` scheme, §13.3) a text-only pass
        stands in, which leaves references out; it is kept apart from the
        text of the later pass. Resolving the term gives out no anchor: a
        definition inside a term keeps its place in document order. The
        definition is open meanwhile, so a ``{{term:}}`` inside it, directly or
        through others, shows its label or id and goes no further (see
        :meth:`_while_open`). A text longer than :data:`_MAX_TERM_TEXT` is the
        definition id instead, and the Warning ``render-term-too-long`` says
        so, once per id. ``None`` is a term past the limits of
        :meth:`_while_open`."""
        source = self.term_sources.get(definition_id)
        if source is None:
            return ""
        known = self._targets_known

        def work() -> str:
            # Worked out where the term is defined, whichever reference asks
            # first, so what it reports is at the definition.
            with self._here(self.term_lines.get(definition_id)):
                if not known:
                    text = self._title_text(source.children)
                else:
                    anchors = set(self.used_anchors)
                    try:
                        text = plain_text(self._resolve_inlines(source.children))
                    finally:
                        self.used_anchors = anchors
                if len(text) > _MAX_TERM_TEXT:
                    self._warn(
                        "render-term-too-long",
                        f"The term of '{printable(definition_id)}' is longer than {_MAX_TERM_TEXT} characters once "
                        f"its references are resolved; '{{{{term: {printable(definition_id)}}}}}' shows the "
                        f"definition id.",
                    )
                    return definition_id
                return text

        return self._while_open(self.term_texts if known else self.term_texts_early, definition_id, work)

    def _plain_open(self, definition_id: str, term: str) -> str | None:
        """The validator's *term* text of a definition as plain text, with
        the definition marked open while its directives resolve."""
        key = f"{definition_id}\0{int(self._targets_known)}"
        return self._while_open(self.term_texts_plain, definition_id, lambda: self._plain_value(term), key=key)

    def _while_open(self, cache: dict[str, list[_TermText]], definition_id: str, work: Callable[[], str],
                    *, key: str | None = None) -> str | None:
        """The text *work* gives with *definition_id* open, kept in *cache*
        (under *key*, default the id), so a chain of terms that each use the
        next one twice is worked out once per term, not once per use.

        What a text reads depends on which terms are open around it: a
        reference to an open term is cut short and shows its label or id. A
        kept text records the terms it named (``seen``) and those it was cut
        short at that were open *before* this one (``cut``: a cycle back
        through an enclosing term). It is reused only where exactly those of
        the terms it named are open, so it reads as it would if worked out
        afresh there. A cut at this term itself, or at one opened inside it, is
        its own business and does not count. Without a cycle every term is
        worked out once.

        A cycle can still ask for a great many different texts (k terms that
        each name all k have more ways round than any cache holds), so the work
        is limited. A text worked out when the cache already holds one for
        this term (for other open terms) is a repeat, and the inline nodes
        resolved inside a repeat, nested ones included, are counted; first
        texts are free. Past :data:`_MAX_TERM_WORK` such nodes, or with
        :data:`_MAX_TERM_DEPTH` terms open at once, the result is ``None`` (the
        reference shows its label or id), and the Warning ``render-term-limit``
        says so, once.
        """
        key = definition_id if key is None else key
        around = set(self._terms_open)
        for kept in cache.get(key, ()):
            if kept.seen & around == kept.cut:
                self._seen |= kept.seen
                self._cut |= kept.cut
                return kept.text
        repeat = bool(cache.get(key))
        if len(self._terms_open) >= _MAX_TERM_DEPTH or (repeat and self._term_work >= _MAX_TERM_WORK):
            # Terms that refer to each other this deeply or this often are
            # not resolved further: like a cut, the reference shows its label
            # or id, and nothing is kept.
            self._warn(
                "render-term-limit",
                "Defined terms refer to each other too deeply or too often to resolve fully; "
                "some {{term:}} references show their definition id.",
            )
            return None
        self._repeating += repeat
        self._terms_open.append(definition_id)
        outer = self._seen, self._cut
        self._seen, self._cut = set(), set()
        try:
            text = work()
            seen, cut = frozenset(self._seen), frozenset(self._cut & around)
            if len(cache.setdefault(key, [])) < _TERM_TEXTS_PER_TERM:
                cache[key].append(_TermText(text, seen, cut))
        finally:
            self._repeating -= repeat
            self._terms_open.pop()
            self._seen = outer[0] | self._seen
            self._cut = outer[1] | (self._cut & around)
        return text

    def _date(self, directive: Directive, value: str) -> Inline:
        if not is_valid_iso_date(value):
            return FailureMarker(f"[INVALID DATE: {value}]")
        return Value("date", self.formatter.date(value))

    def _money(self, directive: Directive, amount: str) -> Inline:
        currency = directive.params.get("currency")
        if not is_valid_money_amount(amount):
            return FailureMarker(f"[INVALID AMOUNT: {amount}]")
        if currency is not None and currency not in KNOWN_CURRENCIES:
            return FailureMarker(f"[UNKNOWN CURRENCY: {currency}]")
        return Value("money", self.formatter.money(amount, currency))

    def _party(self, directive: Directive, name: str) -> Inline:
        return self._named("party", name, self.result.index.party_lookup, directive)

    def _side(self, directive: Directive, name: str) -> Inline:
        return self._named("side", name, self.result.index.side_lookup, directive)

    def _named(self, kind: str, name: str, lookup: dict[str, str], directive: Directive) -> Inline:
        """``{{party:}}`` and ``{{side:}}`` (§13.5): failure markers first — a
        label never hides one (§11.5) — then the label, then the display name
        the validator resolved (§3.6)."""
        if not IDENTIFIER_RE.fullmatch(name):
            return FailureMarker(f"[INVALID {kind.upper()}: {name}]")
        if name not in lookup:
            return FailureMarker(f"[UNKNOWN {kind.upper()}: {name}]")
        key = self._side_key(name) if kind == "side" else self._party_key(name)
        return Value(kind, directive.params.get("label") or self._plain_value(lookup[name], *key))

    def _side_key(self, name: str) -> tuple[Any, ...]:
        """The frontmatter path of side *name*, or ``()``."""
        return next((("sides", i) for i, side in enumerate(self.metadata.sides) if side.name == name), ())

    def _party_key(self, name: str) -> tuple[Any, ...]:
        """The frontmatter path of party *name*, or ``()``."""
        return next((("sides", i, "parties", j) for i, side in enumerate(self.metadata.sides)
                     for j, party in enumerate(side.parties) if party.name == name), ())

    def _duration(self, directive: Directive, value: str) -> Inline:
        unit = directive.params.get("unit")
        if not is_positive_numeric(value):
            return FailureMarker(f"[INVALID DURATION: {value}]")
        if unit not in DURATION_UNITS:
            return FailureMarker(f"[INVALID DURATION UNIT: {unit}]" if unit else "[INVALID DURATION UNIT]")
        shown = self.formatter.format_duration(value, unit)
        if shown.english_fallback:
            # One Warning per locale and unit: _warn drops a repeat. A value
            # is never shown as a raw CLDR unit id (§10.5).
            self._warn("render-locale-fallback",
                       f"No CLDR pattern for '{DURATION_UNITS[unit]}' in '{self.formatter.tag}'; English used.")
        return Value("duration", shown.text)

    def _field(self, directive: Directive, value: str) -> Inline:
        field_type = directive.params.get("type")
        if not field_type or not IDENTIFIER_RE.fullmatch(field_type):
            return FailureMarker(f"[INVALID FIELD: {value}]" if value else "[INVALID FIELD]")
        return Value("field", value)

    def _placeholder_type(self, directive: Directive) -> str:
        """The effective type of a placeholder (§10.7): as written, else its
        declared question's, else ``text``."""
        declared = self.questions.get(directive.positional or "")
        declared_type = declared.get("type") if isinstance(declared, dict) else None
        return directive.params.get("type") or (declared_type if isinstance(declared_type, str) else None) or "text"

    def _placeholder(self, directive: Directive, placeholder_id: str) -> Inline:
        invalid = FailureMarker(f"[INVALID PLACEHOLDER: {placeholder_id}]" if placeholder_id else "[INVALID PLACEHOLDER]")
        if not IDENTIFIER_RE.fullmatch(placeholder_id):
            return invalid
        placeholder_type = self._placeholder_type(directive)
        declared = self.questions.get(placeholder_id)
        declared_type = declared.get("type") if isinstance(declared, dict) else None
        if isinstance(declared_type, str) and declared_type in DECISION_QUESTION_TYPES:
            return invalid  # a decision question's id is not a blank (§15.2)
        if placeholder_type not in VALID_PLACEHOLDER_TYPES or placeholder_id in self.inconsistent_placeholders:
            return invalid
        currency = directive.params.get("currency")
        if placeholder_type == "money" and currency is not None and currency not in KNOWN_CURRENCIES:
            return FailureMarker(f"[UNKNOWN CURRENCY: {currency}]")
        unit = directive.params.get("unit")
        if placeholder_type == "duration" and unit is not None and unit not in DURATION_UNITS:
            return FailureMarker(f"[INVALID DURATION UNIT: {unit}]")
        prompt = ""
        if self.style.placeholders.show_prompt and isinstance(declared, dict):
            prompt = str(declared.get("prompt") or "")
        # §13.5 rule 6: a style whose blank is empty offers no visual blank,
        # and neither does one of whitespace or format characters only
        # (U+200B zero-width space, U+FEFF, bidi controls): it shows nothing.
        blank = self.style.placeholders.blank
        shows = any(not c.isspace() and unicodedata.category(c) != "Cf" for c in blank)
        return Blank(placeholder_id, blank if shows else f"[TBD: {placeholder_id}]", prompt)

    def _attach(self, directive: Directive, attachment_id: str) -> Inline:
        title = self.result.index.attachment_lookup.get(attachment_id)
        if title is None:
            return FailureMarker(f"[UNKNOWN ATTACHMENT: {attachment_id}]")
        anchor = self.attachment_anchors.get(attachment_id)
        if anchor is not None:
            href = "#" + anchor
        else:
            declared = next((a for a in self.metadata.attachments if a.id == attachment_id), None)
            href = declared.file if declared and declared.file else None
        key = next((("attachments", i, "title") for i, a in enumerate(self.metadata.attachments)
                    if a.id == attachment_id), ())
        return Value("attach", directive.params.get("label") or self._plain_value(title, *key), href)

    def _choose(self, directive: Directive, question_id: str) -> Inline:
        invalid = FailureMarker(f"[INVALID CHOICE: {question_id}]" if question_id else "[INVALID CHOICE]")
        question = self.questions.get(question_id)
        if not isinstance(question, dict):
            return invalid
        if question.get("type") == "boolean":
            expected = {"true", "false"}
        elif question.get("type") == "choice" and isinstance(question.get("choices"), dict):
            expected = {str(value) for value in question["choices"]}
        else:
            return invalid
        if directive.duplicates or set(directive.params) != expected:
            return invalid
        empty = self.style.template_view.empty_choice
        options = tuple(value if value else empty for value in directive.params.values())
        return Choice(question_id, options, self.style.template_view.choice_separator)

    def _include(self, directive: Directive, path: str) -> Inline:
        # Includes are a Full-level capability (§17.4): never dropped silently.
        self._warn(
            "render-not-processed",
            f"'{{{{include: {path}}}}}' was not processed: file inclusion is beyond the Rendering "
            f"conformance level (§17.5).",
        )
        return FailureMarker(f"[NOT PROCESSED: include {path}]")

    def _def(self, directive: Directive, identifier: str) -> Inline | None:
        return None  # a {{def:}} with no quoted term: an Error; it shows nothing (§7.2)

    # -- frontmatter ----------------------------------------------------------

    def _plain_value(self, text: str, *key: Any) -> str:
        """A frontmatter string with its directives (placeholders, §3.10)
        resolved, as plain text. *key* is the path of the frontmatter value in
        the document, when the text is shown elsewhere (a title or a name
        in the body): what it reports is at that key, not where it is shown."""
        if "{{" not in text:
            return text
        with self._at(self.key_line(*key)) if key else nullcontext():
            return plain_text(self._resolve_inlines(plain_inlines(text)))

    def _frontmatter(self, text: str) -> tuple[Inline, ...]:
        return self._resolve_inlines(plain_inlines(text))

    def _header(self) -> tuple[tuple[str, tuple[Inline, ...]], ...]:
        rows: list[tuple[str, tuple[Inline, ...]]] = []
        metadata = self.metadata
        if self.style.title_block.effective_date and metadata.effective_date:
            with self._at(self.key_line("effective_date")):
                rows.append((self.labels.effective_date or "", self._date_or_text(metadata.effective_date)))
        if self.style.title_block.version and metadata.version:
            with self._at(self.key_line("version")):
                rows.append((self.labels.version or "", self._frontmatter(metadata.version)))
        return tuple(rows)

    def _date_or_text(self, value: str) -> tuple[Inline, ...]:
        if is_valid_iso_date(value):
            return (Value("date", self.formatter.date(value)),)
        return self._frontmatter(value)

    def _sides(self) -> tuple[SideInfo, ...]:
        sides: list[SideInfo] = []
        for side_index, side in enumerate(self.metadata.sides):
            parties = tuple(self._party_info(side_index, party_index, party)
                            for party_index, party in enumerate(side.parties))
            label = self.result.index.side_lookup.get(side.name) or side.label or side.name
            with self._at(self.key_line("sides", side_index, "label")):
                sides.append(SideInfo(self._frontmatter(label), parties))
        return tuple(sides)

    def _party_info(self, side_index: int, party_index: int, party: Party) -> PartyInfo:
        with self._at(self.key_line("sides", side_index, "parties", party_index)):
            details: list[tuple[str, tuple[Inline, ...]]] = []
            if party.identification_number:
                details.append((self.labels.identification_number or "", self._frontmatter(party.identification_number)))
            if party.date_of_birth:
                details.append((self.labels.date_of_birth or "", self._date_or_text(party.date_of_birth)))
            if party.address:
                details.append((self.labels.address or "", self._frontmatter(party.address)))
            for custom in party.custom_fields:
                details.append((custom.label, self._frontmatter(custom.value)))
            for rep in party.representatives:
                name, title = self._frontmatter(rep.name), self._frontmatter(rep.title)
                value = name + (Text(", "),) + title if name and title else name or title
                if value:
                    details.append((self.labels.represented_by or "", value))
            return PartyInfo(
                name=self._frontmatter(party.legal_name or party.label or party.name),
                details=tuple(details),
            )

    def _attachments(self) -> tuple[AttachmentPart, ...]:
        """Attachment placeholders (§13.8), in declared order. At the
        Rendering level the files are not read, so each shows its title."""
        parts: list[AttachmentPart] = []
        for attachment in self.metadata.attachments:
            if self.style.attachments.render == "omit":
                continue
            anchor = self._anchor(attachment.id)
            self.attachment_anchors[attachment.id] = anchor
            parts.append(AttachmentPart(
                id=attachment.id,
                title=plain_inlines(attachment.title or attachment.id),
                file=fill_condition(self.labels.attachment_file or "{file}", attachment.file, key="file")
                if attachment.file else "",
                anchor=anchor,
                condition_label=self._condition(attachment.when),
            ))
        return tuple(parts)

    def _signatures(self) -> tuple[SignatureParty, ...]:
        if not (self.style.signatures.enabled and self.metadata.include_signatures):
            return ()
        blocks: list[SignatureParty] = []
        for side_index, side in enumerate(self.metadata.sides):
            with self._at(self.key_line("sides", side_index, "label")):
                label = self._frontmatter(self.result.index.side_lookup.get(side.name) or side.label or side.name)
            for party_index, party in enumerate(side.parties):
                with self._at(self.key_line("sides", side_index, "parties", party_index)):
                    # Where signature blocks are generated, legal_name MUST appear (§3.6).
                    blocks.append(SignatureParty(
                        side=label,
                        name=self._frontmatter(party.legal_name or party.label or party.name),
                        signatories=tuple(
                            (self._frontmatter(rep.name), self._frontmatter(rep.title))
                            for rep in party.representatives
                        ),
                    ))
        return tuple(blocks)

    # -- diagnostics ----------------------------------------------------------

    def _found(self, rule: str, level: str, message: str) -> Diagnostic:
        """A diagnostic of the renderer's, at the line being resolved, in the
        document's file (§16.9)."""
        return Diagnostic(rule=rule, level=level, message=message, line=self.line, file=self.document.filename)

    def _warn(self, rule: str, message: str) -> None:
        """Report a Warning, unless the same one (rule and message) is
        reported already: wherever it recurs, it stands at its first line."""
        diagnostic = self._found(rule, "warning", message)
        if not repeats(diagnostic, self.diagnostics):
            self.diagnostics.append(diagnostic)

    def _link_refused(self, href: str) -> None:
        """Report a link whose URL does not use a scheme links may use
        (:data:`~legaldown_render.urls.LINK_SCHEMES`) as ``render-link-refused``:
        the writers show its text and say nothing (ADR 0003), so the reader of
        the document would not otherwise know a link was lost."""
        shown = printable(unquote(href.strip()))
        self._warn(
            "render-link-refused",
            f"Link to '{shown}' is not rendered as a link: only http, https, mailto, tel, ftp, ftps, sftp, sip, "
            f"sips, sms, urn, geo, news and relative links are; its text is shown.",
        )

    def _not_relative(self, path: str) -> None:
        """Report an image *path* that is not relative as the Core Error
        ``path-not-relative`` (§2.3, §16.2), unless the validator has
        reported it (:func:`~legaldown_render.diagnostics.covered_by`: its
        report of the path, at this line). markdown-it has percent-encoded
        the path ("C:\\x" reads "C:%5Cx"), so it is decoded to name the path
        as written."""
        path = unquote(path)
        shown = printable(path)
        diagnostic = self._found(
            "path-not-relative", "error",
            f"Image path '{shown}' is not relative (§2.3): file references are relative paths with no "
            f"URI scheme. Its alt text is shown instead.",
        )
        # Assumption: the validator quotes the path in its message as '<path>',
        # as written. Matching the quoted form keeps a path that is a prefix of
        # another ("/a" and "/ab") from being counted as already reported.
        if covered_by(diagnostic, path, self.result.diagnostics) or repeats(diagnostic, self.diagnostics):
            return
        self.diagnostics.append(diagnostic)


_HANDLERS: dict[str, Callable[[Resolver, Directive, str], Inline | None]] = {
    "ref": Resolver._ref,
    "term": Resolver._term,
    "def": Resolver._def,
    "date": Resolver._date,
    "money": Resolver._money,
    "party": Resolver._party,
    "side": Resolver._side,
    "duration": Resolver._duration,
    "field": Resolver._field,
    "placeholder": Resolver._placeholder,
    "attach": Resolver._attach,
    "choose": Resolver._choose,
    "include": Resolver._include,
}


#: What numbers sections, in a unit's slot: the validator (its numbers).
_SECTIONS = object()


class _Counter:
    """Numbers sibling units in order (§13.2, §15.8). A unit joins the
    previous unit's number when it is an alternative to it: the same
    identifier, and a presence that excludes the presence of every unit
    already holding that number (§15.4). Only adjacent units are compared,
    as the validator does for sections."""

    def __init__(self, exclusive: Callable[[Presence, Presence], bool]) -> None:
        self.exclusive = exclusive
        self.count = 0
        self.identifier = ""
        self.holders: list[Presence] = []

    def new_list(self) -> None:
        """Go on counting in another list: its first unit is no alternative
        to the last unit of the list before, which is not its sibling."""
        self.identifier = ""
        self.holders = []

    def next(self, identifier: str, presence: Presence | None) -> int:
        """The number for the next unit. *presence* is None for a unit with
        no valid condition of its own, which is never an alternative."""
        if (presence is not None and identifier and identifier == self.identifier
                and all(self.exclusive(holder, presence) for holder in self.holders)):
            self.holders.append(presence)
            return self.count
        self.count += 1
        self.identifier = identifier if presence is not None else ""
        self.holders = [presence] if presence is not None else []
        return self.count


class _Slot(NamedTuple):
    """Where a numbered unit's designation comes from, for render-ref-ambiguous."""

    #: What numbers the unit: its list's or paragraphs' _Counter, or
    #: _SECTIONS for a section (the validator numbers those).
    source: object
    #: Its number there; alternatives share one (§15.8).
    number: object
    #: When the unit appears (§15.3).
    presence: Presence
    #: "section", "paragraph", or "item".
    kind: str
    #: The identifier it anchors, or "".
    anchor: str

    @property
    def key(self) -> tuple[object, object]:
        """The unit and its alternatives: its source and number."""
        return (self.source, self.number)


def fill_condition(template: str, value: str, *, key: str = "condition") -> str:
    return template.replace("{" + key + "}", value)


def _attribute_text(inlines: tuple[Inline, ...]) -> tuple[Inline, ...]:
    """*inlines* for a plain-text attribute (a title, alt text): a defined
    term there shows its term but is not the definition's anchor, since an
    attribute cannot hold one — however deeply it is nested."""
    out: list[Inline] = []
    for inline in inlines:
        if isinstance(inline, DefinitionSource):
            out.extend(_attribute_text(inline.children))
            continue
        children = getattr(inline, "children", None)
        if isinstance(children, tuple):
            inline = replace(inline, children=_attribute_text(children))
        out.append(inline)
    return tuple(out)


def resolve_tree(
    tree: RenderTree,
    document: Document,
    result: ValidationResult,
    style: Style,
    formatter: Formatter,
) -> tuple[RenderTree, list[Diagnostic]]:
    """The resolved tree, and the diagnostics only rendering can produce."""
    resolver = Resolver(tree, document, result, style, formatter)
    resolved = resolver.run()
    return resolved, resolver.diagnostics


__all__ = ["DEFINITION_ANCHOR_PREFIX", "Resolver", "resolve_tree"]

