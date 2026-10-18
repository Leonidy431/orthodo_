import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import io
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book_pipeline as core
import build_word_third_attempt as word
import run_book_pipeline as runner


class EvidenceTests(unittest.TestCase):
    def test_actual_schemas(self):
        manifest, registry, evidence = core.validate_schemas(ROOT)
        self.assertEqual(len(manifest["books"]), 5)
        self.assertFalse(registry["scope_closed"])
        self.assertTrue(all(c["verification"] == "unknown" for c in evidence["claims"]))

    def test_paths_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "books").mkdir()
            (root / "books/outside").symlink_to("/tmp", target_is_directory=True)
            for name in ("/tmp/x", "books/../x", "books/outside/x"):
                with self.assertRaises(ValueError):
                    core.project_path(root, name, "books")

    def test_deduplication(self):
        rows = core.deduplicate([
            {"id": "a", "sha256": "same"},
            {"id": "b", "sha256": "same"},
            {"id": "c", "title": "same"},
            {"id": "d", "title": "same"},
            {"id": "e", "work": "prayers", "edition": "1", "language": "EL"},
            {"id": "f", "work": "prayers", "edition": "2", "language": "EL"},
        ])
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["duplicates"], ["b"])

    def test_completeness(self):
        self.assertEqual(core.completeness(["confirmed", "refuted", "unknown", "conflict", None]), 40)
        self.assertEqual(core.completeness([]), 0)

    def test_three_values_and_conflict(self):
        self.assertEqual(core.combine(["confirmed", "confirmed"]), "confirmed")
        self.assertEqual(core.combine(["confirmed", "unknown"]), "unknown")
        self.assertEqual(core.combine(["refuted", "unknown"]), "refuted")
        self.assertEqual(core.combine(["confirmed", "refuted"]), "conflict")
        self.assertEqual(core.combine(["conflict", "unknown"]), "conflict")
        self.assertEqual(core.combine([]), "unknown")

    def fixture(self):
        registry = {
            "scope_closed": True, "territories": ["Cyprus"], "comparative_territories": ["Armenia"],
            "jurisdictions": [
                {"id": "cy", "territories": ["Cyprus"], "family": "eastern_orthodox", "verification": "confirmed"},
                {"id": "am", "territories": ["Armenia"], "family": "oriental_orthodox", "verification": "confirmed"},
            ],
        }
        claim = {
            "verification": "confirmed", "category": "forbidden",
            "local_custom": False, "exceptions": [], "context": {
                "jurisdictions": ["cy", "am"], "visitor_status": "visitor",
                "place": "all places in the stated jurisdictional scope",
                "service_time": "the service explicitly named by the sources",
                "accessibility": "explicit applicability checked in sources",
                "gender_age": None, "gender_age_status": "not_distinguished_by_source",
            },
            "evidence": [
                {"jurisdiction_id": j["id"], "family": j["family"], "status": "confirmed",
                 "source_id": j["id"], "locator": "section 1", "applicability": "jurisdiction",
                 "context": {}} for j in registry["jurisdictions"]
            ],
        }
        for item in claim["evidence"]:
            item["context"] = copy.deepcopy(claim["context"])
        sources = [{"id": j["id"], "access": "primary_inspected"} for j in registry["jurisdictions"]]
        return claim, registry, sources

    def test_intersection_closed_full_and_contextual(self):
        claim, registry, sources = self.fixture()
        self.assertEqual(core.intersection(claim, registry, sources), "confirmed")
        for field, value in (("scope_closed", False),):
            changed = dict(registry, **{field: value})
            self.assertEqual(core.intersection(claim, changed, sources), "unknown")
        self.assertEqual(core.intersection(dict(claim, evidence=claim["evidence"][:1]), registry, sources), "unknown")
        claim["evidence"][0]["context"] = {"visitor_status": "catechumen"}
        self.assertEqual(core.intersection(claim, registry, sources), "unknown")

    def test_local_custom_exceptions_family_and_conflict(self):
        claim, registry, sources = self.fixture()
        for changes in ({"local_custom": True}, {"exceptions": ["local exception"]}):
            self.assertEqual(core.intersection(dict(claim, **changes), registry, sources), "unknown")
        claim["evidence"][1]["family"] = "eastern_orthodox"
        self.assertEqual(core.intersection(claim, registry, sources), "unknown")
        claim, registry, sources = self.fixture()
        claim["evidence"][1]["status"] = "refuted"
        self.assertEqual(core.intersection(claim, registry, sources), "conflict")

    def test_unspecified_context_cannot_enter_universal_core(self):
        claim, registry, sources = self.fixture()
        for key, value in (("visitor_status", "unspecified"), ("place", None),
                           ("service_time", None), ("accessibility", None),
                           ("jurisdictions", []), ("gender_age_status", None)):
            changed = copy.deepcopy(claim)
            changed["context"][key] = value
            for item in changed["evidence"]:
                item["context"] = copy.deepcopy(changed["context"])
            self.assertEqual(core.intersection(changed, registry, sources), "unknown")

    def test_publication_rights_are_text_specific(self):
        source = {
            "access": "primary_inspected", "rights": "open_license", "edition": "v1",
            "attribution": "publisher", "license": "explicit text license",
            "checked_at": "2026-10-18", "url": "https://example.org", "sha256": "a" * 64,
        }
        self.assertTrue(core.publishable(source))
        for changes in ({"rights": "unknown"}, {"license": None}, {"access": "search_only"},
                        {"sha256": "invalid"}, {"git_url": "https://example.org/repo", "commit_sha": None}):
            self.assertFalse(core.publishable(dict(source, **changes)))

    def test_bad_schema_family_duplicate_and_reference(self):
        manifest, registry, evidence = core.validate_schemas(ROOT)
        for target in ("family", "duplicate", "source"):
            broken = copy.deepcopy(registry)
            if target == "family":
                broken["jurisdictions"][0]["family"] = "political_group"
            elif target == "duplicate":
                broken["jurisdictions"].append(broken["jurisdictions"][0])
            else:
                broken["jurisdictions"][0]["source_ids"] = ["missing"]
            with patch.object(core, "read_json", side_effect=[manifest, broken, evidence]):
                with self.assertRaises(ValueError):
                    core.validate_schemas(ROOT)


