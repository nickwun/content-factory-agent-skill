#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from source_registry import update_markdown_source_status, update_record, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark a normalized source as processing/used/failed/skipped.")
    parser.add_argument("--vault", default="/Users/hui/Documents/ContentFactoryVault")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--status", required=True, choices=["processing", "used", "failed", "skipped", "normalized"])
    parser.add_argument("--output", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--corpus", default="")
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    updates: dict[str, object] = {"status": args.status}
    if args.status == "used":
        updates["usedAt"] = utc_now()
    if args.output:
        updates["usedByOutput"] = args.output
    if args.profile:
        updates["profile"] = args.profile
    if args.corpus:
        updates["corpus"] = args.corpus
    if args.note:
        # Preserve existing notes in the registry update below.
        existing = update_record(vault, args.source_id, {})
        notes = list(existing.get("notes") or [])
        notes.append(args.note)
        updates["notes"] = notes

    record = update_record(vault, args.source_id, updates)
    normalized_path = str(record.get("normalizedPath") or "")
    if normalized_path:
        update_markdown_source_status(
            Path(normalized_path),
            source_status=str(record["status"]),
            used_at=str(record.get("usedAt") or ""),
            used_by_output=str(record.get("usedByOutput") or ""),
        )

    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
