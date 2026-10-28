"""The DOCX writer (§13.6): a resolved tree as a WordprocessingML package.

It writes OOXML directly, with the standard library only (``zipfile`` and
``xml.etree``): docs/decisions/0008-docx-word-native.md says why. Like every writer it
contains no LegalDown rules (docs/decisions/0003); it lays out what resolution computed
and emits no diagnostics. The package has the parts a Word document needs and nothing that
varies between two renders of one document.

Layout of this package:

* :mod:`.ooxml` -- namespaces, the sanitiser, element, run, field, bookmark and hyperlink builders;
* :mod:`.package` -- parts, relationships, content types and the deterministic ZIP;
* :mod:`.styles`, :mod:`.settings`, :mod:`.headers`, :mod:`.properties` -- the parts that come from the style template;
* :mod:`.numbering` -- ``numbering.xml``, from the plan's numbering definitions;
* :mod:`.body` and :mod:`.runs` -- the body of the document and its inlines, laid out as the plan says;
* :mod:`.matter` -- the front and back matter: header rows and parties, the table of contents, the attachments and
  the signature blocks.
"""
from __future__ import annotations

from collections.abc import Mapping

from ...resolve.docx_plan import DocxPlan, plan_docx
from ...style import Style
from ...style.docx import DocxSettings, docx_settings
from ...tree import RenderTree
from . import package as pkg
from .body import BodyWriter, document_title
from .headers import build_footer, build_header
from .matter import MatterSettings
from .numbering import numbering_part
from .ooxml import Bookmarks, el, serialize
from .properties import build_app, build_core
from .settings import build_settings
from .styles import build_styles, language_tag

__all__ = ["DocxWriter"]


class DocxWriter:
    """Writes a resolved tree as a ``.docx`` file.

    *style* is the style template (the DOCX settings are converted from it, see
    :func:`~legaldown_render.style.docx.docx_settings`, which raises
    :class:`~legaldown_render.errors.StyleError` for one Word cannot express);
    *generator* names the program in the document's properties. The running header and
    footer take ``{title}``, ``{version}`` and ``{date}`` from the tree (its title, and the
    ``version`` and ``effective_date`` the resolver formatted as the title block shows them).
    """

    extension = "docx"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def __init__(self, style: Style, *, generator: str = "", settings: DocxSettings | None = None) -> None:
        self.style = style
        self.generator = generator or "legaldown-render"
        self.settings = settings if settings is not None else docx_settings(style)

    def write(self, tree: RenderTree, plan: DocxPlan | None = None) -> bytes:
        """The ``.docx`` file for *tree*, numbered, bookmarked and cross-referenced as *plan* says. Without
        one the tree is planned here (:func:`~legaldown_render.resolve.docx_plan.plan_docx`), whose diagnostics
        are then lost: :func:`~legaldown_render.render` plans first and reports them."""
        if plan is None:
            plan = plan_docx(tree, text_switch=self.settings.reference_text_switch)[0]
        settings = self.settings
        language = language_tag(tree.language, self.style.locale)
        title = document_title(tree)
        values: Mapping[str, str] = {"title": title, "version": tree.version, "date": tree.effective_date}
        text_width = settings.page.text_width

        package = pkg.Package()
        package.relationships.add(pkg.REL_OFFICE_DOCUMENT, "word/document.xml")
        package.relationships.add(pkg.REL_CORE_PROPERTIES, "docProps/core.xml")
        package.relationships.add(pkg.REL_EXTENDED_PROPERTIES, "docProps/app.xml")

        rels = pkg.Relationships()
        rels.add(pkg.REL_STYLES, "styles.xml")
        rels.add(pkg.REL_NUMBERING, "numbering.xml")
        rels.add(pkg.REL_SETTINGS, "settings.xml")
        header_id = footer_id = None
        page = self.style.page
        if page.header:
            header_id = rels.add(pkg.REL_HEADER, "header1.xml")
        if page.footer:
            footer_id = rels.add(pkg.REL_FOOTER, "footer1.xml")

        matter = MatterSettings(text_width, settings.points, self.style.contents.depth, self.style.attachments.separator)
        body = BodyWriter(tree, plan, Bookmarks(), rels, text_width, settings.link_color, matter).body(
            settings.page, header_id=header_id, footer_id=footer_id)
        document = el("document", None, body)
        package.add("docProps/core.xml", pkg.CT_CORE_PROPERTIES, build_core(title, language))
        package.add("docProps/app.xml", pkg.CT_EXTENDED_PROPERTIES, build_app(self.generator))
        package.add("word/document.xml", pkg.CT_DOCUMENT, serialize(document), rels)
        package.add("word/styles.xml", pkg.CT_STYLES, build_styles(self.style, settings, language,
                                                                   heading_num_id=plan.heading_num_id))
        package.add("word/numbering.xml", pkg.CT_NUMBERING, numbering_part(plan))
        package.add("word/settings.xml", pkg.CT_SETTINGS,
                    build_settings(update_fields=settings.update_fields_on_open))
        if header_id:
            package.add("word/header1.xml", pkg.CT_HEADER, build_header(page.header, values, text_width))
        if footer_id:
            package.add("word/footer1.xml", pkg.CT_FOOTER, build_footer(page.footer, values, text_width))
        return package.to_bytes()
