#!/usr/bin/env python3
"""Validate nine language targets and report missing material, never invent translations."""

import argparse
import json
from pathlib import Path

import book_pipeline as core


ROOT = Path(__file__).resolve().parents[1]
MVP = {1, 2, 5, 8, 9, 10, 12}
TARGETS = {
    "universal-el-en-bg": ("universal", "ru", ("el", "en", "bg"), ()),
    **{
        f"{corpus}-{prayer}-{explanation}": (corpus, explanation, (prayer,), (explanation,))
        for corpus, prayer in (("armenia", "hy"), ("bulgaria", "bg"), ("cyprus", "el"), ("russia", "cu"))
        for explanation in ("ru", "en")
    },
}
LANGUAGES = {"hy", "ru", "en", "bg", "el", "cu"}


def features(root):
    data = core.read_json(Path(root) / "data/research/mvp-features-99.json")
    result = []
    for group in data["groups"]:
        if len(group["features"]) != 9:
            raise ValueError("Every feature group must contain nine distinct requirements")
        for feature in group["features"]:
            row = {key: value for key, value in group.items() if key not in {"features", "id"}}
            row.update(feature, group_id=group["id"])
            if row["kind"] not in {"observed", "proposed"} or row["priority"] not in {"MVP", "next", "later"}:
                raise ValueError("Invalid feature kind/priority")
            if not all(row.get(field) for field in ("id", "description", "locator", "transfer_limit", "verification", "applicable_books")):
                raise ValueError("Feature lacks explanation, limits or evidence boundary")
            if not set(row["applicable_books"]) <= core.BOOK_IDS:
                raise ValueError("Unknown applicable book")
            if row["kind"] == "observed":
                if row["verification"] != "provided_research_not_independently_verified" or not row["source_ids"]:
                    raise ValueError("Supplied observations must not claim own verification")
                if not set(row["source_ids"]) <= data["sources"].keys():
                    raise ValueError("Missing analogue source")
                row["source_urls"] = [url for sid in row["source_ids"] for url in data["sources"][sid]["urls"]]
            else:
                if row["source_ids"] or row["verification"] != "project_requirement_not_source_observation":
                    raise ValueError("Proposed requirement is not a source observation")
                row["source_urls"] = []
            result.append(row)
    if len(result) != 99 or len({f["id"] for f in result}) != 99:
        raise ValueError("Exactly 99 unique feature IDs required")
    if sum(f["kind"] == "observed" for f in result) != 36:
        raise ValueError("Expected 36 supplied observations and 63 proposed adaptations")
    if len({f["description"] for f in result}) != 99:
        raise ValueError("Duplicate feature description")
    return result


def validate_manifest(root):
    root = Path(root)
    manifest = core.read_json(root / "books/manifest.json")
    path = core.project_path(root, manifest["edition_manifest"], "books")
    editions = core.read_json(path)
    rows = editions["editions"]
    if manifest["target_edition_count"] != 9 or editions["target_count"] != 9 or editions["corpus_count"] != 5:
        raise ValueError("Five corpora and nine target builds required")
    if len(rows) != 9 or {row["id"] for row in rows} != TARGETS.keys():
        raise ValueError("Nine exact edition IDs required, not nine arbitrary files")
    if len({row["docx"] for row in rows}) != 9:
        raise ValueError("Edition output paths must be distinct")
    corpora = editions["corpora"]
    if len(corpora) != 5 or {c["id"] for c in corpora} != core.BOOK_IDS:
        raise ValueError("Exactly five shared corpora required")
    if set(editions["mvp_chapters"]) != MVP:
        raise ValueError("Wrong MVP chapter selection")
    for corpus in corpora:
        core.project_path(root, corpus["segments"], "books")
    for row in rows:
        expected = TARGETS[row["id"]]
        if (row["corpus_id"], row["explanation_language"], tuple(row["prayer_languages"]), tuple(row["translation_languages"])) != expected:
            raise ValueError("Edition language matrix differs from approved target")
        if row["apparatus_mode"] != "monolingual" or not set(row["reader_languages"]) <= LANGUAGES:
            raise ValueError("Single-language apparatus must not be called fully bilingual")
        if row["explanation_language"] not in row["reader_languages"]:
            raise ValueError("Reader language excludes explanations")
        expected_layout = "parallel_three_columns" if row["corpus_id"] == "universal" else "parallel_two_columns"
        if row["prayer_layout"] != expected_layout:
            raise ValueError("Wrong prayer-block layout")
        if not {"historical_edition", "pronunciation", "status"} <= row.keys():
            raise ValueError("Edition must expose unresolved recension/pronunciation explicitly")
        core.project_path(root, row["docx"], "deliverables")
    return editions


