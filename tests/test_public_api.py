"""The public API, and the render tree as a host reads it (issue #13,
docs/render-tree.md)."""
from __future__ import annotations

import dataclasses
import importlib
import typing

import pytest
from conftest import DOCUMENTS

import legaldown_render
from legaldown_render import RenderTree, render
from legaldown_render import tree as public_tree

PUBLIC_MODULES = ("legaldown_render", "legaldown_render.tree", "legaldown_render.style", "legaldown_render.writers")

#: The fields of every public node, in order. A change here is a change to the
#: public tree: list it in the release notes (docs/roadmap.md) and, for a
#: rename or a removal, follow docs/render-tree.md.
FIELDS = {
    "RenderTree": ('title', 'subtitle', 'language', 'preamble', 'sections', 'is_template', 'locale', 'header', 'sides', 'attachments', 'attachments_label', 'signatures', 'signature_labels', 'contents', 'contents_label', 'attachments_anchor', 'colon', 'formats', 'version', 'effective_date'),
    "NumberingFormats": ('scheme', 'headings', 'enumerated', 'renumber', 'paragraphs', 'reference'),
    "LevelFormat": ('counter', 'label', 'ref'),
    "ContentsEntry": ('level', 'label', 'title', 'anchor', 'condition_label', 'unit'),
    "SideInfo": ('label', 'parties'),
    "PartyInfo": ('name', 'details'),
    "AttachmentPart": ('id', 'title', 'file', 'anchor', 'condition_label'),
    "SignatureParty": ('side', 'name', 'signatories'),
    "Section": ('level', 'title', 'identifier', 'condition', 'blocks', 'label', 'designation', 'anchor', 'condition_label', 'unit', 'number', 'parent_unit', 'shares_number_with', 'line'),
    "Paragraph": ('inlines', 'anchor_id', 'condition', 'top_level', 'label', 'anchor', 'condition_label', 'unit', 'number', 'parent_unit', 'shares_number_with', 'line'),
    "List": ('ordered', 'items', 'enumerated', 'family', 'depth', 'format'),
    "ListItem": ('blocks', 'anchor_id', 'condition', 'label', 'anchor', 'condition_label', 'unit', 'number', 'parent_unit', 'shares_number_with', 'line'),
    "Quote": ('blocks', 'line'),
    "DraftingNote": ('blocks', 'label', 'line'),
    "CodeBlock": ('text', 'info', 'line'),
    "Table": ('header', 'rows', 'align', 'line'),
    "Rule": (),
    "Text": ('text',),
    "SoftBreak": (),
    "HardBreak": (),
    "Emphasis": ('children',),
    "Strong": ('children',),
    "Code": ('text',),
    "Link": ('href', 'children', 'title'),
    "Image": ('src', 'children', 'title'),
    "CrossRef": ('text', 'target', 'target_unit', 'section_unit'),
    "TermRef": ('text', 'target', 'style'),
    "DefinedTerm": ('children', 'anchor', 'style', 'defining'),
    "Value": ('kind', 'text', 'href'),
    "Blank": ('id', 'text', 'prompt'),
    "Choice": ('question', 'options', 'separator'),
    "FailureMarker": ('text',),
}


@pytest.mark.parametrize("module", PUBLIC_MODULES)
def test_every_public_name_exists(module: str) -> None:
    loaded = importlib.import_module(module)
    missing = [name for name in loaded.__all__ if not hasattr(loaded, name)]
    assert not missing
    assert len(set(loaded.__all__)) == len(loaded.__all__)


def test_render_tree_is_public_at_the_top_level() -> None:
    assert legaldown_render.RenderTree is public_tree.RenderTree
    assert typing.get_type_hints(legaldown_render.RenderResult)["tree"] is RenderTree


def test_the_node_types_are_public_and_the_source_nodes_are_not() -> None:
    names = set(public_tree.__all__)
    resolved = set(typing.get_args(public_tree.ResolvedInline))
    assert {cls.__name__ for cls in resolved} <= names
    assert {cls.__name__ for cls in typing.get_args(public_tree.Block)} <= names
    # Inline is ResolvedInline plus the source nodes, which stay internal.
    assert set(typing.get_args(public_tree.Inline)) - resolved == set(public_tree.SOURCE_INLINES)
    assert not {cls.__name__ for cls in public_tree.SOURCE_INLINES} & names
    assert "Inline" not in names


def test_every_class_a_public_node_holds_is_public() -> None:
    def classes(hint: object) -> set[type]:
        if isinstance(hint, type):
            return {hint}
        return set().union(*(classes(arg) for arg in typing.get_args(hint))) if typing.get_args(hint) else set()

    public = {getattr(public_tree, name) for name in public_tree.__all__ if isinstance(getattr(public_tree, name), type)}
    builtins = {str, int, bool, tuple, dict, type(None)}
    for node in public:
        for hint in typing.get_type_hints(node).values():
            leaked = classes(hint) - public - builtins - set(public_tree.SOURCE_INLINES)
            assert not leaked, (node.__name__, leaked)


