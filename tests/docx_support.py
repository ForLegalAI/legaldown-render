"""Helpers for the DOCX tests: reading a package, validating its parts against the
ISO/IEC 29500-4 transitional schemas, and running LibreOffice over it.

The schemas are not in the repository (tests/schemas/README.md): ``python tests/schemas/fetch.py``
downloads them. The tests that need them, or LibreOffice, skip when they are missing, unless
``LEGALDOWN_REQUIRE_DOCX_TOOLS`` is set (the CI job sets it), when a missing tool is a failure.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
UNO_EXPORT = ROOT / "lo" / "uno_export.py"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


def tool_missing(reason: str) -> None:
    """Skip, or fail when the environment promises the tools (CI): a job that sets
    ``LEGALDOWN_REQUIRE_DOCX_TOOLS`` must not pass by skipping what it exists to run."""
    if os.environ.get("LEGALDOWN_REQUIRE_DOCX_TOOLS"):
        pytest.fail(f"required tool missing: {reason}")
    pytest.skip(reason)


# -- Reading a package -------------------------------------------------------------------


def parts(data: bytes) -> dict[str, bytes]:
    """Every entry of the ZIP *data*, by name, in archive order."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def texts(root: ET.Element) -> list[str]:
    """The text of every paragraph under *root*, tabs as TAB and breaks as LF (the tab stops
    of a paragraph's properties are not text, so only the content of runs counts)."""
    lines = []
    for paragraph in root.iter(f"{W}p"):
        line = ""
        for run in paragraph.iter(f"{W}r"):
            for node in run:
                if node.tag == f"{W}t":
                    line += node.text or ""
                elif node.tag == f"{W}tab":
                    line += "\t"
                elif node.tag == f"{W}br":
                    line += "\n"
        lines.append(line)
    return lines


# -- Schemas -----------------------------------------------------------------------------


def schema_directory() -> Path | None:
    candidate = Path(os.environ.get("LEGALDOWN_OOXML_SCHEMAS") or ROOT / "schemas" / "ooxml")
    return candidate if (candidate / "ld-wml.xsd").is_file() else None


@cache
def _schemas() -> dict[str, object]:
    from lxml import etree

    directory = schema_directory()
    assert directory is not None
    return {name: etree.XMLSchema(etree.parse(str(directory / file)))
            for name, file in (("wml", "ld-wml.xsd"), ("types", "opc-contentTypes.xsd"),
                               ("rels", "opc-relationships.xsd"),
                               ("app", "shared-documentPropertiesExtended.xsd"))}


def require_schemas() -> None:
    """Skip (or fail in CI) unless lxml and the schemas are there."""
    try:
        import lxml  # noqa: F401
    except ImportError:
        tool_missing("lxml is not installed (a test dependency: pip install -e '.[dev]')")
    if schema_directory() is None:
        tool_missing("the OOXML schemas are not fetched (python tests/schemas/fetch.py)")


def schema_for(name: str) -> str | None:
    """Which schema validates the part *name*, or None when none is available (core.xml:
    its schema imports Dublin Core from the web)."""
    if name == "[Content_Types].xml":
        return "types"
    if name.endswith(".rels"):
        return "rels"
    if name == "docProps/app.xml":
        return "app"
    if name.startswith("word/") and name.endswith(".xml"):
        return "wml"
    return None


def well_formed(data: bytes) -> object:
    """The parsed XML of *data* (raising lxml's error if it is not well-formed), without
    resolving entities or touching the network."""
    from lxml import etree

    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))


def validation_errors(data: bytes) -> list[str]:
    """Everything wrong with the package *data*: a part that is not well-formed or does not
    validate against its schema, a relationship to a part that is not there, a part that has
    no content type. Empty when the package is sound."""
    from lxml import etree

    require_schemas()
    problems: list[str] = []
    contents = parts(data)
    for name, content in contents.items():
        try:
            root = well_formed(content)
        except etree.XMLSyntaxError as error:
            problems.append(f"{name}: not well-formed: {error}")
            continue
        schema_name = schema_for(name)
        if schema_name is not None:
            schema = _schemas()[schema_name]
            if not schema.validate(root):  # type: ignore[attr-defined]
                problems += [f"{name}: {entry.message} (line {entry.line})"
                             for entry in schema.error_log]  # type: ignore[attr-defined]
    problems += _package_problems(contents)
    return problems


