"""Download the OOXML schemas the DOCX tests validate against.

Usage: ``python tests/schemas/fetch.py [DIRECTORY]`` (default: ``tests/schemas/ooxml``,
which is ignored by git). Standard library only. See tests/schemas/README.md for what
is fetched and why it is not stored in the repository.

Every download is checked against the SHA-256 it had when the schemas were last
reviewed; a different file stops the script, so a changed upstream is noticed rather
than trusted.
"""
from __future__ import annotations

import hashlib
import io
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

#: (url, sha256 of the download, the inner zip to open, the files to take from it)
SOURCES = (
    ("https://ecma-international.org/wp-content/uploads/ECMA-376-4_5th_edition_december_2016.zip",
     "bd25da1109f73762356596918bf5ff8b74a1331642dba5f1c1d1dfc6bed34ecd",
     "OfficeOpenXML-XMLSchema-Transitional.zip", None),
    ("https://ecma-international.org/wp-content/uploads/ECMA-376-2_5th_edition_december_2021.zip",
     "1d489dc491168ea1f9e9a59063acc8dd5f02b4ad1d21aa7ec19ba9a58d020c70",
     "OpenPackagingConventions-XMLSchema.zip", ("opc-contentTypes.xsd", "opc-relationships.xsd")),
)
XML_XSD = ("https://www.w3.org/2001/xml.xsd", "61960fb3131e38022caad5360e2f33a3382578ab3c80cd58bd74320ede61b20c")

#: wml.xsd imports the XML namespace without saying where it is; libxml2 needs it loaded first.
WRAPPER = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" elementFormDefault="qualified">
  <xs:import namespace="http://www.w3.org/XML/1998/namespace" schemaLocation="xml.xsd"/>
  <xs:import namespace="http://schemas.openxmlformats.org/wordprocessingml/2006/main" schemaLocation="wml.xsd"/>
</xs:schema>
"""


def download(url: str, sha256: str) -> bytes:
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URLs
                data = response.read()
            break
        except OSError as error:
            last = error
            time.sleep(2 * (attempt + 1))
    else:
        raise SystemExit(f"cannot download {url}: {last}")
    found = hashlib.sha256(data).hexdigest()
    if found != sha256:
        raise SystemExit(f"{url}: expected SHA-256 {sha256}, got {found}; review the change before updating this script")
    return data


def fetch(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for url, sha256, inner, wanted in SOURCES:
        with zipfile.ZipFile(io.BytesIO(download(url, sha256))) as outer, \
                zipfile.ZipFile(io.BytesIO(outer.read(inner))) as schemas:
            for name in schemas.namelist():
                if name.endswith(".xsd") and (wanted is None or name in wanted):
                    (target / Path(name).name).write_bytes(schemas.read(name))
    (target / "xml.xsd").write_bytes(download(*XML_XSD))
    (target / "ld-wml.xsd").write_text(WRAPPER, encoding="utf-8")
    print(f"schemas written to {target}")


if __name__ == "__main__":
    fetch(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "ooxml")
