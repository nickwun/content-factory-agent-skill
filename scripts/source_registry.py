from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REGISTRY_RELATIVE_PATH = Path("01-Materials") / "source-registry.json"
VALID_STATUSES = {"unused", "normalized", "processing", "used", "failed", "skipped"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def registry_path(vault: Path) -> Path:
    return vault / REGISTRY_RELATIVE_PATH


def read_registry(vault: Path) -> list[dict[str, Any]]:
    path = registry_path(vault)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("source-registry.json must contain a list.")
    return payload


def write_registry(vault: Path, registry: list[dict[str, Any]]) -> None:
    path = registry_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def stable_source_id(path: Path, content_hash: str) -> str:
    digest = hashlib.sha256(f"{path.resolve()}|{content_hash}".encode("utf-8")).hexdigest()
    return f"source-{digest[:16]}"


def normalize_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def create_record(path: Path) -> dict[str, Any]:
    content_hash = file_hash(path)
    return {
        "sourceId": stable_source_id(path, content_hash),
        "fileName": path.name,
        "rawPath": normalize_path(path),
        "normalizedPath": "",
        "contentHash": content_hash,
        "title": "",
        "status": "unused",
        "usedAt": "",
        "usedByOutput": "",
        "profile": "",
        "corpus": "",
        "notes": [],
    }


def index_by_source_id(registry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("sourceId", "")): item for item in registry if item.get("sourceId")}


def find_record_by_raw_path(registry: list[dict[str, Any]], raw_path: Path) -> dict[str, Any] | None:
    target = normalize_path(raw_path)
    return next((item for item in registry if item.get("rawPath") == target), None)


def upsert_docx_records(vault: Path, files: list[Path]) -> list[dict[str, Any]]:
    registry = read_registry(vault)
    by_raw_path = {str(item.get("rawPath", "")): item for item in registry}

    for path in files:
        raw_path = normalize_path(path)
        next_record = create_record(path)
        existing = by_raw_path.get(raw_path)
        if existing:
            previous_status = existing.get("status") or "unused"
            existing.update(
                {
                    "fileName": next_record["fileName"],
                    "rawPath": next_record["rawPath"],
                    "contentHash": next_record["contentHash"],
                    "sourceId": next_record["sourceId"],
                }
            )
            if previous_status in {"", None}:
                existing["status"] = "unused"
        else:
            registry.append(next_record)

    mark_duplicate_hashes(registry)
    write_registry(vault, registry)
    return registry


def mark_duplicate_hashes(registry: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for item in registry:
        content_hash = str(item.get("contentHash", ""))
        if content_hash:
            counts[content_hash] = counts.get(content_hash, 0) + 1

    for item in registry:
        notes = list(item.get("notes") or [])
        notes = [note for note in notes if not str(note).startswith("possible_duplicate")]
        content_hash = str(item.get("contentHash", ""))
        if content_hash and counts.get(content_hash, 0) > 1:
            notes.append(f"possible_duplicate: same contentHash appears {counts[content_hash]} times")
        item["notes"] = notes


def update_record(
    vault: Path,
    source_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    registry = read_registry(vault)
    record = index_by_source_id(registry).get(source_id)
    if not record:
        raise ValueError(f"sourceId not found: {source_id}")
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid status: {updates['status']}")
    record.update(updates)
    mark_duplicate_hashes(registry)
    write_registry(vault, registry)
    return record


def ensure_record_for_file(vault: Path, source: Path) -> dict[str, Any]:
    registry = upsert_docx_records(vault, [source])
    record = find_record_by_raw_path(registry, source)
    if not record:
        raise ValueError(f"failed to register source: {source}")
    return record


def parse_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---\n", 4)
    if end < 0:
        return {}, markdown
    frontmatter_text = markdown[4:end]
    body = markdown[end + 5 :]
    data: dict[str, str] = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data, body


def dump_frontmatter(data: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in data.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def upsert_markdown_frontmatter(markdown: str, updates: dict[str, str]) -> str:
    existing, body = parse_frontmatter(markdown)
    merged = {**existing, **updates}
    return dump_frontmatter(merged) + body.lstrip()


def update_markdown_source_status(
    markdown_path: Path,
    *,
    source_status: str,
    used_at: str = "",
    used_by_output: str = "",
) -> None:
    markdown = markdown_path.read_text(encoding="utf-8")
    markdown_path.write_text(
        upsert_markdown_frontmatter(
            markdown,
            {
                "sourceStatus": source_status,
                "usedAt": used_at,
                "usedByOutput": used_by_output,
            },
        ),
        encoding="utf-8",
    )


def discover_docx_files(vault: Path) -> list[Path]:
    raw_dir = vault / "01-Materials" / "docx-raw"
    return sorted(path for path in raw_dir.glob("*.docx") if not path.name.startswith("~$"))


def count_by_status(registry: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in sorted(VALID_STATUSES)}
    for item in registry:
        status = str(item.get("status") or "unused")
        counts[status] = counts.get(status, 0) + 1
    return counts


def slug_from_output(value: str) -> str:
    return re.sub(r"\s+", "-", value.strip())
