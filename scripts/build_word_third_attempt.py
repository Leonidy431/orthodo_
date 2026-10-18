#!/usr/bin/env python3
"""Build the third-attempt editorial document using only Python's standard library."""

import argparse
import hashlib
import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/third-attempt/common-prayers-three-columns.md"
OUTPUT = ROOT / "deliverables/third-attempt/common-prayers-three-columns.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("w", W)
ET.register_namespace("r", R)
HEADER = ["Ελληνικά", "English", "Български"]
TITLE = [
    "Гаврилов Леонид Геннадьевич",
    "Основные молитвы и поведение в храме",
    "Кипр, Армения, Болгария: сравнительное исследование и трёхъязычная памятка",
    "Монография",
    "2026",
    "Рабочая исследовательская редакция",
]


def element(parent, name, attrs=None, text=None):
    node = ET.SubElement(parent, f"{{{W}}}{name}", {
        f"{{{W}}}{key}": str(value) for key, value in (attrs or {}).items()
    })
    node.text = text
    return node


def xml_bytes(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse(source):
    blocks = []
    rows = []

    def finish_table():
        if rows:
            if rows[0] != HEADER or len(rows) < 2:
                raise ValueError("A three-language table must have a header and content")
            blocks.append(("table", list(rows)))
            rows.clear()

    for line in source.splitlines():
        line = line.strip()
        if line.startswith("|"):
            if not line.endswith("|"):
                raise ValueError("Unclosed table row")
            cells = [cell.strip() for cell in line[1:-1].split("|")]
            if len(cells) != 3 or any(not cell for cell in cells):
                raise ValueError("Exactly three nonempty cells are required")
            if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                rows.append(cells)
            continue
        finish_table()
        if not line:
            continue
        if line == "<!-- pagebreak -->":
            blocks.append(("pagebreak", ""))
        elif line.startswith("#"):
            match = re.fullmatch(r"(#{1,3}) (.+)", line)
            if not match:
                raise ValueError("Unsupported heading")
            blocks.append((f"h{len(match[1])}", match[2]))
        else:
            blocks.append(("p", line))
    finish_table()
    if [text for kind, text in blocks[:6]] != TITLE:
        raise ValueError("The six required title-page lines must be preserved")
    if not any(kind == "table" for kind, _ in blocks):
        raise ValueError("No parallel table")
    return blocks


def build(blocks):
    document = ET.Element(f"{{{W}}}document")
    body = element(document, "body")
    relationships = ET.Element(f"{{{P}}}Relationships")
    ET.SubElement(relationships, f"{{{P}}}Relationship", {
        "Id": "footer", "Type": R + "/footer", "Target": "footer1.xml",
    })
    expected = []

    def paragraph(parent, text, style=None, language="ru-RU"):
        expected.append(text)
        p = element(parent, "p")
        props = element(p, "pPr")
        element(props, "spacing", {"after": 120})
        if style:
            element(props, "keepNext")
            element(props, "outlineLvl", {"val": int(style[-1]) - 1})
        # Explicit run properties keep the document independent of installed templates.
        for part in re.split(r"(https://[^\s<>]+)", text):
            if not part:
                continue
            run_parent = p
            if part.startswith("https://"):
                rel_id = f"url{len(relationships)}"
                ET.SubElement(relationships, f"{{{P}}}Relationship", {
                    "Id": rel_id, "Type": R + "/hyperlink",
                    "Target": part, "TargetMode": "External",
                })
                run_parent = element(p, "hyperlink")
                run_parent.set(f"{{{R}}}id", rel_id)
            run = element(run_parent, "r")
            rprops = element(run, "rPr")
            element(rprops, "rFonts", {
                "ascii": "DejaVu Sans", "hAnsi": "DejaVu Sans",
                "eastAsia": "DejaVu Sans", "cs": "DejaVu Sans",
            })
            element(rprops, "lang", {"val": language})
            element(rprops, "sz", {"val": 28 if style else 22})
            if style:
                element(rprops, "b")
            node = element(run, "t", text=part)
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return p

    for kind, value in blocks:
        if kind == "pagebreak":
            element(element(element(body, "p"), "r"), "br", {"type": "page"})
        elif kind == "table":
            table = element(body, "tbl")
            props = element(table, "tblPr")
            element(props, "tblW", {"w": 14400, "type": "dxa"})
            element(props, "tblLayout", {"type": "fixed"})
            borders = element(props, "tblBorders")
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                element(borders, side, {"val": "single", "sz": 4, "color": "808080"})
            grid = element(table, "tblGrid")
            for _ in range(3):
                element(grid, "gridCol", {"w": 4800})
            for index, cells in enumerate(value):
                row = element(table, "tr")
                if index == 0:
                    element(element(row, "trPr"), "tblHeader", {"val": "true"})
                # No fixed row heights or cantSplit: long content can flow across pages.
                for text, language in zip(cells, ("el-GR", "en-US", "bg-BG")):
                    cell = element(row, "tc")
                    cell_props = element(cell, "tcPr")
                    element(cell_props, "tcW", {"w": 4800, "type": "dxa"})
                    paragraph(cell, text, language=language)
        else:
            paragraph(body, value, kind if kind.startswith("h") else None)
    section = element(body, "sectPr")
    element(section, "footerReference", {"type": "default"}).set(f"{{{R}}}id", "footer")
    element(section, "pgSz", {"w": 16838, "h": 11906, "orient": "landscape"})
    element(section, "pgMar", {
        "top": 850, "right": 1219, "bottom": 850, "left": 1219,
        "header": 400, "footer": 400, "gutter": 0,
    })
    footer = ET.Element(f"{{{W}}}ftr")
    p = element(footer, "p")
    element(element(p, "pPr"), "jc", {"val": "center"})
    element(p, "fldSimple", {"instr": "PAGE"})
    types = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    for extension, content_type in (
        ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
        ("xml", "application/xml"),
    ):
        ET.SubElement(types, "Default", Extension=extension, ContentType=content_type)
    for part, content_type in (
        ("document", "document.main"),
        ("footer1", "footer"),
    ):
        ET.SubElement(types, "Override", PartName=f"/word/{part}.xml",
                      ContentType=f"application/vnd.openxmlformats-officedocument.wordprocessingml.{content_type}+xml")
    package_rels = ET.Element(f"{{{P}}}Relationships")
    ET.SubElement(package_rels, f"{{{P}}}Relationship", {
        "Id": "document", "Type": R + "/officeDocument", "Target": "word/document.xml",
    })
    parts = {
        "[Content_Types].xml": xml_bytes(types),
        "_rels/.rels": xml_bytes(package_rels),
        "word/document.xml": xml_bytes(document),
        "word/_rels/document.xml.rels": xml_bytes(relationships),
        "word/footer1.xml": xml_bytes(footer),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue(), expected


def validate(content, expected):
    ns = {"w": W}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC error")
        for name in archive.namelist():
            ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read("word/document.xml"))
        actual = [
            "".join(p.itertext())
            for p in root.findall(".//w:p", ns)
            if p.findall(".//w:t", ns)
        ]
        if actual != expected:
            raise ValueError("Source text is missing, truncated or reordered")
        tables = root.findall(".//w:tbl", ns)
        if not tables:
            raise ValueError("Missing table")
        for table in tables:
            for row in table.findall("w:tr", ns):
                if len(row.findall("w:tc", ns)) != 3:
                    raise ValueError("Expected three cells in every table row")
            if table.find("w:tr/w:trPr/w:tblHeader", ns) is None:
                raise ValueError("Missing repeating header")
            if [col.get(f"{{{W}}}w") for col in table.findall("w:tblGrid/w:gridCol", ns)] != ["4800"] * 3:
                raise ValueError("Unequal columns")
        size = root.find(".//w:sectPr/w:pgSz", ns)
        if size is None or size.get(f"{{{W}}}orient") != "landscape":
            raise ValueError("Missing landscape page setting")
        footer = ET.fromstring(archive.read("word/footer1.xml"))
        if footer.find(".//w:fldSimple[@w:instr='PAGE']", ns) is None:
            raise ValueError("Missing page-number field")
    if not content:
        raise ValueError("Empty document")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--write", action="store_true", help="Write the validated DOCX")
    modes.add_argument("--check", action="store_true", help="Check existing DOCX against source")
    args = parser.parse_args()
    content, expected = build(parse(args.source.read_text(encoding="utf-8")))
    validate(content, expected)
    if args.check:
        existing = args.output.read_bytes()
        validate(existing, expected)
        if existing != content:
            raise ValueError("DOCX is not the reproducible build of this source")
    elif args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(content)
    mode = "checked" if args.check else "written" if args.write else "dry-run; not written"
    print(f"{mode}: {args.output}; {len(content)} bytes; SHA-256 {hashlib.sha256(content).hexdigest()}")
    print("ZIP/XML, source text, three equal cells, repeating headers and page field: OK")
    print("No visual rendering performed.")


if __name__ == "__main__":
    main()
