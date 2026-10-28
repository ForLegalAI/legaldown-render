# OOXML schemas for the DOCX tests

`tests/test_docx_validation.py` validates every part of the generated documents against the ISO/IEC
29500-4 (transitional) schemas. The schemas are **not stored in this repository**. Fetch them with

```
python tests/schemas/fetch.py            # writes tests/schemas/ooxml/, which git ignores
```

(standard library only; `LEGALDOWN_OOXML_SCHEMAS=DIR` points the tests at another copy). The tests
skip when they are missing, and fail instead when `LEGALDOWN_REQUIRE_DOCX_TOOLS` is set, as the `docx`
job of `.github/workflows/ci.yml` does after fetching them.

## What is fetched, and from where

| Files | Source | Used for |
|---|---|---|
| `wml.xsd` and the schemas it imports (`dml-*.xsd`, `shared-*.xsd`, ...) | `OfficeOpenXML-XMLSchema-Transitional.zip` in [ECMA-376 5th edition, Part 4](https://ecma-international.org/wp-content/uploads/ECMA-376-4_5th_edition_december_2016.zip) (the transitional schemas of ISO/IEC 29500-4:2016) | `document.xml`, `styles.xml`, `numbering.xml`, `settings.xml`, headers and footers; `app.xml` (`shared-documentPropertiesExtended.xsd`) |
| `opc-contentTypes.xsd`, `opc-relationships.xsd` | `OpenPackagingConventions-XMLSchema.zip` in [ECMA-376 5th edition, Part 2](https://ecma-international.org/wp-content/uploads/ECMA-376-2_5th_edition_december_2021.zip) | `[Content_Types].xml` and every `.rels` |
| `xml.xsd` | [W3C](https://www.w3.org/2001/xml.xsd) | `xml:space` and `xml:lang`, which `wml.xsd` imports without saying where they are |
| `ld-wml.xsd` | written by `fetch.py` (ours, a few lines) | a wrapper that loads `xml.xsd` before `wml.xsd`, as libxml2 needs |

Each download is checked against the SHA-256 recorded in `fetch.py`; a changed file stops the script.

**Not validated:** `docProps/core.xml`. Its schema (`opc-coreProperties.xsd`) imports the Dublin Core
schemas from `dublincore.org` over the network. The tests check that part's shape directly
(tests/test_docx_package.py). Everything else is validated; the package itself (content types for
every part, every relationship target present, `[Content_Types].xml` first) is checked in
`tests/docx_support.py`.

## Why they are fetched and not vendored

Committing them would need a licence that allows redistribution. None is clear, so they are fetched:

* The XSD files carry no licence or copyright header, and the standards' own text (the Part 4 and Part 2 PDFs
  in the same downloads) contains no redistribution grant that could be extracted from it.
* The schemas are published, free of charge, by Ecma International (ECMA-376) and, as ISO/IEC 29500, by
  ISO/IEC, whose webstore terms apply to that copy. Both are copyrighted standards texts. Ecma's general
  practice is to allow copying with the notice retained, but that was not confirmed here for these files, and
  the ISO/IEC terms differ.
* Another copy of the same schemas, bundled with a third-party tool whose licence forbids copying and
  distributing it, was available to the author. It was neither used nor copied: only the files fetched from
  the publishers are used.

If the maintainers clear the licence (for example by asking Ecma), the files can be committed here and
`fetch.py` dropped; nothing else depends on where they live.