class DesignTests(unittest.TestCase):
    def test_used_font_codepoints(self):
        coverage = word.font_coverage("Α\u0313\u0301 Ω\u0342 α\u0345 Ἀ ᾠ Български English")
        self.assertEqual(len(coverage), 2)

    def test_questions_are_not_decorated_as_prayers(self):
        content, expected = word.build([("p", text) for text in word.TITLE] + [
            ("table", [word.HEADER, ["Πού;", "Where?", "Къде?"]]),
        ])
        word.validate(content, expected)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": word.W}
            row = root.findall(".//w:tr", ns)[1]
            for run in row.findall(".//w:r", ns):
                self.assertEqual(run.find("w:rPr/w:color", ns).get(f"{{{word.W}}}val"), "000000")
                self.assertEqual(run.find("w:rPr/w:sz", ns).get(f"{{{word.W}}}val"), "23")

    def test_grapheme_and_no_case_change(self):
        for text, first in (("Α\u0313\u0301λφα", "Α\u0313\u0301"), ("Ἀλφα", "Ἀ"),
                            ("Буква", "Б"), ("Example", "E"), ("α\u0345λφα", "α\u0345")):
            a, rest = word.initial(text)
            self.assertEqual(a, first)
            self.assertEqual(a + rest, text)
        with self.assertRaises(ValueError):
            word.initial("\u0313Α")

    def test_red_initial_in_each_language_cell(self):
        rows = [word.HEADER, ["[prayer] Α\u0313\u0301λφα", "[prayer] Example", "[prayer] Буква"]]
        content, expected = word.build([("p", t) for t in word.TITLE] + [("table", rows)])
        word.validate(content, expected)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": word.W}
            for cell, initial in zip(xml.findall(".//w:tr", ns)[1].findall("w:tc", ns), ["Α\u0313\u0301", "E", "Б"]):
                run = cell.find(".//w:r", ns)
                self.assertEqual(run.find("w:t", ns).text, initial)
                self.assertEqual(run.find("w:rPr/w:color", ns).get(f"{{{word.W}}}val"), "9B2424")
                self.assertEqual(run.find("w:rPr/w:sz", ns).get(f"{{{word.W}}}val"), "38")

    def test_long_unicode_and_reproducible_layout(self):
        blocks = [("p", t) for t in word.TITLE] + [("table", [word.HEADER, ["Α\u0313" * 2000, "E" * 2000, "Б" * 2000]])]
        content, expected = word.build(blocks)
        word.validate(content, expected)
        self.assertEqual(content, word.build(blocks)[0])

    def test_contrast(self):
        def linear(value):
            value /= 255
            return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
        lum = sum(w * linear(v) for w, v in zip((.2126, .7152, .0722), (155, 36, 36)))
        self.assertGreater((1.05) / (lum + .05), 7)