def test_the_fields_of_the_public_nodes() -> None:
    nodes = {name: getattr(public_tree, name) for name in public_tree.__all__
             if dataclasses.is_dataclass(getattr(public_tree, name))}
    assert {name: tuple(f.name for f in dataclasses.fields(node)) for name, node in nodes.items()} == FIELDS


def test_rendered_trees_are_resolved() -> None:
    for name in ("features.lgd", "template.lgd"):
        tree = render((DOCUMENTS / name).read_text(encoding="utf-8"), format="text").tree
        assert public_tree.is_resolved(tree)
        public_tree.assert_resolved(tree)
        assert all(isinstance(inline, public_tree.ResolvedInline) for inline in public_tree.iter_tree_inlines(tree))


def test_an_unresolved_tree_is_reported() -> None:
    from legaldown.syntax import iter_directives

    from legaldown_render import InternalError

    result = render("# A\n\nText.\n", format="text")
    directive = next(iter(iter_directives("{{party: acme}}")))
    # The source node is internal; a host never builds one. It is used here only to break the invariant.
    paragraph = public_tree.Paragraph(inlines=(public_tree.DirectiveSource(directive),))
    section = dataclasses.replace(result.tree.sections[0], blocks=(paragraph,))
    unresolved = dataclasses.replace(result.tree, sections=(section,))
    assert not public_tree.is_resolved(unresolved)
    with pytest.raises(InternalError):
        public_tree.assert_resolved(unresolved)


class _HostWriter:
    """A writer written against the public API only, as a host's would be:
    it draws what the resolver computed and knows no LegalDown rule."""

    def __init__(self) -> None:
        self.anchors: set[str] = set()
        self.targets: set[str] = set()

    def document(self, tree: RenderTree) -> str:
        lines = [public_tree.plain_text(tree.title)]
        lines += [self.block(block) for block in tree.preamble]
        for section in tree.sections:
            self._anchor(section.anchor)
            lines.append(f"{'#' * section.level} {section.label or ''} {public_tree.plain_text(section.title)}".strip())
            lines += [self.block(block) for block in section.blocks]
        for attachment in tree.attachments:
            self._anchor(attachment.anchor)
            lines.append(public_tree.plain_text(attachment.title))
        self._anchor(tree.attachments_anchor)
        return "\n".join(lines)

    def _anchor(self, anchor: str | None) -> None:
        if anchor:
            self.anchors.add(anchor)

    def block(self, block: public_tree.Block) -> str:
        match block:
            case public_tree.Paragraph(inlines=inlines, label=label, anchor=anchor):
                self._anchor(anchor)
                return f"{label + ' ' if label else ''}{self.inlines(inlines)}"
            case public_tree.List(items=items):
                out = []
                for item in items:
                    self._anchor(item.anchor)
                    out.append(f"{item.label or '-'} " + "\n".join(self.block(child) for child in item.blocks))
                return "\n".join(out)
            case public_tree.Quote(blocks=blocks) | public_tree.DraftingNote(blocks=blocks):
                return "\n".join("> " + self.block(child) for child in blocks)
            case public_tree.Table(header=header, rows=rows):
                return "\n".join(" | ".join(self.inlines(cell) for cell in row) for row in (header, *rows))
            case public_tree.CodeBlock(text=text):
                return text
            case _:
                return ""

    def inlines(self, inlines: tuple[public_tree.ResolvedInline, ...]) -> str:
        out = []
        for inline in inlines:
            match inline:
                case public_tree.CrossRef(text=text, target=target):
                    if target:
                        self.targets.add(target)
                    out.append(text)
                case public_tree.TermRef(text=text, target=target):
                    if target:
                        self.targets.add(target)
                    out.append(text)
                case public_tree.DefinedTerm(children=children, anchor=anchor):
                    self._anchor(anchor)
                    out.append(self.inlines(children))
                case public_tree.Value(href=href, text=text):
                    if href and href.startswith("#"):
                        self.targets.add(href[1:])
                    out.append(text)
                case _:
                    out.append(public_tree.plain_text((inline,)))
        return "".join(out)


def test_a_host_writer_draws_what_the_resolver_computed() -> None:
    source = (DOCUMENTS / "features.lgd").read_text(encoding="utf-8")
    result = render(source, format="text")
    writer = _HostWriter()
    drawn = writer.document(result.tree)
    # Labels, reference text and failure markers are in the tree: the host
    # draws the same numbers and markers as the built-in plain-text writer.
    for section in result.tree.sections:
        if section.label:
            assert section.label in result.output
            assert section.label in drawn
    markers = [inline.text for inline in public_tree.iter_tree_inlines(result.tree)
               if isinstance(inline, public_tree.FailureMarker)]
    assert markers
    for marker in markers:
        assert marker in drawn and marker in result.output
    # Every link target is an anchor of the same tree.
    assert writer.targets
    assert writer.targets <= writer.anchors