def validate_corpus(data, source_map):
    if data["corpus_id"] not in core.BOOK_IDS:
        raise ValueError("Unknown corpus")
    explanation_ids, prayer_ids = data["explanation_group_ids"], data["prayer_group_ids"]
    groups = explanation_ids + prayer_ids
    if not explanation_ids or not prayer_ids or len(groups) != len(set(groups)):
        raise ValueError("Explicit distinct explanation and prayer groups required")
    if set(data["group_chapters"]) != set(groups) or set(data["group_chapters"].values()) != MVP:
        raise ValueError("Every required group must map to an explicit MVP chapter")
    rows = data["segments"]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate segment ID")
    by_id = {row["id"]: row for row in rows}
    slots = set()
    for row in rows:
        required = {"id", "group_id", "chapter", "kind", "language", "text",
                    "verification", "source_id", "source_locator", "historical_edition",
                    "language_variety", "pronunciation", "translation_of", "translator",
                    "translation_provenance", "regional_applicability"}
        if not required <= row.keys():
            raise ValueError("Segment must preserve language, edition, translation and pronunciation metadata")
        if row["kind"] not in {"apparatus", "prayer_original", "prayer_translation", "transliteration"}:
            raise ValueError("Invalid segment role")
        if row["language"] not in LANGUAGES or row["verification"] not in core.VERIFICATIONS:
            raise ValueError("Invalid segment language/status")
        if not isinstance(row["text"], str) or not row["text"].strip() or row["chapter"] not in range(1, 13):
            raise ValueError("Empty segment or invalid chapter")
        if row["group_id"] not in groups:
            raise ValueError("Segment outside registered group")
        if row["chapter"] != data["group_chapters"][row["group_id"]]:
            raise ValueError("Segment aligned with wrong chapter")
        prayer = row["kind"] != "apparatus"
        if (row["group_id"] in prayer_ids) != prayer or (prayer and row["chapter"] != 8):
            raise ValueError("Apparatus and prayer blocks must remain separate")
        slot = (row["group_id"], row["language"], row["kind"])
        if slot in slots:
            raise ValueError("Ambiguous duplicate language segment")
        slots.add(slot)
        if not prayer:
            if row["translation_of"] is not None:
                raise ValueError("Apparatus is not a prayer translation")
            continue
        if row["kind"] == "transliteration":
            if row["translation_of"] not in by_id or row["pronunciation"] not in {"eastern", "western", "not_applicable"}:
                raise ValueError("Transliteration needs an original and explicit pronunciation")
            original = by_id[row["translation_of"]]
            if original["kind"] != "prayer_original" or original["group_id"] != row["group_id"]:
                raise ValueError("Transliteration must reference its own group's original")
            continue
        source = source_map.get(row["source_id"], {})
        if row["verification"] == "confirmed":
            if not row["source_locator"] or not row["historical_edition"] or not core.publishable(source):
                raise ValueError("Confirmed prayer needs text-specific rights and edition provenance")
            if row["historical_edition"] != source["edition"]:
                raise ValueError("Segment recension differs from its exact source edition")
            if row["language"] not in source.get("languages", []):
                raise ValueError("Exact source must document this prayer/translation language")
            if row["language"] == "hy":
                if row["language_variety"] not in {"classical", "modern_eastern", "modern_western"}:
                    raise ValueError("Armenian variety must be explicit")
                if row["pronunciation"] not in {"eastern", "western", "not_specified"}:
                    raise ValueError("Armenian pronunciation cannot be silently inferred")
                if row["language_variety"].startswith("modern_") and not (
                    row["regional_applicability"].get("territory") == "Армения"
                    and row["regional_applicability"].get("verification") == "confirmed"
                    and row["regional_applicability"].get("source_id") in source_map
                    and source_map[row["regional_applicability"]["source_id"]]["access"] == "primary_inspected"
                    and row["regional_applicability"].get("locator")
                ):
                    raise ValueError("Modern Armenian needs separately verified applicability to Armenia")
            if data["corpus_id"] == "russia" and row["kind"] == "prayer_original" and row["language"] != "cu":
                raise ValueError("Russian cannot substitute for Church Slavonic original")
        if row["kind"] == "prayer_original":
            if row["translation_of"] is not None:
                raise ValueError("Original must not be silently relabelled a translation")
        else:
            original = by_id.get(row["translation_of"])
            if not original or original["kind"] != "prayer_original" or original["group_id"] != row["group_id"]:
                raise ValueError("Translation is not aligned to its original group")
            if original["language"] == row["language"]:
                raise ValueError("Translation must not relabel the same language")
            if row["verification"] == "confirmed" and not all(
                row[field] for field in ("translator", "translation_provenance")
            ):
                raise ValueError("Confirmed translation lacks translator/provenance")
    return data


