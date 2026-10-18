#!/usr/bin/env python3
"""Finite, local-only manual runner; dry-run unless --execute is supplied."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import book_pipeline as core
import build_word_third_attempt as word


ROOT = Path(__file__).resolve().parents[1]
PHASES = tuple(f"P{i}" for i in range(7))
STATES = {"pending", "running", "passed", "blocked", "failed", "needs_human"}
BRANCH = "copilot/third-attempt-book-research"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise ValueError("Refusing symlink state output")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def fingerprint(root):
    paths = [
        root / "books/manifest.json", root / "data/evidence.json",
        root / "data/jurisdictions.json",
        *sorted((root / "books").glob("*/manuscript.md")),
        *sorted((root / "books").glob("*/sources.json")),
        *sorted((root / "scripts").glob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def book_build(root, book, evidence, write=False, check=False):
    source_path = core.project_path(root, book["manuscript"], "books")
    metadata = core.read_json(core.project_path(root, book["sources"], "books"))
    source_map = {source["id"]: source for source in evidence["sources"]}
    if metadata["book_id"] != book["id"] or not set(metadata["source_ids"]) <= source_map.keys():
        raise ValueError("Book source identity mismatch")
    claim_map = {claim["id"]: claim for claim in evidence["claims"]}
    if not set(metadata["claim_ids"]) <= claim_map.keys():
        raise ValueError("Missing book claim")
    verified = metadata["verified_claim_ids"]
    if not set(verified) <= set(metadata["claim_ids"]):
        raise ValueError("Verified book claim outside source list")
    if any(claim_map[c]["verification"] != "confirmed" for c in verified):
        raise ValueError("Unverified claim promoted in book")
    if book["id"] == "universal" and verified:
        registry = core.read_json(root / "data/jurisdictions.json")
        if any(core.intersection(claim_map[c], registry, evidence["sources"]) != "confirmed" for c in verified):
            raise ValueError("Universal verified claim lacks a confirmed full-scope intersection")
    if book["verified_claims"] != len(verified):
        raise ValueError("Book verified counter differs from evidence")
    text = source_path.read_text(encoding="utf-8")
    title = [line for line in text.splitlines() if line.strip()][:6]
    if title[0] != "Гаврилов Леонид Геннадьевич" or title[1] != book["title"] or title[3:] != ["Монография", "2026", "Рабочая исследовательская редакция"]:
        raise ValueError("Wrong cover identity")
    parallel = book["id"] == "universal"
    blocks = word.parse(text, title=title, require_table=parallel)
    if not parallel and any(kind == "table" for kind, _ in blocks):
        raise ValueError("Country books must not have artificial parallel columns")
    prayer_ids = metadata["prayer_source_ids"]
    if any(source_id not in metadata["source_ids"] or not core.publishable(source_map[source_id]) for source_id in prayer_ids):
        raise ValueError("Prayer full-text rights/provenance not established")
    if "[prayer] " in text and not prayer_ids:
        raise ValueError("Prayer marker without a rights-cleared source")
    content, expected = word.build(blocks, landscape=parallel)
    word.validate(content, expected, require_table=parallel, landscape=parallel)
    coverage = word.font_coverage("".join(expected) + "Α\u0313\u0301 Ω\u0342 Ἀ ᾠ")
    output = core.project_path(root, book["docx"], "deliverables")
    if check:
        if output.read_bytes() != content:
            raise ValueError("Existing DOCX differs from reproducible source")
    if write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    return {
        "id": book["id"], "status": "draft_layout_not_verified_guide",
        "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        "source_sha256": core.source_digest(source_path),
        "fonts": coverage, "visual_review": "not_performed",
    }


def phase_result(phase, root, state, deadline):
    if phase == "P0":
        for name in ("CLAUDE.md", "current-task.md", "decisions.md", "progress.md"):
            if not (root / name).is_file():
                return "blocked", f"Missing approved project file: {name}"
        return "passed", "Approved specification and prior audit available; no new network operation"
    manifest, registry, evidence = core.validate_schemas(root)
    if phase == "P1":
        for name in ("HLD.md", "phases.md", "scope.md", "five-book-plan.md", "evidence-matrix.md", "jurisdictions.md"):
            if not (root / "docs" / name).is_file():
                return "blocked", f"Missing architecture output: {name}"
        return "passed", "Five IDs, scope and evidence schemas validated; not substantive books"
    if phase == "P2":
        source_map = {s["id"]: s for s in evidence["sources"]}
        jurisdiction_map = {j["id"]: j for j in registry["jurisdictions"]}
        country_jurisdictions = {
            "armenia": "armenian-apostolic", "bulgaria": "bulgarian-orthodox",
            "cyprus": "cyprus-church", "russia": "russian-orthodox",
        }
        countries = set()
        for claim in evidence["claims"]:
            if claim["verification"] != "confirmed" or claim["category"] == "unknown":
                continue
            for item in claim["evidence"]:
                jurisdiction = jurisdiction_map[item["jurisdiction_id"]]
                if (item["status"] == "confirmed" and item["locator"]
                        and item["context"] == claim["context"]
                        and item["family"] == jurisdiction["family"]
                        and source_map[item["source_id"]]["access"] == "primary_inspected"):
                    countries.update(
                        book for book, jid in country_jurisdictions.items()
                        if item["jurisdiction_id"] == jid and book in claim["books"]
                        and jid in claim["context"]["jurisdictions"]
                    )
        if countries != core.BOOK_IDS - {"universal"}:
            return "blocked", "No primary-inspected applicable sample for every country; DNS/rights verification pending"
        return "needs_human", "Candidate sample present; editor must check local applicability and text rights"
    if phase == "P3":
        results = {c["id"]: core.intersection(c, registry, evidence["sources"]) for c in evidence["claims"]}
        state["intersection"] = results
        if not registry["scope_closed"] or not any(value == "confirmed" for value in results.values()):
            return "blocked", "Jurisdiction registry open or no fully confirmed contextual intersection"
        return "needs_human", "Intersection candidates require comparative editorial interpretation"
    if phase == "P4":
        if any(b["verified_claims"] == 0 or b["status"] != "verified_working_text" for b in manifest["books"]):
            return "blocked", "Five authorial research drafts are not five verified substantive guides"
        return "needs_human", "Substantive manuscripts require source and language editorial approval"
    if phase == "P5":
        artifacts = []
        for book in manifest["books"]:
            if time.monotonic() >= deadline:
                return "blocked", "Time budget reached during layout stage; resume available"
            artifacts.append(book_build(root, book, evidence, write=True))
            state["artifacts"] = artifacts
        return "needs_human", "Five DOCX draft layouts built and structurally checked; rendering and substantive acceptance pending"
    incomplete = [
        p for p in PHASES[:-1]
        if state["phases"][p]["status"] != "passed"
    ]
    if incomplete:
        return "blocked", "Not finished guides; incomplete acceptance phases: " + ", ".join(incomplete)
    return "needs_human", "Final editorial, visual and publication confirmation required"


def publication_request(root):
    branch = subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if branch != BRANCH:
        raise ValueError("Publication only in the assigned PR branch")
    paths = []
    for args in (["diff", "--name-only", "-z", "HEAD"], ["ls-files", "--others", "--exclude-standard", "-z"]):
        output = subprocess.run(
            ["git", "-C", str(root), *args], check=True,
            capture_output=True, timeout=10,
        ).stdout.decode()
        paths.extend(p for p in output.split("\0") if p)
    allowed_roots = {"books", "docs", "data", "deliverables", "scripts", "tests"}
    allowed_files = {"CLAUDE.md", "current-task.md", "decisions.md", "progress.md", ".gitignore"}
    for relative in paths:
        path = Path(relative)
        if path.parts[0] not in allowed_roots and relative not in allowed_files:
            raise ValueError(f"Publication outside project allowlist: {relative}")
        if any(p in {".git", "__pycache__"} for p in path.parts) or path.suffix in {".pyc", ".tmp"}:
            raise ValueError("Publication contains generated debris")
        if not (root / path).resolve().is_relative_to(root.resolve()):
            raise ValueError("Publication symlink escapes project")
    return {
        "status": "needs_human", "branch": branch, "paths": sorted(set(paths)),
        "reason": "Explicit request only: secret scan and PR publication tool required; no commit or push executed",
    }


def run(root, phases, *, execute=False, resume=False, retry_reason=None,
        max_seconds=1200, max_sources=100, max_attempts=3, publish=False):
    root = Path(root).resolve()
    if not math.isfinite(max_seconds) or max_seconds <= 0 or max_sources < 0 or not 1 <= max_attempts <= 3:
        raise ValueError("Finite positive time, nonnegative source budget, attempts 1..3 required")
    if not phases or not set(phases) <= set(PHASES):
        raise ValueError("Unknown phase")
    if publish and not execute:
        raise ValueError("--publish requires --execute; dry-run never publishes or writes")
    if not execute:
        return {
            "mode": "dry-run", "phases": phases, "network_requests": 0,
            "writes": 0, "publication": False,
            "limits": {"seconds": max_seconds, "new_sources": max_sources, "attempts": max_attempts},
        }
    state_path = core.project_path(root, "data/pipeline/state.json", "data")
    events_path = core.project_path(root, "data/pipeline/progress.json", "data")
    if state_path.exists() and not resume:
        raise ValueError("State already exists; use --resume to preserve attempt history")
    state = core.read_json(state_path) if resume and state_path.exists() else {
        "schema_version": 1, "phases": {
            p: {"status": "pending", "attempts": 0, "reason": "Not executed"} for p in PHASES
        }, "source_ids": [], "events": [], "network_requests": 0,
    }
    if not set(PHASES) <= state["phases"].keys() or any(v["status"] not in STATES for v in state["phases"].values()):
        raise ValueError("Invalid persisted state")
    deadline = time.monotonic() + max_seconds
    current_fingerprint = fingerprint(root)
    for record in state["phases"].values():
        if record["status"] == "passed" and record.get("input_sha256") != current_fingerprint:
            record.update(status="pending", reason="Inputs changed; previous acceptance is stale")
    sources = core.read_json(root / "data/evidence.json")["sources"]
    new_sources = {s["id"] for s in sources} - set(state["source_ids"])

    def transition(phase, status, reason):
        record = state["phases"][phase]
        record.update(status=status, reason=reason, updated_at=now())
        state["events"].append({"phase": phase, "status": status, "reason": reason, "at": now()})
        write_json(state_path, state)
        write_json(events_path, {"events": state["events"], "network_requests": 0})

    for phase in PHASES:
        if phase not in phases:
            continue
        record = state["phases"][phase]
        if record["status"] == "running":
            transition(phase, "failed", "Interrupted previous execution; explicit retry with a new hypothesis required")
        unchanged = record.get("input_sha256") == current_fingerprint
        if resume and unchanged and record["status"] == "passed":
            continue
        if resume and record["status"] in {"blocked", "failed", "needs_human"} and unchanged and not retry_reason:
            continue
        if record["attempts"] >= max_attempts:
            transition(phase, "blocked", "Attempt limit reached; no automatic retry")
            continue
        if time.monotonic() >= deadline:
            transition(phase, "blocked", "Manual run time budget reached")
            break
        if len(new_sources) > max_sources and phase in {"P2", "P3"}:
            transition(phase, "blocked", "New source budget exceeded; independent local layout remains available")
            continue
        record["attempts"] += 1
        record["input_sha256"] = current_fingerprint
        transition(phase, "running", retry_reason or "Single finite local attempt")
        try:
            status, reason = phase_result(phase, root, state, deadline)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            status, reason = "failed", f"{type(exc).__name__}: {exc}"
        transition(phase, status, reason)
    if len(new_sources) <= max_sources:
        state["source_ids"] = sorted({s["id"] for s in sources})
    state["updated_at"] = now()
    state["limits"] = {"seconds": max_seconds, "new_sources": max_sources, "attempts": max_attempts}
    if publish:
        state["publication"] = publication_request(root)
    write_json(state_path, state)
    write_json(events_path, {"events": state["events"], "network_requests": 0})
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--phase", action="append", choices=PHASES)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-reason")
    parser.add_argument("--max-seconds", type=float, default=1200)
    parser.add_argument("--max-sources", type=int, default=100)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    result = run(
        ROOT, args.phase or list(PHASES), execute=args.execute, resume=args.resume,
        retry_reason=args.retry_reason, max_seconds=args.max_seconds,
        max_sources=args.max_sources, max_attempts=args.max_attempts, publish=args.publish,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.execute:
        selected = args.phase or PHASES
        if any(result["phases"][p]["status"] in {"failed", "blocked", "needs_human"} for p in selected):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
