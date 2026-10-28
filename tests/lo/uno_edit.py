#!/usr/bin/python3
"""Edit DOCX files in headless LibreOffice through UNO the way a lawyer does, update the fields, and write what
the document says: for each job, a paragraph is inserted before the first paragraph that holds some text (so that
it takes that paragraph's numbering and style, as pressing Enter at its start does), or that paragraph is deleted,
then every field and index is updated (the indexes too, so a table of contents lists what was inserted) and the body
is exported as plain text (``JOB.txt``, UTF-8).

Run it with the system Python that has the ``uno`` module, **not** with the project's virtual environment:

    /usr/bin/python3 tests/lo/uno_edit.py OUTDIR JOBS.json

``JOBS.json`` is a list of ``{"name": "job", "file": "/path/doc.docx", "edits": [["text of a paragraph", "new"], ...]}``;
an edit whose new text is ``null`` deletes the paragraph whose text is exactly that instead. An edit with a third entry,
``["text of a paragraph", "new", "text of another"]``, puts the new paragraph on the list and the level of the paragraph that
holds the third text, as pasting a copy of that item at this place does (a new paragraph otherwise takes the numbering of
the one it is inserted before).
One LibreOffice instance serves every job. Exit status 0 when every job was exported.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import uno  # type: ignore[import-not-found]  # noqa: I001 - provided by python3-uno, not by pip
from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK  # type: ignore[import-not-found]


def _value(name: str, value: object) -> PropertyValue:
    prop = PropertyValue()
    prop.Name, prop.Value = name, value
    return prop


def _connect(pipe: str, process: subprocess.Popen[bytes]) -> object:
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(120):
        if process.poll() is not None:
            raise SystemExit("soffice exited before it accepted a connection")
        try:
            return resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
        except Exception:  # not listening yet
            time.sleep(0.5)
    raise SystemExit("soffice did not accept a connection in time")


def _paragraph(document: object, match: str) -> object:
    """The first paragraph of the body that holds *match*."""
    paragraphs = document.getText().createEnumeration()
    while paragraphs.hasMoreElements():
        paragraph = paragraphs.nextElement()
        if (paragraph.supportsService("com.sun.star.text.Paragraph") and not paragraph.ParaStyleName.startswith("Contents")
                and match in paragraph.getString()):
            return paragraph
    raise SystemExit(f"no paragraph holds {match!r}")


def _edit(document: object, match: str, new: str | None, like: str | None = None) -> None:
    """Insert a paragraph with the text *new* before the first paragraph that holds *match*, on the list and level of the
    paragraph that holds *like* when that is given, or, when *new* is None, delete the first paragraph whose text is
    exactly *match* (with its break, as Delete at its end does; the last paragraph of the document with the break before
    it, so that no empty paragraph is left)."""
    text = document.getText()
    paragraphs = text.createEnumeration()
    while paragraphs.hasMoreElements():
        paragraph = paragraphs.nextElement()
        # A line of a table of contents holds the heading's words too: only the document's own paragraphs count.
        if not paragraph.supportsService("com.sun.star.text.Paragraph") or paragraph.ParaStyleName.startswith("Contents"):
            continue
        if new is None:
            if paragraph.getString() != match:
                continue
            cursor = text.createTextCursorByRange(paragraph.getStart())
            cursor.gotoEndOfParagraph(True)
            if not cursor.goRight(1, True):  # the last paragraph: take the break before it instead
                cursor = text.createTextCursorByRange(paragraph.getStart())
                cursor.goLeft(1, False)
                cursor.gotoEnd(True)
            cursor.setString("")
            return
        if match in paragraph.getString():
            cursor = text.createTextCursorByRange(paragraph.getStart())
            text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)
            cursor.goLeft(1, False)
            cursor.setString(new)
            if like is not None:
                copied = _paragraph(document, like)
                for name in ("NumberingRules", "ListId", "NumberingLevel"):
                    cursor.setPropertyValue(name, copied.getPropertyValue(name))
            return
    raise SystemExit(f"no paragraph {'is' if new is None else 'holds'} {match!r}")


def edit(out_dir: str, jobs: list[dict]) -> None:
    profile = tempfile.mkdtemp(prefix="lo-profile-")
    pipe = f"legaldown_{uuid.uuid4().hex}"
    process = subprocess.Popen(
        ["soffice", "--headless", "--invisible", "--norestore", "--nologo", "--nodefault",
         f"--accept=pipe,name={pipe};urp;", f"-env:UserInstallation=file://{profile}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    desktop = None
    try:
        context = _connect(pipe, process)
        desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        for job in jobs:
            document = desktop.loadComponentFromURL(uno.systemPathToFileUrl(os.path.abspath(job["file"])), "_blank", 0,
                                                    (_value("Hidden", True),))
            if document is None:
                raise SystemExit(f"LibreOffice could not open {job['file']}")
            try:
                for match, new, *like in job["edits"]:
                    _edit(document, match, new, *like)
                for _ in range(2):  # page numbers and indexes settle after the first layout
                    indexes = document.getDocumentIndexes()
                    for position in range(indexes.getCount()):
                        indexes.getByIndex(position).update()
                    document.getTextFields().refresh()
                    document.refresh()
                document.storeToURL(uno.systemPathToFileUrl(os.path.join(out_dir, f"{job['name']}.txt")),
                                    (_value("FilterName", "Text (encoded)"), _value("FilterOptions", "UTF8")))
            finally:
                document.close(True)
    finally:
        if desktop is not None:
            with contextlib.suppress(Exception):  # already gone
                desktop.terminate()
        time.sleep(0.5)
        process.kill()
        process.wait()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with open(sys.argv[2], encoding="utf-8") as handle:
        edit(sys.argv[1], json.load(handle))