def edition_status(edition, corpus):
    """Completeness is by required slots, never by presence of any language somewhere."""
    rows = [row for row in corpus["segments"] if row["verification"] == "confirmed"]
    missing, required_count, translation_count, missing_translations = [], 0, 0, 0
    for group in corpus["explanation_group_ids"]:
        required_count += 1
        if not any(r["group_id"] == group and r["kind"] == "apparatus"
                   and r["language"] == edition["explanation_language"] for r in rows):
            missing.append(f"{group}:apparatus:{edition['explanation_language']}")
    for group in corpus["prayer_group_ids"]:
        originals = [r for r in rows if r["group_id"] == group and r["kind"] == "prayer_original"]
        for language in edition["prayer_languages"]:
            required_count += 1
            if not any(r["language"] == language for r in originals):
                missing.append(f"{group}:prayer_original:{language}")
        for language in edition["translation_languages"]:
            required_count += 1
            translation_count += 1
            aligned = [
                r for r in rows if r["group_id"] == group and r["kind"] == "prayer_translation"
                and r["language"] == language
                and any(o["id"] == r["translation_of"] and o["language"] in edition["prayer_languages"] for o in originals)
            ]
            if not aligned:
                missing.append(f"{group}:prayer_translation:{language}")
                missing_translations += 1
    covered_chapters = {r["chapter"] for r in rows if r["kind"] != "transliteration"
                        and r["language"] in {edition["explanation_language"], *edition["prayer_languages"], *edition["translation_languages"]}}
    missing_chapters = sorted(MVP - covered_chapters)
    return {
        "id": edition["id"], "corpus_id": edition["corpus_id"],
        "status": "blocked_missing_segments" if missing or missing_chapters else "needs_human",
        "missing_slots": missing, "missing_mvp_chapters": missing_chapters,
        "required_segment_completeness_percent": 100 * (required_count - len(missing)) / required_count,
        "translation_completeness_percent": (
            100 * (translation_count - missing_translations) / translation_count
            if translation_count else None
        ),
        "ready_to_publish": False,
        "reason": "No missing slots is still not ecclesiastical, language or visual approval",
    }


def report(root):
    root = Path(root)
    editions = validate_manifest(root)
    evidence = core.read_json(root / "data/evidence.json")
    sources = {row["id"]: row for row in evidence["sources"]}
    corpora = {}
    for row in editions["corpora"]:
        data = core.read_json(core.project_path(root, row["segments"], "books"))
        if data["corpus_id"] != row["id"]:
            raise ValueError("Corpus file identity mismatch")
        corpora[row["id"]] = validate_corpus(data, sources)
    results = []
    for edition in editions["editions"]:
        result = edition_status(edition, corpora[edition["corpus_id"]])
        output = core.project_path(root, edition["docx"], "deliverables")
        result["target_file_exists"] = output.is_file()
        result["file_existence_is_not_acceptance"] = True
        results.append(result)
    return {
        "target_editions": 9, "content_corpora": 5,
        "target_files_existing": sum(r["target_file_exists"] for r in results),
        "accepted_editions": 0, "editions": results,
        "network_requests": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true", help="Explicitly write data/pipeline/editions.json, not DOCX")
    args = parser.parse_args()
    result = report(ROOT)
    features(ROOT)
    if args.write_report:
        path = core.project_path(ROOT, "data/pipeline/editions.json", "data")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("Refusing symlink report output")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(2 if any(r["status"] != "needs_human" for r in result["editions"]) else 0)


if __name__ == "__main__":
    main()
