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
style template or a renderer: ``ref-not-enumerated``, ``raw-html``, and
constructs beyond the Rendering level (§17.5).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from legaldown import Diagnostic, Directive, Document, ValidationResult, slugify_identifier
from legaldown.validator import KNOWN_CURRENCIES
from legaldown.validator.helpers import is_positive_numeric, is_valid_iso_date, is_valid_money_amount
from legaldown.validator.patterns import IDENTIFIER_RE

from ..build import plain_inlines
from ..style import Style, effective_labels
from ..style.model import LevelFormat
from ..tree import (
    AttachmentPart,
    Blank,
    Block,
    Choice,
    CodeBlock,
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
    iter_block_inlines,
    iter_inlines,
    plain_text,
)
from .numbering import RENUMBERED, extend, fill, format_counter, heading_levels
from .values import DURATION_UNITS, Formatter

#: Definition anchors are prefixed with a character no identifier can hold,
#: so they can never collide with a section or item anchor (§5.6).
DEFINITION_ANCHOR_PREFIX = "def:"

_VALUE_TYPES = ("text", "date", "money", "duration")
_DECISION_TYPES = ("boolean", "choice")

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


@dataclass(frozen=True, slots=True)
class _Target:
    """What a ``{{ref:}}`` to an anchor resolves to."""

    designation: str
    anchor: str | None
    #: False for an item or paragraph whose list or paragraphs the style does
    #: not number: the designation is then its section's (§6.3).
    enumerated: bool = True


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
        self.targets: dict[str, _Target] = {}
        self.used_anchors: set[str] = set()
        questions = self.metadata.questions
        self.questions: dict[str, Any] = questions if isinstance(questions, dict) else {}
        self.definition_ids: set[str] = set()
        self.inconsistent_placeholders: set[str] = set()
        self.template = tree.is_template
        self.attachment_anchors: dict[str, str | None] = {}

    # -- entry point ----------------------------------------------------------

    def run(self) -> RenderTree:
        self._survey()
        attachments = self._attachments()
        sections = self._number_sections()
        preamble = tuple(self._structure(block, section=None, depth=0) for block in self.tree.preamble)
        sections = tuple(
            replace(section, blocks=self._structure_section(section))
            for section in sections
        )
        resolve = self._resolve_inlines
        sections = tuple(
            replace(
                section,
                title=resolve(section.title),
                blocks=tuple(self._resolve_block(block) for block in section.blocks),
                condition_label=self._condition(section.condition),
            )
            for section in sections
        )
        return replace(
            self.tree,
            is_template=self.template,
            title=resolve(self.tree.title),
            subtitle=resolve(self.tree.subtitle),
            locale=self.formatter.tag,
            preamble=tuple(self._resolve_block(block) for block in preamble),
            sections=sections,
            header=self._header(),
            sides=self._sides() if self.style.title_block.parties else (),
            attachments=tuple(replace(a, title=resolve(a.title)) for a in attachments),
            attachments_label=self.labels.attachments or "",
            signatures=self._signatures(),
            signature_labels={
                "date": self.labels.signature_date or "",
                "place": self.labels.signature_place or "",
                "name": self.labels.signature_name or "",
                "title": self.labels.signature_title or "",
                "heading": self.labels.signatures or "",
            },
        )

    def _survey(self) -> None:
        """One pass over the tree before resolving: which definitions exist,
        whether template constructs are used, and which placeholder ids are
        used with conflicting types (§10.7)."""
        types: dict[str, set[str]] = {}
        blocks = list(self.tree.preamble) + [block for section in self.tree.sections for block in section.blocks]
        inlines = [i for block in blocks for i in iter_block_inlines(block)]
        inlines += [i for section in self.tree.sections for i in iter_inlines(section.title)]
        inlines += list(iter_inlines(self.tree.title)) + list(iter_inlines(self.tree.subtitle))
        for inline in inlines:
            if isinstance(inline, DefinitionSource):
                self.definition_ids.add(self._definition_id(inline))
            elif isinstance(inline, DirectiveSource):
                directive = inline.directive
                if directive.name == "choose":
                    self.template = True
                elif directive.name == "placeholder" and directive.positional and not directive.malformed:
                    types.setdefault(directive.positional, set()).add(self._placeholder_type(directive))
        self.inconsistent_placeholders = {pid for pid, found in types.items() if len(found) > 1}
        if any(section.condition for section in self.tree.sections) or any(
            _has_condition(block) for block in blocks
        ):
            self.template = True

    # -- anchors --------------------------------------------------------------

    def _anchor(self, name: str) -> str | None:
        """An output anchor for *name*, or None when an earlier unit already
        took it — alternatives in a template view share an identifier
        (§15.4), and the first keeps the anchor."""
        if not name or name in self.used_anchors:
            return None
        self.used_anchors.add(name)
        return name

    def _register(self, identifier: str, target: _Target) -> None:
        if identifier and identifier not in self.targets:
            self.targets[identifier] = target

    # -- structure: numbering, labels, anchors ---------------------------------

    def _number_sections(self) -> list[Section]:
        levels = heading_levels(self.style.numbering)
        counters = [0] * 7
        previous: dict[int, Section] = {}
        out: list[Section] = []
        for section in self.tree.sections:
            level = section.level
            sibling = previous.get(level)
            # Alternatives share an identifier, and SHOULD share a number (§15.8).
            if not (sibling is not None and _alternatives(sibling.identifier, sibling.condition,
                                                          section.identifier, section.condition)):
                counters[level] += 1
            # An alternative's subsections are numbered afresh, like any section's.
            counters[level + 1:] = [0] * (6 - level)
            previous = {lvl: s for lvl, s in previous.items() if lvl < level}
            previous[level] = section
            if self.textual:
                label, designation = None, self._title_text(section.title)
            else:
                designation = ""
                for depth in range(1, level + 1):
                    if counters[depth]:
                        fmt = levels[depth - 1]
                        designation = extend(designation, fill(fmt.ref, n=format_counter(counters[depth], fmt.counter)),
                                             textual=False)
                fmt = levels[level - 1]
                label = fill(fmt.label, n=format_counter(counters[level], fmt.counter), path=designation)
            anchor = self._anchor(section.identifier)
            self._register(section.identifier, _Target(designation, anchor))
            out.append(replace(section, label=label, designation=designation, anchor=anchor))
        return out

    def _title_text(self, title: tuple[Inline, ...]) -> str:
        """A heading's text, as the ``none`` scheme designates it (§13.3).
        Directives in it are resolved, except references, whose targets are
        not all known yet; a defined term shows as its term."""
        kept: list[Inline] = []
        for inline in title:
            if isinstance(inline, DefinitionSource):
                kept.append(Text(inline.term))
            elif not (isinstance(inline, DirectiveSource) and inline.directive.name == "ref"):
                kept.append(inline)
        return " ".join(plain_text(self._resolve_inlines(tuple(kept))).split())

    def _structure_section(self, section: Section) -> tuple[Block, ...]:
        paragraphs = 0
        previous: Paragraph | None = None
        out: list[Block] = []
        for block in section.blocks:
            if isinstance(block, Paragraph) and block.top_level:
                # Alternative paragraphs share a number, as sections do (§15.8).
                if previous is None or not _alternatives(previous.anchor_id, previous.condition,
                                                         block.anchor_id, block.condition):
                    paragraphs += 1
                previous = block
                block = self._number_paragraph(block, section, paragraphs)
            out.append(self._structure(block, section=section, depth=0))
        return tuple(out)

    def _number_paragraph(self, block: Paragraph, section: Section, number: int) -> Paragraph:
        numbering = self.style.paragraphs
        base = section.designation or ""
        if numbering.numbered:
            fmt = numbering.format
            n = format_counter(number, fmt.counter)
            designation = extend(base, fill(fmt.ref, n=n, section=base), textual=self.textual)
            label = fill(fmt.label, n=n, section=base, path=designation)
            target = _Target(designation, None)
        else:
            label, target = None, _Target(base, None, enumerated=False)
        anchor = self._anchor(block.anchor_id)
        if block.anchor_id:
            self._register(block.anchor_id, replace(target, anchor=anchor))
        return replace(block, label=label, anchor=anchor)

    def _structure(self, block: Block, *, section: Section | None, depth: int) -> Block:
        match block:
            case List():
                return self._structure_list(block, section=section, depth=depth,
                                            parent=section.designation if section else "")
            case Quote(blocks=blocks):
                return Quote(tuple(self._structure(child, section=section, depth=depth) for child in blocks))
            case DraftingNote(blocks=blocks):
                return DraftingNote(tuple(self._structure(child, section=section, depth=depth) for child in blocks),
                                    label=self.labels.drafting_note)
            case _:
                return block

    def _structure_list(self, block: List, *, section: Section | None, depth: int, parent: str) -> List:
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
        index = 0
        previous: ListItem | None = None
        for item in block.items:
            if previous is None or not _alternatives(previous.anchor_id, previous.condition,
                                                     item.anchor_id, item.condition):
                index += 1
            previous = item
            if fmt is not None:
                n = format_counter(index, fmt.counter)
                designation = extend(parent, fill(fmt.ref, n=n, section=base), textual=self.textual)
                label = fill(fmt.label, n=n, section=base, path=designation)
                target = _Target(designation, None)
            else:
                designation, label, target = parent, None, _Target(base, None, enumerated=False)
            anchor = self._anchor(item.anchor_id)
            if item.anchor_id and section is not None:
                self._register(item.anchor_id, replace(target, anchor=anchor))
            children = tuple(
                self._structure_list(child, section=section, depth=depth + 1, parent=designation)
                if isinstance(child, List) else self._structure(child, section=section, depth=depth + 1)
                for child in item.blocks
            )
            items.append(replace(item, blocks=children, label=label, anchor=anchor))
        return List(block.ordered, tuple(items), enumerated=fmt is not None)

    # -- blocks ---------------------------------------------------------------

    def _resolve_block(self, block: Block) -> Block:
        resolve = self._resolve_inlines
        match block:
            case Paragraph():
                return replace(block, inlines=resolve(block.inlines), condition_label=self._condition(block.condition))
            case List(items=items):
                return replace(block, items=tuple(
                    replace(item, blocks=tuple(self._resolve_block(child) for child in item.blocks),
                            condition_label=self._condition(item.condition))
                    for item in items
                ))
            case Quote(blocks=blocks):
                return Quote(tuple(self._resolve_block(child) for child in blocks))
            case DraftingNote(blocks=blocks):
                return replace(block, blocks=tuple(self._resolve_block(child) for child in blocks))
            case Table(header=header, rows=rows, align=align):
                return Table(tuple(resolve(cell) for cell in header),
                             tuple(tuple(resolve(cell) for cell in row) for row in rows), align)
            case CodeBlock():
                return block
            case _:
                return block

    def _condition(self, condition: str) -> str | None:
        if not condition:
            return None
        return fill_condition(self.labels.condition or "{condition}", condition)

    # -- inlines --------------------------------------------------------------

    def _resolve_inlines(self, inlines: tuple[Inline, ...]) -> tuple[Inline, ...]:
        out: list[Inline] = []
        for inline in inlines:
            resolved = self._resolve_inline(inline)
            if resolved is not None:
                out.append(resolved)
        return tuple(out)

    def _resolve_inline(self, inline: Inline) -> Inline | None:
        match inline:
            case DirectiveSource(directive=directive):
                return self._directive(directive)
            case DefinitionSource(children=children):
                identifier = self._definition_id(inline)
                return DefinedTerm(self._resolve_inlines(children),
                                   self._anchor(DEFINITION_ANCHOR_PREFIX + identifier),
                                   self.style.definitions.style)
            case Emphasis(children=children):
                return Emphasis(self._resolve_inlines(children))
            case Strong(children=children):
                return Strong(self._resolve_inlines(children))
            case Link(children=children) | Image(children=children):
                return replace(inline, children=self._resolve_inlines(children))
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
        text = self.style.references.format.replace("{designation}", target.designation)
        return CrossRef(text, target.anchor or "")

    def _term(self, directive: Directive, definition_id: str) -> Inline:
        term = self.result.definition_lookup.get(definition_id)
        if term is None:
            return FailureMarker(f"[UNDEFINED: {definition_id}]")
        text = directive.params.get("label") or self._plain_value(term)
        target = DEFINITION_ANCHOR_PREFIX + definition_id if definition_id in self.definition_ids else None
        return TermRef(text, target, self.style.definitions.term_style)

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
        return self._named("party", name, self.result.party_lookup, directive)

    def _side(self, directive: Directive, name: str) -> Inline:
        return self._named("side", name, self.result.side_lookup, directive)

    def _named(self, kind: str, name: str, lookup: dict[str, str], directive: Directive) -> Inline:
        """``{{party:}}`` and ``{{side:}}`` (§13.5): failure markers first — a
        label never hides one (§11.5) — then the label, then the display name
        the validator resolved (§3.6)."""
        if not IDENTIFIER_RE.fullmatch(name):
            return FailureMarker(f"[INVALID {kind.upper()}: {name}]")
        if name not in lookup:
            return FailureMarker(f"[UNKNOWN {kind.upper()}: {name}]")
        return Value(kind, directive.params.get("label") or self._plain_value(lookup[name]))

    def _duration(self, directive: Directive, value: str) -> Inline:
        unit = directive.params.get("unit")
        if not is_positive_numeric(value):
            return FailureMarker(f"[INVALID DURATION: {value}]")
        if unit not in DURATION_UNITS:
            return FailureMarker(f"[INVALID DURATION UNIT: {unit}]" if unit else "[INVALID DURATION UNIT]")
        return Value("duration", self.formatter.duration(value, unit))

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
        if isinstance(declared, dict) and declared.get("type") in _DECISION_TYPES:
            return invalid  # a decision question's id is not a blank (§15.2)
        if placeholder_type not in _VALUE_TYPES or placeholder_id in self.inconsistent_placeholders:
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
        return Blank(placeholder_id, self.style.placeholders.blank, prompt)

    def _attach(self, directive: Directive, attachment_id: str) -> Inline:
        title = self.result.attachment_lookup.get(attachment_id)
        if title is None:
            return FailureMarker(f"[UNKNOWN ATTACHMENT: {attachment_id}]")
        anchor = self.attachment_anchors.get(attachment_id)
        if anchor is not None:
            href = "#" + anchor
        else:
            declared = next((a for a in self.metadata.attachments if a.id == attachment_id), None)
            href = declared.file if declared and declared.file else None
        return Value("attach", directive.params.get("label") or self._plain_value(title), href)

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

    def _plain_value(self, text: str) -> str:
        """A frontmatter string with its directives (placeholders, §3.10)
        resolved, as plain text."""
        if "{{" not in text:
            return text
        return plain_text(self._resolve_inlines(plain_inlines(text)))

    def _frontmatter(self, text: str) -> tuple[Inline, ...]:
        return self._resolve_inlines(plain_inlines(text))

    def _header(self) -> tuple[tuple[str, tuple[Inline, ...]], ...]:
        rows: list[tuple[str, tuple[Inline, ...]]] = []
        metadata = self.metadata
        if self.style.title_block.effective_date and metadata.effective_date:
            rows.append((self.labels.effective_date or "", self._date_or_text(metadata.effective_date)))
        if self.style.title_block.version and metadata.version:
            rows.append((self.labels.version or "", self._frontmatter(metadata.version)))
        return tuple(rows)

    def _date_or_text(self, value: str) -> tuple[Inline, ...]:
        if is_valid_iso_date(value):
            return (Value("date", self.formatter.date(value)),)
        return self._frontmatter(value)

    def _sides(self) -> tuple[SideInfo, ...]:
        sides: list[SideInfo] = []
        for side in self.metadata.sides:
            parties: list[PartyInfo] = []
            for party in side.parties:
                details: list[tuple[str, tuple[Inline, ...]]] = []
                if party.identification_number:
                    details.append((self.labels.identification_number or "", self._frontmatter(party.identification_number)))
                if party.date_of_birth:
                    details.append((self.labels.date_of_birth or "", self._date_or_text(party.date_of_birth)))
                if party.address:
                    details.append((self.labels.address or "", self._frontmatter(party.address)))
                for custom in party.custom_fields:
                    details.append((custom.label, self._frontmatter(custom.value)))
                representatives = tuple(
                    (self._frontmatter(rep.name), self._frontmatter(rep.title)) for rep in party.representatives
                )
                parties.append(PartyInfo(
                    name=self._frontmatter(party.legal_name or party.label or party.name),
                    details=tuple(details),
                    representatives=representatives,
                ))
            label = self.result.side_lookup.get(side.name) or side.label or side.name
            sides.append(SideInfo(self._frontmatter(label), tuple(parties)))
        return tuple(sides)

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
        for side in self.metadata.sides:
            label = self._frontmatter(self.result.side_lookup.get(side.name) or side.label or side.name)
            for party in side.parties:
                # Where signature blocks are generated, legal_name MUST appear (§3.6).
                blocks.append(SignatureParty(
                    side=label,
                    name=self._frontmatter(party.legal_name or party.label or party.name),
                    signatories=tuple(
                        (self._frontmatter(rep.name), self._frontmatter(rep.title)) for rep in party.representatives
                    ),
                ))
        return tuple(blocks)

    # -- diagnostics ----------------------------------------------------------

    def _warn(self, rule: str, message: str) -> None:
        diagnostic = Diagnostic(rule=rule, level="warning", message=message)
        if diagnostic not in self.diagnostics:
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


def _alternatives(first_id: str, first_condition: str, second_id: str, second_condition: str) -> bool:
    """True when two consecutive units are alternatives: they share an
    identifier and both are conditional (§15.4). Mutual exclusion itself is
    checked by the validator."""
    return bool(first_id) and first_id == second_id and bool(first_condition) and bool(second_condition)


def fill_condition(template: str, value: str, *, key: str = "condition") -> str:
    return template.replace("{" + key + "}", value)


def _has_condition(block: Block) -> bool:
    match block:
        case Paragraph(condition=condition):
            return bool(condition)
        case List(items=items):
            return any(item.condition or any(_has_condition(child) for child in item.blocks) for item in items)
        case Quote(blocks=blocks) | DraftingNote(blocks=blocks):
            return isinstance(block, DraftingNote) or any(_has_condition(child) for child in blocks)
    return False


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