def _package_problems(contents: dict[str, bytes]) -> list[str]:
    from lxml import etree

    problems: list[str] = []
    names = list(contents)
    if names[:1] != ["[Content_Types].xml"]:
        problems.append("[Content_Types].xml is not the first entry")
    types = etree.fromstring(contents["[Content_Types].xml"])
    defaults = {entry.get("Extension") for entry in types if entry.tag.endswith("}Default")}
    overrides = {entry.get("PartName") for entry in types if entry.tag.endswith("}Override")}
    for name in names:
        if name != "[Content_Types].xml" and f"/{name}" not in overrides and name.rpartition(".")[2] not in defaults:
            problems.append(f"{name}: no content type")
    for override in overrides:
        if override.lstrip("/") not in contents:
            problems.append(f"{override}: a content type for a part that is not there")
    for name, content in contents.items():
        if not name.endswith(".rels"):
            continue
        owner_folder = posixpath.dirname(posixpath.dirname(name))
        for relationship in etree.fromstring(content):
            if relationship.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(posixpath.join(owner_folder, relationship.get("Target")))
            if target not in contents:
                problems.append(f"{name}: the target {relationship.get('Target')} is not a part")
    return problems


# -- LibreOffice ---------------------------------------------------------------------------


@cache
def system_python() -> str | None:
    """A Python that can ``import uno`` (python3-uno installs it for the system's Python, not
    for a virtual environment's), or None."""
    for candidate in ("/usr/bin/python3", shutil.which("python3"), shutil.which("python")):
        if candidate and Path(candidate).is_file():
            done = subprocess.run([candidate, "-c", "import uno"], capture_output=True, check=False)
            if done.returncode == 0:
                return candidate
    return None


def require_libreoffice() -> str:
    """The Python to run the UNO script with, or skip (fail in CI) when LibreOffice or its Python bindings are missing."""
    if shutil.which("soffice") is None:
        tool_missing("LibreOffice (soffice) is not installed")
    python = system_python()
    if python is None:
        tool_missing("no Python with the uno module (python3-uno) was found")
    assert python is not None
    return python


class Exported:
    """What LibreOffice says about a document once its fields are updated."""

    def __init__(self, text: str, header: str, footer: str, pages: int) -> None:
        self.text = text
        self.header = header
        self.footer = footer
        self.pages = pages

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()


def repackage(data: bytes, name: str, change: Callable[[str], str]) -> bytes:
    """The package *data* with the text of the part *name* changed by *change*: for a test that
    the checks catch what they are meant to."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for part, content in parts(data).items():
            archive.writestr(part, change(content.decode("utf-8")).encode("utf-8") if part == name else content)
    return buffer.getvalue()


def libreoffice_export(documents: dict[str, bytes], *, update: bool = True) -> dict[str, Exported]:
    """Open each document (name to DOCX bytes) in one LibreOffice instance, update its fields
    and indexes (unless *update* is False: the fields as the file caches them), and return its
    text, header, footer and page count."""
    python = require_libreoffice()
    with tempfile.TemporaryDirectory(prefix="ld-lo-") as folder:
        out = Path(folder, "out")
        out.mkdir()
        for name, data in documents.items():
            Path(folder, f"{name}.docx").write_bytes(data)
        done = subprocess.run([python, str(UNO_EXPORT), *([] if update else ["--no-update"]), str(out),
                               *(str(Path(folder, f"{n}.docx")) for n in documents)],
                              capture_output=True, text=True, timeout=300, check=False)
        if done.returncode != 0:
            pytest.fail(f"LibreOffice export failed:\n{done.stdout}\n{done.stderr}")
        result = {}
        for name in documents:
            text = Path(out, f"{name}.txt").read_text(encoding="utf-8-sig")
            info = json.loads(Path(out, f"{name}.json").read_text(encoding="utf-8"))
            result[name] = Exported(text, info["header"], info["footer"], info["pages"])
        return result
