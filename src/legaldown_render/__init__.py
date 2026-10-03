"""legaldown-render — the reference renderer for LegalDown.

Render a LegalDown document to HTML or plain text. Section numbers,
cross-references, defined terms, and formatted values are generated at render
time, under a style template (§13.7)::

    from legaldown_render import render

    result = render(open("contract.lgd").read(), format="html", style="default")
    for diagnostic in result.diagnostics:
        print(diagnostic.level, diagnostic.rule, diagnostic.message)
    open("contract.html", "w").write(result.output)

Parsing and Core validation come from legaldown-validator; this package
claims the Rendering conformance level (§17.3). Exact coverage:
https://github.com/ForLegalAI/legaldown-render/blob/main/CONFORMANCE.md
"""
from __future__ import annotations

from .api import RenderOptions, RenderResult, render, render_file
from .errors import DocumentError, InternalError, RenderError, RenderRefused
from .style import Style, StyleError, builtin_styles, dump_style, load_style

__version__ = "0.3.0"

#: The LegalDown specification version this renderer targets.
SPEC_VERSION = "0.2"

#: Conformance level per specification §17.
CONFORMANCE_LEVEL = "rendering"

__all__ = [
    "__version__",
    "SPEC_VERSION",
    "CONFORMANCE_LEVEL",
    "render",
    "render_file",
    "RenderOptions",
    "RenderResult",
    "Style",
    "StyleError",
    "load_style",
    "dump_style",
    "builtin_styles",
    "RenderError",
    "DocumentError",
    "RenderRefused",
    "InternalError",
]
