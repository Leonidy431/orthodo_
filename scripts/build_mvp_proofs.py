#!/usr/bin/env python3
"""Build bilingual/trilingual editorial-question proofs, not language editions."""

import argparse
from pathlib import Path

import book_pipeline as core
import build_word_third_attempt as word


ROOT = Path(__file__).resolve().parents[1]


def proof(data, columns):
    if columns not in {2, 3}:
        raise ValueError("Expected two or three proof languages")
    languages = data["languages"][:columns]
    rows = [data["headers"][:columns]]
    if len(data["rows"]) != len({row["id"] for row in data["rows"]}):
        raise ValueError("Proof row IDs must be distinct")
    for row in data["rows"]:
        if any(not isinstance(row.get(lang), str) or not row[lang].strip() for lang in languages):
            raise ValueError("Missing aligned question language")
        rows.append([row[lang] for lang in languages])
    if len(rows) < 2:
        raise ValueError("No proof content")
    title = [
        "Гаврилов Леонид Геннадьевич",
        "Тестовый разворот: составительские вопросы",
        "Двуязычная проба EL/EN" if columns == 2 else "Трёхъязычная проба EL/EN/BG",
        "Макет оформления, не молитвенный текст",
        "2026",
        "Рабочая исследовательская редакция",
    ]
    blocks = [("p", text) for text in title]
    blocks += [
        ("p", "Только проверка структуры таблицы. Не готовое языковое издание и не визуально проверенная страница."),
        ("pagebreak", ""),
        ("p", "Составительские вопросы, не церковные предписания. Переводы молитв здесь не демонстрируются."),
        ("table", rows),
    ]
    content, expected = word.build(blocks, table_languages=languages)
    word.validate(content, expected, table_columns=columns)
    word.font_coverage("".join(expected))
    return content, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = core.read_json(ROOT / "data/research/layout-proofs.json")
    for columns, name in ((2, "bilingual"), (3, "trilingual")):
        content, _ = proof(data, columns)
        path = core.project_path(ROOT, f"deliverables/proofs/{name}.docx", "deliverables")
        if args.check and path.read_bytes() != content:
            raise ValueError("Proof differs from editable JSON source")
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        print(f"{name}: {len(content)} bytes; {'written' if args.write else 'checked' if args.check else 'dry-run, no write'}; not visually rendered")


if __name__ == "__main__":
    main()
