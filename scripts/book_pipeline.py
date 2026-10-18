"""Evidence gates shared by the manual five-book pipeline and its tests."""

import hashlib
import json
from pathlib import Path
import re


BOOK_IDS = {"universal", "armenia", "bulgaria", "cyprus", "russia"}
CATEGORIES = {
    "mandatory", "forbidden", "discouraged", "permission_required",
    "conditional", "recommendation", "unknown",
}
VERIFICATIONS = {"confirmed", "refuted", "unknown", "conflict"}
FAMILIES = {"eastern_orthodox", "oriental_orthodox"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def project_path(root, relative, prefix):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative:
        raise ValueError("Missing project path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix:
        raise ValueError(f"Unsafe project path: {relative}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root / prefix):
        raise ValueError(f"Symlink escapes project directory: {relative}")
    return resolved


def validate_schemas(root):
    root = Path(root)
    manifest = read_json(root / "books/manifest.json")
    registry = read_json(root / "data/jurisdictions.json")
    evidence = read_json(root / "data/evidence.json")
    books = manifest["books"]
    if len(books) != 5 or {b["id"] for b in books} != BOOK_IDS:
        raise ValueError("Exactly five distinct book IDs required")
    for book in books:
        for key, prefix in (("manuscript", "books"), ("sources", "books"), ("docx", "deliverables")):
            project_path(root, book[key], prefix)
        if book["layout"] != ("parallel_three_columns" if book["id"] == "universal" else "single_column"):
            raise ValueError("Wrong book layout")
    jurisdictions = registry["jurisdictions"]
    if len({j["id"] for j in jurisdictions}) != len(jurisdictions):
        raise ValueError("Duplicate jurisdiction ID")
    if type(registry["scope_closed"]) is not bool:
        raise ValueError("scope_closed must be boolean")
    territories = registry["territories"] + registry["comparative_territories"]
    if len(set(registry["territories"])) != 20:
        raise ValueError("Twenty scope territories required")
    for jurisdiction in jurisdictions:
        required = {"self_name", "family", "territories", "administrative_status",
                    "governing_body", "mother_church", "primacy", "recognition",
                    "communion", "source_ids", "verification", "checked_at"}
        if not required <= jurisdiction.keys() or jurisdiction["family"] not in FAMILIES:
            raise ValueError("Incomplete jurisdiction or wrong church family")
        if jurisdiction["verification"] not in VERIFICATIONS:
            raise ValueError("Unknown jurisdiction verification state")
        if not jurisdiction["territories"] or not set(jurisdiction["territories"]) <= set(territories):
            raise ValueError("Jurisdiction outside scope")
        for relation in jurisdiction["recognition"] + jurisdiction["communion"]:
            if not {"subject", "relation", "position", "as_of", "source_id", "locator", "verification"} <= relation.keys():
                raise ValueError("Dated viewpoint and source required for relations")
    sources = evidence["sources"]
    source_ids = {s["id"] for s in sources}
    if len(source_ids) != len(sources):
        raise ValueError("Duplicate source ID")
    for source in sources:
        if not {"url", "edition", "access", "checked_at", "rights", "sha256"} <= source.keys():
            raise ValueError("Incomplete source record")
        if not source["url"].startswith("https://"):
            raise ValueError("Only HTTPS sources in executable registry")
    for jurisdiction in jurisdictions:
        if not set(jurisdiction["source_ids"]) <= source_ids:
            raise ValueError("Missing jurisdiction source")
    claims = evidence["claims"]
    if len({c["id"] for c in claims}) != len(claims):
        raise ValueError("Duplicate claim ID")
    jid = {j["id"]: j for j in jurisdictions}
    for claim in claims:
        required = {"wording", "books", "category", "context", "source_ids", "locator",
                    "quotation", "translation", "verification", "checked_at",
                    "evidence", "exceptions", "local_custom"}
        if not required <= claim.keys():
            raise ValueError("Incomplete claim")
        if claim["category"] not in CATEGORIES or claim["verification"] not in VERIFICATIONS:
            raise ValueError("Invalid claim category or verification")
        if not set(claim["books"]) <= BOOK_IDS or not set(claim["source_ids"]) <= source_ids:
            raise ValueError("Invalid book/source reference")
        context = claim["context"]
        if not {"jurisdictions", "place", "service_time", "visitor_status", "gender_age", "accessibility"} <= context.keys():
            raise ValueError("Missing claim context")
        if not set(context["jurisdictions"]) <= jid.keys():
            raise ValueError("Invalid context jurisdiction")
        if type(claim["local_custom"]) is not bool:
            raise ValueError("local_custom must be boolean")
        for item in claim["evidence"]:
            if not {"jurisdiction_id", "family", "status", "source_id", "locator",
                    "quotation", "translation", "context", "checked_at", "applicability"} <= item.keys():
                raise ValueError("Incomplete evidence")
            if item["jurisdiction_id"] not in jid or item["source_id"] not in source_ids:
                raise ValueError("Dangling evidence reference")
            if item["family"] != jid[item["jurisdiction_id"]]["family"] or item["status"] not in VERIFICATIONS:
                raise ValueError("Evidence church family/status mismatch")
    return manifest, registry, evidence


def deduplicate(records):
    """Only verified identities or matching nonempty content hashes justify grouping."""
    result, seen = [], {}
    for record in records:
        digest = record.get("sha256")
        identity = tuple(record.get(key) for key in ("work", "edition", "language"))
        key = ("sha", digest) if digest else ("identity", identity) if all(identity) else None
        if key is not None and key in seen:
            seen[key]["duplicates"].append(record["id"])
        else:
            row = dict(record, duplicates=[])
            result.append(row)
            if key is not None:
                seen[key] = row
    return result


def completeness(values):
    if not values:
        return 0.0
    if any(value is not None and value not in VERIFICATIONS for value in values):
        raise ValueError("Not a verification state")
    return 100 * sum(value in {"confirmed", "refuted"} for value in values) / len(values)


def combine(states):
    if not states:
        return "unknown"
    if "conflict" in states or ("confirmed" in states and "refuted" in states):
        return "conflict"
    if "refuted" in states:
        return "refuted"
    if "unknown" in states:
        return "unknown"
    if set(states) != {"confirmed"}:
        raise ValueError("Invalid verification states")
    return "confirmed"


def context_confirmed(context):
    required = ("place", "service_time", "accessibility")
    unknown = {"unknown", "unspecified", "pending", "not_checked"}
    if not context.get("jurisdictions") or not all(
        isinstance(context.get(key), str) and context[key].strip()
        and context[key] not in unknown for key in required
    ):
        return False
    if context.get("visitor_status") not in {
        "visitor", "unbaptized_visitor", "catechumen", "baptized_member",
    }:
        return False
    return bool(context.get("gender_age")) or context.get("gender_age_status") == "not_distinguished_by_source"


def intersection(claim, registry, sources):
    """A conservative contextual gate, not automated ecclesiastical interpretation."""
    jurisdictions = registry["jurisdictions"]
    if not registry["scope_closed"] or not jurisdictions:
        return "unknown"
    if any(j["verification"] != "confirmed" for j in jurisdictions):
        return "unknown"
    covered = {t for j in jurisdictions for t in j["territories"]}
    if not set(registry["territories"] + registry["comparative_territories"]) <= covered:
        return "unknown"
    if claim["verification"] != "confirmed":
        return claim["verification"]
    if claim["exceptions"] or claim["local_custom"] or claim["category"] == "unknown":
        return "unknown"
    if not context_confirmed(claim["context"]):
        return "unknown"
    if set(claim["context"]["jurisdictions"]) != {j["id"] for j in jurisdictions}:
        return "unknown"
    source_map = {s["id"]: s for s in sources}
    states = []
    for jurisdiction in jurisdictions:
        matches = [
            e for e in claim["evidence"]
            if e["jurisdiction_id"] == jurisdiction["id"]
        ]
        checked = []
        for evidence in matches:
            source = source_map.get(evidence["source_id"], {})
            valid = (
                evidence["family"] == jurisdiction["family"]
                and evidence["applicability"] == "jurisdiction"
                and evidence["context"] == claim["context"]
                and evidence["locator"]
                and source.get("access") == "primary_inspected"
            )
            checked.append(evidence["status"] if valid else "unknown")
        states.append(combine(checked))
    return combine(states)


def publishable(source):
    """Full text permission, distinct from permission to retain bibliographic metadata."""
    required = ("edition", "attribution", "license", "checked_at", "url", "sha256")
    if source.get("access") != "primary_inspected" or source.get("rights") not in {"public_domain", "explicit_permission", "open_license"}:
        return False
    if not all(source.get(field) for field in required):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", source["sha256"]):
        return False
    return not source.get("git_url") or bool(re.fullmatch(r"[0-9a-f]{40}", source.get("commit_sha") or ""))


def source_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
