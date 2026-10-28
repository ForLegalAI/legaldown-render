#!/usr/bin/python3
"""Open DOCX files in headless LibreOffice through UNO, update every field and index,
and write what the document says: one ``NAME.txt`` per file (the body as plain text,
UTF-8, one line per paragraph, read from the document model: tests/lo/uno_text.py) and one
``NAME.json`` with the page count and the text of the running header and footer.

Run it with the system Python that has the ``uno`` module (``/usr/bin/python3`` once
``python3-uno`` is installed), **not** with the project's virtual environment:

    /usr/bin/python3 tests/lo/uno_export.py [--no-update] OUTDIR FILE.docx [FILE.docx ...]

The same LibreOffice instance opens every file, so a test module pays the start-up
once. Exit status 0 when every file was exported.

With ``--no-update`` the fields are left as the file caches them: what the export says
is then what the writer wrote (a document whose cached results were made wrong on
purpose shows the wrong results there, and the right ones only after the update, which
is how a round trip proves that LibreOffice computed the numbers and did not read them).
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
from uno_text import body_text


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


def _update(document: object) -> None:
    """Update every index and field, twice: page numbers settle after the first layout."""
    for _ in range(2):
        indexes = document.getDocumentIndexes()
        for position in range(indexes.getCount()):
            indexes.getByIndex(position).update()
        document.getTextFields().refresh()
        document.refresh()


def _page_texts(document: object) -> tuple[str, str]:
    """The text of the first page style's header and footer ("" when it has none)."""
    styles = document.getStyleFamilies().getByName("PageStyles")
    page = styles.getByName(document.getCurrentController().getViewCursor().PageStyleName)
    header = page.HeaderText.getString() if page.HeaderIsOn else ""
    footer = page.FooterText.getString() if page.FooterIsOn else ""
    return header, footer


def export(out_dir: str, files: list[str], *, update: bool = True) -> None:
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
        for path in files:
            path = os.path.abspath(path)
            stem = os.path.splitext(os.path.basename(path))[0]
            document = desktop.loadComponentFromURL(uno.systemPathToFileUrl(path), "_blank", 0,
                                                    (_value("Hidden", True),))
            if document is None:
                raise SystemExit(f"LibreOffice could not open {path}")
            try:
                if update:
                    _update(document)
                header, footer = _page_texts(document)
                with open(os.path.join(out_dir, f"{stem}.txt"), "w", encoding="utf-8", newline="") as handle:
                    handle.write(body_text(document))
                pages = document.getCurrentController().PageCount
                with open(os.path.join(out_dir, f"{stem}.json"), "w", encoding="utf-8") as handle:
                    json.dump({"header": header, "footer": footer, "pages": pages}, handle)
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
    arguments = [argument for argument in sys.argv[1:] if argument != "--no-update"]
    if len(arguments) < 2:
        raise SystemExit(__doc__)
    export(arguments[0], arguments[1:], update="--no-update" not in sys.argv[1:])
