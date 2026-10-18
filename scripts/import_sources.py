#!/usr/bin/env python3
"""Import only explicitly authorized, checksum-pinned files from the manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 15
SCORE_KEYS = {str(number) for number in range(1, 13)}
MANIFEST_ENTRY_KEYS = {
    "id",
    "url",
    "destination",
    "sha256",
    "verified",
    "rights_status",
    "rights_evidence_url",
    "license",
}
CANDIDATE_KEYS = {
    "id",
    "title",
    "author_publisher",
    "edition",
    "language",
    "church",
    "material_type",
    "canonical_url",
    "download_url",
    "rights_status",
    "checked_on",
    "scores",
    "known_score_sum",
    "verified_parameter_count",
}


def _https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def validate_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("Manifest must be an object with version 1.")
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ValueError("Manifest sources must be a list.")

    seen_ids: set[str] = set()
    validated = []
    for source in sources:
        if not isinstance(source, dict) or set(source) != MANIFEST_ENTRY_KEYS:
            raise ValueError("Each source must contain exactly the documented manifest fields.")
        source_id = source["id"]
        if (
            not isinstance(source_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", source_id)
            or source_id in seen_ids
        ):
            raise ValueError("Source ids must be unique lowercase slugs.")
        seen_ids.add(source_id)
        if not _https_url(source["url"]) or not _https_url(source["rights_evidence_url"]):
            raise ValueError(f"{source_id}: source and rights evidence URLs must use HTTPS.")
        if source["verified"] is not True or source["rights_status"] != "authorized":
            raise ValueError(f"{source_id}: source and public redistribution rights must be verified.")
        if not isinstance(source["license"], str) or not source["license"].strip():
            raise ValueError(f"{source_id}: a specific license or permission statement is required.")
        if (
            not isinstance(source["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        ):
            raise ValueError(f"{source_id}: expected a lowercase SHA-256 digest.")

        destination = source["destination"]
        if not isinstance(destination, str) or not destination:
            raise ValueError(f"{source_id}: destination must be a relative path.")
        path = Path(destination)
        if (
            path.is_absolute()
            or any(part in ("", ".", "..") for part in path.parts)
            or path.parts[0] not in ("sources", "books")
        ):
            raise ValueError(f"{source_id}: destination must stay within sources/ or books/.")
        validated.append(source)
    return validated


def _normalize_identity_part(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", normalized, flags=re.UNICODE))


def candidate_identity(candidate: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        _normalize_identity_part(candidate[field])
        for field in ("title", "author_publisher", "edition", "language")
    )


def deduplicate_candidates(
    candidates: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the first bibliographic identity and report later mirrors/formats."""
    unique = []
    duplicates = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        identity = candidate_identity(candidate)
        if identity in seen:
            duplicates.append(candidate)
        else:
            seen.add(identity)
            unique.append(candidate)
    return unique, duplicates


