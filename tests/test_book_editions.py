import copy
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book_pipeline as core
import book_editions as editions
import build_mvp_proofs as proofs
import build_word_third_attempt as word
import run_book_pipeline as runner


class EditionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = editions.validate_manifest(ROOT)
        self.edition = next(e for e in self.manifest["editions"] if e["id"] == "russia-cu-ru")
        self.corpus = core.read_json(ROOT / "books/russia/segments.json")
        self.sources = {
            lang: {
                "access": "primary_inspected", "rights": "open_license", "edition": "test-v1",
                "languages": [lang], "attribution": "Synthetic test only", "license": "test license",
                "checked_at": "2026-10-18", "url": "https://example.org/test", "sha256": "a" * 64,
            } for lang in ("cu", "ru", "en", "hy", "el", "bg")
        }

    def row(self, language, kind, group, chapter, original=None):
        return {
            "id": f"{group}-{language}-{kind}", "group_id": group, "chapter": chapter,
            "kind": kind, "language": language, "text": "Synthetic fixture, not a prayer",
            "verification": "confirmed", "source_id": language, "source_locator": "test section",
            "historical_edition": "test-v1", "language_variety": "not_applicable",
            "pronunciation": "not_applicable", "translation_of": original,
            "translator": "Synthetic test only" if original else None,
            "translation_provenance": "Test fixture" if original else None,
            "regional_applicability": {},
            "origin": "external",
        }

    def complete(self):
        for group in self.corpus["explanation_group_ids"]:
            self.corpus["segments"].append(self.row("ru", "apparatus", group, self.corpus["group_chapters"][group]))
        original = self.row("cu", "prayer_original", "R08", 8)
        self.corpus["segments"] += [
            original, self.row("ru", "prayer_translation", "R08", 8, original["id"]),
        ]
        self.edition["segment_selection"] = {
            f"{r['group_id']}:{r['kind']}:{r['language']}": {
                "segment_id": r["id"],
                **{key: r[key] for key in ("historical_edition", "language_variety", "pronunciation")},
            } for r in self.corpus["segments"]
        }
        return self.corpus

    def test_exact_matrix_and_actual_count(self):
        result = editions.report(ROOT)
        self.assertEqual(result["target_editions"], 9)
        self.assertEqual(result["content_corpora"], 5)
        self.assertEqual(result["target_files_existing"], 0)
        self.assertEqual(result["accepted_editions"], 0)
        self.assertTrue(all(e["status"] == "blocked_missing_segments" for e in result["editions"]))
        for row in result["editions"]:
            self.assertEqual(row["required_segment_completeness_percent"], 0)
            self.assertIs(row["ready_to_publish"], False)
        self.assertIsNone(result["editions"][0]["translation_completeness_percent"])

    def test_wrong_matrix_and_duplicate_path_rejected(self):
        manifest = core.read_json(ROOT / "books/manifest.json")
        for change in ("language", "path", "id", "apparatus"):
            bad = copy.deepcopy(self.manifest)
            if change == "language":
                bad["editions"][-1]["prayer_languages"] = ["ru"]
            elif change == "path":
                bad["editions"][-1]["docx"] = bad["editions"][0]["docx"]
            elif change == "apparatus":
                bad["editions"][-1]["apparatus_mode"] = "fully_bilingual"
            else:
                bad["editions"][-1]["id"] = "extra"
            with patch.object(core, "read_json", side_effect=[manifest, bad]):
                with self.assertRaises(ValueError):
                    editions.validate_manifest(ROOT)

    def test_complete_is_not_accepted(self):
        editions.validate_corpus(self.complete(), self.sources)
        result = editions.edition_status(self.edition, self.corpus)
        self.assertEqual(result["status"], "needs_human")
        self.assertEqual(result["required_segment_completeness_percent"], 100)
        self.assertEqual(result["translation_completeness_percent"], 100)
        self.assertFalse(result["ready_to_publish"])

    def test_missing_translation_and_transliteration(self):
        self.complete()
        translation = self.corpus["segments"][-1]
        for change in ({"verification": "unknown"}, {"language": "en", "source_id": "en"}, {"kind": "transliteration"}):
            corpus = copy.deepcopy(self.corpus)
            corpus["segments"][-1].update(change)
            editions.validate_corpus(corpus, self.sources)
            result = editions.edition_status(self.edition, corpus)
            self.assertIn("R08:prayer_translation:ru", result["missing_slots"])
            self.assertEqual(result["translation_completeness_percent"], 0)
        self.assertEqual(translation["kind"], "prayer_translation")

    def test_translation_must_link_same_group_original(self):
        self.complete()
        for link in (None, "missing", self.corpus["segments"][0]["id"]):
            corpus = copy.deepcopy(self.corpus)
            corpus["segments"][-1]["translation_of"] = link
            with self.assertRaisesRegex(ValueError, "aligned"):
                editions.validate_corpus(corpus, self.sources)

    def test_each_required_group_needs_its_own_translation(self):
        self.complete()
        self.corpus["prayer_group_ids"].append("R08-b")
        self.corpus["group_chapters"]["R08-b"] = 8
        self.corpus["segments"].append(self.row("cu", "prayer_original", "R08-b", 8))
        pin = copy.deepcopy(self.edition["segment_selection"]["R08:prayer_original:cu"])
        pin["segment_id"] = self.corpus["segments"][-1]["id"]
        self.edition["segment_selection"]["R08-b:prayer_original:cu"] = pin
        editions.validate_corpus(self.corpus, self.sources)
        result = editions.edition_status(self.edition, self.corpus)
        self.assertEqual(result["translation_completeness_percent"], 50)
        self.assertIn("R08-b:prayer_translation:ru", result["missing_slots"])
        self.corpus["segments"][-2]["group_id"] = "R08-b"
        with self.assertRaisesRegex(ValueError, "aligned"):
            editions.validate_corpus(self.corpus, self.sources)

    def test_original_and_translation_rights_independent(self):
        self.complete()
        for language in ("cu", "ru"):
            sources = copy.deepcopy(self.sources)
            sources[language]["rights"] = "unknown"
            with self.assertRaisesRegex(ValueError, "rights"):
                editions.validate_corpus(self.corpus, sources)

    def test_exact_recension_language_and_translation_provenance(self):
        self.complete()
        for key, value in (("historical_edition", "different"), ("translator", None), ("translation_provenance", None)):
            corpus = copy.deepcopy(self.corpus)
            corpus["segments"][-1][key] = value
            with self.assertRaises(ValueError):
                editions.validate_corpus(corpus, self.sources)
        self.sources["ru"]["languages"] = ["cu"]
        with self.assertRaisesRegex(ValueError, "language"):
            editions.validate_corpus(self.corpus, self.sources)

    def test_russian_cannot_replace_slavonic(self):
        self.complete()
        self.corpus["segments"][-2].update(language="ru", source_id="ru")
        with self.assertRaisesRegex(ValueError, "Slavonic"):
            editions.validate_corpus(self.corpus, self.sources)

    def test_explicit_armenian_variety_pronunciation_and_armenia(self):
        corpus = core.read_json(ROOT / "books/armenia/segments.json")
        row = self.row("hy", "prayer_original", "A08", 8)
        corpus["segments"] = [row]
        with self.assertRaisesRegex(ValueError, "variety"):
            editions.validate_corpus(corpus, self.sources)
        row.update(language_variety="classical", pronunciation="not_specified")
        editions.validate_corpus(corpus, self.sources)
        row["pronunciation"] = None
        with self.assertRaisesRegex(ValueError, "pronunciation"):
            editions.validate_corpus(corpus, self.sources)
        row.update(language_variety="modern_eastern", pronunciation="eastern")
        with self.assertRaisesRegex(ValueError, "Armenia"):
            editions.validate_corpus(corpus, self.sources)
        row["regional_applicability"] = {
            "territory": "Армения", "verification": "confirmed", "source_id": "hy", "locator": "test",
        }
        editions.validate_corpus(corpus, self.sources)
        self.sources["hy"]["access"] = "search_only"
        with self.assertRaises(ValueError):
            editions.validate_corpus(corpus, self.sources)

    def test_bad_group_chapter_and_duplicate_segment(self):
        self.complete()
        for change in ("chapter", "duplicate", "slot"):
            corpus = copy.deepcopy(self.corpus)
            if change == "chapter":
                corpus["segments"][0]["chapter"] = 3
            else:
                extra = copy.deepcopy(corpus["segments"][0])
                if change == "slot":
                    extra["id"] = "another"
                corpus["segments"].append(extra)
            with self.assertRaises(ValueError):
                editions.validate_corpus(corpus, self.sources)

    def test_external_apparatus_and_transliteration_cannot_skip_rights(self):
        self.complete()
        for kind in ("apparatus", "transliteration"):
            corpus = copy.deepcopy(self.corpus)
            row = corpus["segments"][0] if kind == "apparatus" else corpus["segments"][-1]
            row["kind"] = kind
            row["source_id"] = "nonexistent"
            with self.assertRaisesRegex(ValueError, "rights"):
                editions.validate_corpus(corpus, self.sources)

    def test_author_created_apparatus_requires_explicit_attribution(self):
        self.complete()
        row = self.corpus["segments"][0]
        row.update(origin="author_created", source_id=None)
        with self.assertRaisesRegex(ValueError, "attributed"):
            editions.validate_corpus(self.corpus, self.sources)
        row.update(attribution="Synthetic compiler fixture", rights="project_original")
        editions.validate_corpus(self.corpus, self.sources)
        self.corpus["segments"][-2].update(origin="author_created", source_id=None, attribution="test", rights="project_original")
        with self.assertRaisesRegex(ValueError, "apparatus"):
            editions.validate_corpus(self.corpus, self.sources)

    def test_variants_coexist_but_cannot_satisfy_another_selection(self):
        self.complete()
        extra = copy.deepcopy(self.corpus["segments"][-2])
        extra.update(id="different-recension", historical_edition="test-v2", source_id="cu-v2")
        self.sources["cu-v2"] = dict(self.sources["cu"], edition="test-v2")
        self.corpus["segments"].append(extra)
        editions.validate_corpus(self.corpus, self.sources)
        self.assertEqual(editions.edition_status(self.edition, self.corpus)["status"], "needs_human")
        pin = self.edition["segment_selection"]["R08:prayer_original:cu"]
        pin["segment_id"] = extra["id"]
        result = editions.edition_status(self.edition, self.corpus)
        self.assertIn("R08:prayer_original:cu", result["missing_slots"])
        pin["historical_edition"] = "test-v2"
        result = editions.edition_status(self.edition, self.corpus)
        self.assertNotIn("R08:prayer_original:cu", result["missing_slots"])
        self.assertIn("R08:prayer_translation:ru", result["missing_slots"])

    def test_armenian_variants_and_pronunciation_selection(self):
        corpus = core.read_json(ROOT / "books/armenia/segments.json")
        classical = self.row("hy", "prayer_original", "A08", 8)
        classical.update(language_variety="classical", pronunciation="western")
        modern = copy.deepcopy(classical)
        modern.update(id="modern-hy", language_variety="modern_eastern", pronunciation="eastern")
        modern["regional_applicability"] = {
            "territory": "Армения", "verification": "confirmed", "source_id": "hy", "locator": "test",
        }
        corpus["segments"] = [classical, modern]
        editions.validate_corpus(corpus, self.sources)
        target = copy.deepcopy(next(e for e in self.manifest["editions"] if e["id"] == "armenia-hy-ru"))
        target["segment_selection"] = {"A08:prayer_original:hy": {
            "segment_id": modern["id"], "historical_edition": "test-v1",
            "language_variety": "classical", "pronunciation": "western",
        }}
        self.assertIn("A08:prayer_original:hy", editions.edition_status(target, corpus)["missing_slots"])

    def test_99_features_markdown_matches_ids_and_boundaries(self):
        rows = editions.features(ROOT)
        self.assertEqual(len(rows), 99)
        self.assertEqual(sum(r["kind"] == "observed" for r in rows), 36)
        text = (ROOT / "docs/research/mvp-features-99.md").read_text()
        self.assertEqual(len(re.findall(r"^\d+\. [A-Z]\d{2} —", text, re.M)), 99)
        for row in rows:
            self.assertIn(row["id"], text)
            self.assertEqual(bool(row["source_urls"]), row["kind"] == "observed")

    def test_default_report_is_read_only(self):
        with patch.object(Path, "write_text", side_effect=AssertionError("write")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
            self.assertEqual(editions.report(ROOT)["network_requests"], 0)

    def test_runner_schema_and_segment_gates(self):
        state = {}
        self.assertEqual(runner.phase_result("P1", ROOT, state, float("inf"))[0], "passed")
        self.assertEqual(state["editions"]["target_editions"], 9)
        self.assertEqual(runner.phase_result("P4", ROOT, {}, float("inf"))[0], "blocked")

    def test_language_inputs_invalidate_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("books/manifest.json", "data/evidence.json", "data/jurisdictions.json",
                         "books/editions.json", "books/russia/segments.json", "data/research/mvp-features-99.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            for name in ("books/editions.json", "books/russia/segments.json", "data/research/mvp-features-99.json"):
                before = runner.fingerprint(root)
                path = root / name
                path.write_text(path.read_text() + "\n")
                self.assertNotEqual(before, runner.fingerprint(root))


class ProofTests(unittest.TestCase):
    def test_two_and_three_column_proofs(self):
        data = core.read_json(ROOT / "data/research/layout-proofs.json")
        for columns in (2, 3):
            content, expected = proofs.proof(data, columns)
            self.assertEqual(content, proofs.proof(data, columns)[0])
            word.validate(content, expected, table_columns=columns)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                doc = ET.fromstring(archive.read("word/document.xml"))
                cells = doc.findall(f".//{{{word.W}}}tr")[1].findall(f"{{{word.W}}}tc")
                self.assertEqual(len(cells), columns)
                self.assertEqual([c.find(f".//{{{word.W}}}lang").get(f"{{{word.W}}}val") for c in cells], data["languages"][:columns])
        self.assertEqual(len(list((ROOT / "deliverables").glob("*.docx"))), 5)

    def test_bilingual_parse_and_malformed_table(self):
        source = "\n".join(word.TITLE) + "\n| Ελληνικά | English |\n| --- | --- |\n| Πού; | Where? |"
        blocks = word.parse(source, title=word.TITLE, table_headers=["Ελληνικά", "English"])
        self.assertEqual(blocks[-1][1][1], ["Πού;", "Where?"])
        with self.assertRaises(ValueError):
            word.build(blocks)
        with self.assertRaises(ValueError):
            word.parse("| Ελληνικά | English |\n| Πού; |", title=word.TITLE, table_headers=["Ελληνικά", "English"])

    def test_missing_language_and_duplicate_row_rejected(self):
        data = core.read_json(ROOT / "data/research/layout-proofs.json")
        del data["rows"][0]["bg-BG"]
        with self.assertRaises(ValueError):
            proofs.proof(data, 3)
        data = core.read_json(ROOT / "data/research/layout-proofs.json")
        data["rows"].append(data["rows"][0])
        with self.assertRaises(ValueError):
            proofs.proof(data, 2)


if __name__ == "__main__":
    unittest.main()
