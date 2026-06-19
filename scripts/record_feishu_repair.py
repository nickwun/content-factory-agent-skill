#!/usr/bin/env python3
"""Record a post-publish Feishu repair in output metadata."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_REPAIR_TYPES = {"cover_repair"}
VALID_STATUSES = {"open", "closed"}


class FeishuRepairError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean value, got: {value}")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FeishuRepairError(f"metadata.json not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeishuRepairError(f"metadata.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeishuRepairError("metadata.json must contain a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_feishu(metadata: dict[str, Any]) -> dict[str, Any]:
    publish = metadata.get("publish")
    if not isinstance(publish, dict):
        publish = {}
        metadata["publish"] = publish
    feishu = publish.get("feishu")
    if not isinstance(feishu, dict):
        feishu = {}
        publish["feishu"] = feishu
    return feishu


def normalize_notes_path(output_dir: Path, notes_path: str) -> str:
    raw = Path(notes_path).expanduser()
    if raw.is_absolute():
        try:
            relative = raw.resolve().relative_to(output_dir)
        except ValueError as exc:
            raise FeishuRepairError("--notes-path must be inside output_dir when absolute") from exc
    else:
        relative = raw
    normalized = relative.as_posix().lstrip("./")
    if not normalized:
        raise FeishuRepairError("--notes-path must not be empty")
    if normalized.startswith("../") or normalized == "..":
        raise FeishuRepairError("--notes-path must not escape output_dir")
    return normalized


def build_repair(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    if args.type not in VALID_REPAIR_TYPES:
        raise FeishuRepairError(f"invalid repair type: {args.type}")
    if args.status not in VALID_STATUSES:
        raise FeishuRepairError(f"invalid repair status: {args.status}")
    notes_path = normalize_notes_path(output_dir, args.notes_path)
    return {
        "type": args.type,
        "status": args.status,
        "createdAt": utc_now(),
        "source": "manual_reconciliation",
        "documentId": args.document_id,
        "documentUrl": args.document_url,
        "originalIssue": {
            "imageUploadResult": args.image_upload_result,
            "coverUploaded": args.cover_uploaded,
        },
        "repairAction": {
            "method": "feishu-cli doc add --index 0 --upload-images",
            "target": "cover",
        },
        "verification": {
            "method": "feishu-cli doc blocks read-only",
            "blocksCount": args.blocks_count,
            "imageBlocksCount": args.image_blocks_count,
            "remoteCoverVisible": args.remote_cover_visible,
        },
        "notesPath": notes_path,
    }


def record_repair(output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    metadata_path = output_dir / "metadata.json"
    metadata = load_json(metadata_path)
    feishu = get_feishu(metadata)
    repair = build_repair(args, output_dir)
    repairs = feishu.get("repairs")
    if repairs is None:
        repairs = []
    if not isinstance(repairs, list):
        raise FeishuRepairError("publish.feishu.repairs must be a list when present")

    match_index = next(
        (
            index
            for index, existing in enumerate(repairs)
            if isinstance(existing, dict)
            and existing.get("type") == repair["type"]
            and existing.get("notesPath") == repair["notesPath"]
        ),
        None,
    )
    if match_index is not None and not args.replace:
        raise FeishuRepairError(
            f"repair already exists for type={repair['type']} notesPath={repair['notesPath']}; pass --replace to update"
        )

    if match_index is None:
        repairs.append(repair)
        action = "appended"
    else:
        repairs[match_index] = repair
        action = "replaced"
    feishu["repairs"] = repairs
    write_json(metadata_path, metadata)
    return {
        "outputDir": str(output_dir),
        "metadataPath": str(metadata_path),
        "action": action,
        "repairCount": len(repairs),
        "repair": repair,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record a Feishu post-publish repair in metadata.publish.feishu.repairs[].")
    parser.add_argument("output_dir")
    parser.add_argument("--type", required=True, choices=sorted(VALID_REPAIR_TYPES))
    parser.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--document-url", required=True)
    parser.add_argument("--notes-path", required=True)
    parser.add_argument("--image-upload-result", required=True)
    parser.add_argument("--cover-uploaded", required=True, type=parse_bool)
    parser.add_argument("--blocks-count", required=True, type=int)
    parser.add_argument("--image-blocks-count", required=True, type=int)
    parser.add_argument("--remote-cover-visible", required=True, type=parse_bool)
    parser.add_argument("--replace", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = record_repair(Path(args.output_dir), args)
    except FeishuRepairError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