def validate_candidate(candidate: Any) -> None:
    if not isinstance(candidate, dict) or set(candidate) != CANDIDATE_KEYS:
        raise ValueError("Candidate metadata fields do not match the registry schema.")
    for field in (
        "id",
        "title",
        "author_publisher",
        "edition",
        "language",
        "material_type",
        "rights_status",
        "checked_on",
    ):
        if not isinstance(candidate[field], str) or not candidate[field].strip():
            raise ValueError(f"Candidate field {field} must be a non-empty string.")
    if not _https_url(candidate["canonical_url"]):
        raise ValueError("Candidate canonical_url must use HTTPS.")
    if candidate["download_url"] is not None and not _https_url(candidate["download_url"]):
        raise ValueError("Candidate download_url must be HTTPS or null.")
    if not isinstance(candidate["church"], list) or not all(
        isinstance(church, str) and church.strip() for church in candidate["church"]
    ):
        raise ValueError("Candidate church must be a list of non-empty strings.")

    scores = candidate["scores"]
    if not isinstance(scores, dict) or set(scores) != SCORE_KEYS:
        raise ValueError("A candidate must contain exactly 12 score entries.")
    known_sum = 0
    known_count = 0
    for number, result in scores.items():
        if not isinstance(result, dict) or set(result) != {"score", "basis"}:
            raise ValueError(f"Score {number} must contain score and basis.")
        score, basis = result["score"], result["basis"]
        if not isinstance(basis, str) or not basis.strip():
            raise ValueError(f"Score {number} requires an evidence basis or Н/Д explanation.")
        if score is None:
            if not basis.strip().casefold().startswith("н/д"):
                raise ValueError(f"Unknown score {number} must be marked Н/Д.")
        elif isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 5:
            if score == 0 and not basis.strip().casefold().startswith(
                ("подтверждено отсутствие:", "confirmed absence:")
            ):
                raise ValueError(
                    f"Score {number} of 0 requires an explicit confirmed-absence basis."
                )
            known_sum += score
            known_count += 1
        else:
            raise ValueError(f"Score {number} must be an integer from 0 to 5 or null.")
    declared_sum = candidate["known_score_sum"]
    if (
        not isinstance(declared_sum, int)
        or isinstance(declared_sum, bool)
        or declared_sum != known_sum
    ):
        raise ValueError("known_score_sum does not match the known parameter scores.")
    declared_count = candidate["verified_parameter_count"]
    if (
        not isinstance(declared_count, int)
        or isinstance(declared_count, bool)
        or declared_count != known_count
    ):
        raise ValueError("verified_parameter_count does not match the known parameter scores.")


def validate_candidate_registry(registry: Any) -> list[dict[str, Any]]:
    if not isinstance(registry, dict) or not isinstance(registry.get("candidates"), list):
        raise ValueError("Candidate registry must contain a candidates list.")
    candidates = registry["candidates"]
    ids: set[str] = set()
    for candidate in candidates:
        validate_candidate(candidate)
        if candidate["id"] in ids:
            raise ValueError("Candidate ids must be unique.")
        ids.add(candidate["id"])
    unique, duplicates = deduplicate_candidates(candidates)
    if duplicates:
        raise ValueError("Candidate registry contains duplicate bibliographic identities.")
    return unique


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _destination_path(root: Path, destination: str) -> Path:
    target = root / destination
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_root):
        raise ValueError("Destination resolves outside the repository root.")
    return target


def _download(source: dict[str, Any], target: Path) -> int:
    request = urllib.request.Request(
        source["url"], headers={"User-Agent": "orthodo-source-import/1.0"}
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    with opener.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        if response.geturl() != source["url"]:
            raise ValueError("Redirected source URLs are not accepted.")
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > MAX_DOWNLOAD_BYTES:
            raise ValueError("Source exceeds the maximum allowed download size.")
        content = bytearray()
        digest = hashlib.sha256()
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            content.extend(chunk)
            digest.update(chunk)
            if len(content) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Source exceeds the maximum allowed download size.")

    if digest.hexdigest() != source["sha256"]:
        raise ValueError("Downloaded content does not match the manifest SHA-256.")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".import-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return len(content)


def run(
    manifest_path: Path,
    root: Path,
    execute: bool = False,
    opener: Callable[..., Any] | None = None,
    output: Any = None,
) -> int:
    import sys

    output = output or sys.stdout
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = validate_manifest(manifest)
    if not sources:
        print("No authorized sources in manifest; no network requests or writes.", file=output)
        return 0
    if not execute:
        for source in sources:
            print(
                f"DRY-RUN {source['id']}: {source['url']} -> {source['destination']} "
                f"(verified HTTPS; rights: {source['license']}; SHA-256 pinned)",
                file=output,
            )
        print("No network requests or files written. Pass --execute to import.", file=output)
        return 0

    # The opener parameter is injectable only for tests; production always rejects redirects.
    for source in sources:
        target = _destination_path(root, source["destination"])
        if opener is None:
            count = _download(source, target)
        else:
            count = opener(source, target)
        print(f"IMPORTED {source['id']}: {count} bytes -> {target}", file=output)
    return 0


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repository_root / "sources-manifest.json",
        help="JSON manifest of explicitly authorized HTTPS files.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform verified downloads and write files (default is dry-run).",
    )
    arguments = parser.parse_args()
    try:
        return run(arguments.manifest, repository_root, execute=arguments.execute)
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
