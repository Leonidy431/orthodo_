#!/usr/bin/env python3
"""Build the third-attempt editorial document using only Python's standard library."""

import argparse
import hashlib
import io
from pathlib import Path
import re
import subprocess
import unicodedata
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
FONT = "Liberation Serif"
ACCENT = "9B2424"
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


def parse(source, title=None, require_table=True):
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
    if [text for kind, text in blocks[:6]] != (title or TITLE):
        raise ValueError("The six required title-page lines must be preserved")
    if require_table and not any(kind == "table" for kind, _ in blocks):
        raise ValueError("No parallel table")
    return blocks


def initial(text):
    """Split one EL/EN/BG letter and its combining marks, without normalization."""
    if not text or not unicodedata.category(text[0]).startswith("L"):
        raise ValueError("Prayer must start with a letter, without decorative substitution")
    end = 1
    while end < len(text) and unicodedata.category(text[end]).startswith("M"):
        end += 1
    return text[:end], text[end:]


def font_coverage(text):
    """Check actual code points against the installed regular and bold font cmaps."""
    results = {}
    for variant in ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"):
        path = Path("/usr/share/fonts/truetype/liberation") / variant
        if not path.is_file():
            raise ValueError(f"Font missing: {path}")
        result = subprocess.run(
            ["fc-query", "--format=%{charset}", str(path)],
            check=True, capture_output=True, text=True, timeout=10,
        )
        ranges = []
        for item in result.stdout.split():
            bounds = item.split("-")
            ranges.append((int(bounds[0], 16), int(bounds[-1], 16)))
        missing = sorted({
            char for char in text if not char.isspace()
            and not any(start <= ord(char) <= end for start, end in ranges)
        })
        if missing:
            raise ValueError(f"{variant} lacks codepoints: {missing!r}")
        results[variant] = "all used codepoints covered; no shaping/rendering claim"
    return results


def build(blocks, landscape=True):
    document = ET.Element(f"{{{W}}}document")
    body = element(document, "body")
    relationships = ET.Element(f"{{{P}}}Relationships")
    ET.SubElement(relationships, f"{{{P}}}Relationship", {
        "Id": "footer", "Type": R + "/footer", "Target": "footer1.xml",
    })
    expected = []

    def paragraph(parent, text, style=None, language="ru-RU", cover=None, table_header=False):
        prayer = text.startswith("[prayer] ")
        if prayer:
            text = text[len("[prayer] "):]
        expected.append(text)
        p = element(parent, "p")
        props = element(p, "pPr")
        if style:
            element(props, "keepNext")
        if cover in (1, 5):
            borders = element(props, "pBdr")
            element(borders, "bottom", {"val": "single", "sz": 4, "space": 12, "color": ACCENT})
        element(props, "spacing", {
            "before": 650 if cover == 0 else 240 if cover is not None else 0,
            "after": 420 if cover is not None else 140,
            "line": 288, "lineRule": "auto",
        })
        if cover is not None:
            element(props, "jc", {"val": "center"})
        if style:
            element(props, "outlineLvl", {"val": int(style[-1]) - 1})
        # Explicit run properties keep the document independent of installed templates.
        chunks = [(part, False) for part in re.split(r"(https://[^\s<>]+)", text)]
        if prayer:
            first, rest = initial(text)
            chunks = [(first, True), (rest, False)]
        for part, is_initial in chunks:
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
                "ascii": FONT, "hAnsi": FONT, "eastAsia": FONT, "cs": FONT,
            })
            if style or table_header:
                element(rprops, "b")
            element(rprops, "color", {"val": ACCENT if style or table_header or is_initial else "000000"})
            element(rprops, "sz", {"val": 38 if is_initial else 34 if cover == 1 else 28 if style else 23})
            if part.startswith("https://"):
                element(rprops, "u", {"val": "single"})
            element(rprops, "lang", {"val": language})
            node = element(run, "t", text=part)
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return p

    for block_index, (kind, value) in enumerate(blocks):
        if kind == "pagebreak":
            element(element(element(body, "p"), "r"), "br", {"type": "page"})
        elif kind == "table":
            table = element(body, "tbl")
            props = element(table, "tblPr")
            element(props, "tblW", {"w": 14400, "type": "dxa"})
            borders = element(props, "tblBorders")
            for side in ("top", "bottom", "insideV"):
                element(borders, side, {"val": "single", "sz": 3, "color": "BBBBBB"})
            element(props, "tblLayout", {"type": "fixed"})
            margins = element(props, "tblCellMar")
            for side in ("top", "left", "bottom", "right"):
                element(margins, side, {"w": 120, "type": "dxa"})
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
                    paragraph(cell, text, language=language, table_header=index == 0)
        else:
            paragraph(body, value, kind if kind.startswith("h") else None,
                      cover=block_index if block_index < 7 else None)
    section = element(body, "sectPr")
    element(section, "footerReference", {"type": "default"}).set(f"{{{R}}}id", "footer")
    element(section, "pgSz", {
        "w": 16838 if landscape else 11906,
        "h": 11906 if landscape else 16838,
        "orient": "landscape" if landscape else "portrait",
    })
    element(section, "pgMar", {
        "top": 850, "right": 1219, "bottom": 850, "left": 1219,
        "header": 400, "footer": 400, "gutter": 0,
    })
    footer = ET.Element(f"{{{W}}}ftr")
    p = element(footer, "p")
    element(element(p, "pPr"), "jc", {"val": "center"})
    field = element(p, "fldSimple", {"instr": "PAGE"})
    run = element(field, "r")
    rprops = element(run, "rPr")
    element(rprops, "rFonts", {"ascii": FONT, "hAnsi": FONT})
    element(rprops, "sz", {"val": 20})
    element(rprops, "lang", {"val": "ru-RU"})
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


def validate(content, expected, require_table=True, landscape=True):
    ns = {"w": W}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC error")
        for name in archive.namelist():
            ET.fromstring(archive.read(name))
        root = ET.fromstring(archive.read("word/document.xml"))
        # Check the property sequences emitted here, not the complete OOXML schema.
        for path, order in (
            (".//w:pPr", ["keepNext", "pBdr", "spacing", "jc", "outlineLvl"]),
            (".//w:rPr", ["rFonts", "b", "color", "sz", "u", "lang"]),
            (".//w:tblPr", ["tblW", "tblBorders", "tblLayout", "tblCellMar"]),
        ):
            for properties in root.findall(path, ns):
                indices = [order.index(child.tag.rsplit("}", 1)[-1]) for child in properties]
                if indices != sorted(indices):
                    raise ValueError("OOXML property order is invalid")
        actual = [
            "".join(p.itertext())
            for p in root.findall(".//w:p", ns)
            if p.findall(".//w:t", ns)
        ]
        if actual != expected:
            raise ValueError("Source text is missing, truncated or reordered")
        tables = root.findall(".//w:tbl", ns)
        if require_table and not tables:
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
        if size is None or size.get(f"{{{W}}}orient") != ("landscape" if landscape else "portrait"):
            raise ValueError("Wrong page orientation")
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
    parser.add_argument("--font-check", action="store_true", help="Check installed fonts, without downloading")
    args = parser.parse_args()
    content, expected = build(parse(args.source.read_text(encoding="utf-8")))
    validate(content, expected)
    if args.font_check:
        print(font_coverage("".join(expected) + "Α\u0313\u0301 Ω\u0342 ά Ἀ ᾠ Български English"))
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
