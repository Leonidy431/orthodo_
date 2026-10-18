import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.import_sources import (
    CANDIDATE_KEYS,
    SCORE_KEYS,
    deduplicate_candidates,
    run,
    validate_candidate,
    validate_manifest,
)


def make_candidate(**overrides):
    scores = {
        str(number): {"score": None, "basis": "Н/Д: источник не проверен."}
        for number in range(1, 13)
    }
    values = {
        "id": "example-book",
        "title": "Prayer Book",
        "author_publisher": "Example Church",
        "edition": "2024",
        "language": "English",
        "church": ["Example Church"],
        "material_type": "book",
        "canonical_url": "https://example.org/book",
        "download_url": None,
        "rights_status": "unknown",
        "checked_on": "2026-10-18",
        "scores": scores,
        "known_score_sum": 0,
        "verified_parameter_count": 0,
    }
    values.update(overrides)
    return values


def make_source(**overrides):
    source = {
        "id": "authorized-book",
        "url": "https://example.org/book.txt",
        "destination": "books/book.txt",
        "sha256": hashlib.sha256(b"book").hexdigest(),
        "verified": True,
        "rights_status": "authorized",
        "rights_evidence_url": "https://example.org/license",
        "license": "Explicit redistribution permission",
    }
    source.update(overrides)
    return source


class ImportSourcesTests(unittest.TestCase):
    def test_deduplicates_mirrors_and_repeated_formats_by_bibliography(self):
        first = make_candidate(canonical_url="https://example.org/book")
        mirror = make_candidate(
            id="same-book-mirror",
            canonical_url="https://mirror.example.org/book",
            download_url="https://mirror.example.org/book.pdf",
        )
        distinct_edition = make_candidate(id="new-edition", edition="2025")
        unique, duplicates = deduplicate_candidates([first, mirror, distinct_edition])
        self.assertEqual([item["id"] for item in unique], ["example-book", "new-edition"])
        self.assertEqual([item["id"] for item in duplicates], ["same-book-mirror"])

    def test_score_schema_has_exactly_12_fields_and_unknown_is_not_zero(self):
        candidate = make_candidate()
        validate_candidate(candidate)
        self.assertEqual(len(candidate["scores"]), 12)
        self.assertEqual(set(candidate["scores"]), SCORE_KEYS)

        candidate["scores"]["1"] = {"score": None, "basis": "Source missing"}
        with self.assertRaisesRegex(ValueError, "marked Н/Д"):
            validate_candidate(candidate)

        candidate = make_candidate()
        candidate["scores"]["1"] = {"score": 0, "basis": "Н/Д: source missing"}
        candidate["known_score_sum"] = 0
        candidate["verified_parameter_count"] = 1
        with self.assertRaisesRegex(ValueError, "explicit confirmed-absence"):
            validate_candidate(candidate)

        candidate = make_candidate(scores={"1": {"score": 4, "basis": "Verified text"}})
        self.assertEqual(set(candidate), CANDIDATE_KEYS)
        with self.assertRaisesRegex(ValueError, "exactly 12"):
            validate_candidate(candidate)

    def test_manifest_rejects_non_https_and_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_manifest({"version": 1, "sources": [make_source(url="http://example.org/file")]})
        with self.assertRaisesRegex(ValueError, "sources/ or books/"):
            validate_manifest(
                {"version": 1, "sources": [make_source(destination="books/../../outside.txt")]}
            )

    def test_dry_run_never_downloads_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps({"version": 1, "sources": [make_source()]}),
                encoding="utf-8",
            )
            network = Mock()
            output = io.StringIO()
            result = run(
                manifest_path,
                root,
                execute=False,
                opener=network,
                output=output,
            )
            self.assertEqual(result, 0)
            network.assert_not_called()
            self.assertFalse((root / "books" / "book.txt").exists())
            self.assertIn("DRY-RUN authorized-book", output.getvalue())


if __name__ == "__main__":
    unittest.main()
