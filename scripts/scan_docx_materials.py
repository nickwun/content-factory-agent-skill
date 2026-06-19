#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from source_registry import count_by_status, discover_docx_files, upsert_docx_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan docx-raw and update source-registry.json.")
    parser.add_argument("--vault", default="/Users/hui/Documents/ContentFactoryVault")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    files = discover_docx_files(vault)
    registry = upsert_docx_records(vault, files)
    duplicate_hash_count = len(
        {
            item["contentHash"]
            for item in registry
            if any(str(note).startswith("possible_duplicate") for note in item.get("notes", []))
        }
    )
    print(
        json.dumps(
            {
                "scanned": len(files),
                "registryPath": str(vault / "01-Materials" / "source-registry.json"),
                "statusCounts": count_by_status(registry),
                "duplicateHashCount": duplicate_hash_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