class RunnerTests(unittest.TestCase):
    def test_universal_builder_cannot_promote_flag_without_intersection(self):
        manifest, _, evidence = core.validate_schemas(ROOT)
        book = copy.deepcopy(next(b for b in manifest["books"] if b["id"] == "universal"))
        book["verified_claims"] = 1
        evidence = copy.deepcopy(evidence)
        evidence["claims"][0]["verification"] = "confirmed"
        actual_read = core.read_json
        def read(path):
            value = actual_read(path)
            if Path(path).name == "sources.json":
                value["verified_claim_ids"] = ["Q-PRAYER"]
            return value
        with patch.object(core, "read_json", side_effect=read):
            with self.assertRaisesRegex(ValueError, "intersection"):
                runner.book_build(ROOT, book, evidence)

    def test_dry_run_has_no_side_effects_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(runner, "write_json", side_effect=AssertionError("write")), \
                 patch.object(runner.subprocess, "run", side_effect=AssertionError("process")):
                result = runner.run(root, ["P5"])
            self.assertEqual(result["writes"], 0)
            self.assertEqual(list(root.iterdir()), [])

    def fixture_root(self, root):
        for name in ("books/manifest.json", "data/evidence.json", "data/jurisdictions.json"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())

    def test_resume_skips_passed_and_unchanged_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_root(root)
            with patch.object(runner, "phase_result", return_value=("blocked", "DNS")) as call:
                state = runner.run(root, ["P2"], execute=True)
                state = runner.run(root, ["P2"], execute=True, resume=True)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(state["phases"]["P2"]["attempts"], 1)
                runner.run(root, ["P2"], execute=True, resume=True, retry_reason="new accessible source")
                self.assertEqual(call.call_count, 2)
            with patch.object(runner, "phase_result", return_value=("passed", "OK")) as call:
                runner.run(root, ["P1"], execute=True, resume=True)
                runner.run(root, ["P1"], execute=True, resume=True)
                self.assertEqual(call.call_count, 1)

    def test_attempt_budget_and_invalid_limits(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                runner.run(ROOT, ["P0"], max_seconds=value)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_root(root)
            with patch.object(runner, "phase_result", return_value=("failed", "error")) as call:
                runner.run(root, ["P2"], execute=True, max_attempts=1)
                state = runner.run(root, ["P2"], execute=True, resume=True, max_attempts=1, retry_reason="new hypothesis")
                self.assertEqual(call.call_count, 1)
                self.assertEqual(state["phases"]["P2"]["status"], "blocked")

    def test_source_budget_and_publication_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_root(root)
            with patch.object(runner, "phase_result", side_effect=AssertionError("must not execute")):
                state = runner.run(root, ["P2"], execute=True, max_sources=0)
            self.assertEqual(state["phases"]["P2"]["status"], "blocked")
        with self.assertRaises(ValueError):
            runner.run(ROOT, ["P0"], publish=True)

    def test_history_cannot_be_silently_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_root(root)
            with patch.object(runner, "phase_result", return_value=("passed", "OK")):
                runner.run(root, ["P1"], execute=True)
                with self.assertRaises(ValueError):
                    runner.run(root, ["P1"], execute=True)
                data = core.read_json(root / "data/evidence.json")
                data["sources"][0]["edition"] = "new input"
                (root / "data/evidence.json").write_text(json.dumps(data))
                state = runner.run(root, ["P0"], execute=True, resume=True)
                self.assertEqual(state["phases"]["P1"]["status"], "pending")

    def test_deadline_and_interrupted_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture_root(root)
            with patch.object(runner.time, "monotonic", side_effect=[0, 2]):
                state = runner.run(root, ["P1"], execute=True, max_seconds=1)
            self.assertEqual(state["phases"]["P1"]["status"], "blocked")
            state["phases"]["P1"]["status"] = "running"
            state["phases"]["P1"]["input_sha256"] = runner.fingerprint(root)
            runner.write_json(root / "data/pipeline/state.json", state)
            state = runner.run(root, ["P1"], execute=True, resume=True)
            self.assertEqual(state["phases"]["P1"]["status"], "failed")

    def test_publish_rejects_other_branch_and_outside_paths(self):
        with patch.object(runner.subprocess, "run") as call:
            call.return_value.stdout = "main\n"
            with self.assertRaises(ValueError):
                runner.publication_request(ROOT)
        class Result:
            def __init__(self, stdout):
                self.stdout = stdout
        with patch.object(runner.subprocess, "run", side_effect=[
            Result(runner.BRANCH + "\n"), Result(b".github/workflows/auto.yml\0"), Result(b""),
        ]):
            with self.assertRaises(ValueError):
                runner.publication_request(ROOT)

    def test_all_actual_manuscripts_build_in_memory(self):
        manifest, _, evidence = core.validate_schemas(ROOT)
        for book in manifest["books"]:
            result = runner.book_build(ROOT, book, evidence, check=True)
            self.assertGreater(result["bytes"], 1000)
            self.assertEqual(result["visual_review"], "not_performed")

    def test_one_local_sample_cannot_count_for_four_countries(self):
        manifest, registry, evidence = core.validate_schemas(ROOT)
        evidence = copy.deepcopy(evidence)
        source = evidence["sources"][0]
        source["access"] = "primary_inspected"
        claim = evidence["claims"][0]
        claim.update(verification="confirmed", category="recommendation")
        claim["context"]["jurisdictions"] = ["cyprus-church"]
        claim["evidence"] = [{
            "jurisdiction_id": "cyprus-church", "family": "eastern_orthodox",
            "status": "confirmed", "locator": "section", "context": claim["context"],
            "source_id": source["id"],
        }]
        with patch.object(core, "validate_schemas", return_value=(manifest, registry, evidence)):
            status, _ = runner.phase_result("P2", ROOT, {}, float("inf"))
        self.assertEqual(status, "blocked")


if __name__ == "__main__":
    unittest.main()
