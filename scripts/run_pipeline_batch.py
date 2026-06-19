#!/usr/bin/env python3
"""Run a small serial ContentFactory article pipeline batch."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_single_article_pipeline import PipelineError, article_char_count, run_pipeline
from source_registry import read_registry


DEFAULT_VAULT = Path("/Users/hui/Documents/ContentFactoryVault")


def registry_counts(vault: Path) -> dict[str, int]:
    registry = read_registry(vault)
    counts = Counter(str(item.get("status") or "unused") for item in registry)
    return dict(sorted(counts.items()))


def choose_source_ids(vault: Path, explicit: list[str], limit: int) -> list[str]:
    if explicit:
        return explicit[:limit]
    selected: list[str] = []
    for record in read_registry(vault):
        if str(record.get("status") or "unused") == "unused":
            selected.append(str(record["sourceId"]))
        if len(selected) >= limit:
            break
    return selected


def batch_summary_path(outputs_root: Path) -> Path:
    batch_dir = outputs_root / "batch-runs"
    batch_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    index = 1
    while True:
        path = batch_dir / f"{date}-pipeline-batch-{index:02d}.md"
        if not path.exists():
            return path
        index += 1


def read_cover_status(output_dir: str) -> str:
    if not output_dir:
        return ""
    metadata_path = Path(output_dir) / "metadata.json"
    if not metadata_path.exists():
        return ""
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(metadata.get("images", {}).get("cover", {}).get("status", ""))


def read_titles_status(output_dir: str) -> str:
    if not output_dir:
        return ""
    metadata_path = Path(output_dir) / "metadata.json"
    titles_path = Path(output_dir) / "titles.md"
    if not metadata_path.exists() or not titles_path.exists():
        return "missing"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "invalid"
    titles = metadata.get("titles")
    if not isinstance(titles, dict):
        return "missing"
    pain = titles.get("pain_point")
    gap = titles.get("cognitive_gap")
    recommended = titles.get("recommended")
    if (
        isinstance(pain, list)
        and len([item for item in pain if str(item).strip()]) == 5
        and isinstance(gap, list)
        and len([item for item in gap if str(item).strip()]) == 5
        and isinstance(recommended, dict)
        and recommended.get("primary")
        and recommended.get("secondary")
        and recommended.get("reason")
    ):
        return "generated"
    return "incomplete"


def read_article_chars(output_dir: str) -> int | None:
    if not output_dir:
        return None
    article_path = Path(output_dir) / "article.md"
    if not article_path.exists():
        return None
    return article_char_count(article_path.read_text(encoding="utf-8"))


def is_safe_output_dir(output_dir: str) -> bool:
    if not output_dir:
        return False
    name = Path(output_dir).name
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$", name))


def write_batch_summary(vault: Path, results: list[dict[str, Any]], counts: dict[str, int]) -> Path:
    path = batch_summary_path(vault / "04-Outputs")
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lines = [
        "---",
        "type: pipeline_batch_summary",
        f"created_at: {created_at}",
        f"item_count: {len(results)}",
        "---",
        "",
        "# Pipeline Batch Summary",
        "",
        "| sourceId | status | outputDir | chars | titlesStatus | coverStatus | error |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for result in results:
        output = result.get("outputDir") or ""
        chars = "" if result.get("chars") is None else str(result["chars"])
        error = str(result.get("error") or "").replace("\n", " ")
        lines.append(
            f"| {result['sourceId']} | {result['status']} | {output} | {chars} | {result.get('titlesStatus', '')} | {result.get('coverStatus', '')} | {error} |"
        )
    lines.extend(["", "## Registry Status", ""])
    for status, count in counts.items():
        lines.append(f"- {status}: {count}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def make_single_args(args: argparse.Namespace, source_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        vault=args.vault,
        source_id=source_id,
        profile=args.profile,
        corpus=args.corpus,
        env_file=args.env_file,
        text_model=args.text_model,
        article_attempts=args.article_attempts,
        cover_provider=args.cover_provider,
        cover_model=args.cover_model,
    )


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    vault = Path(args.vault).expanduser().resolve()
    limit = min(max(args.limit, 1), 5)
    source_ids = choose_source_ids(vault, args.source_id, limit)
    if not source_ids:
        raise PipelineError("No unused sources selected for pipeline batch.")

    results: list[dict[str, Any]] = []
    for source_id in source_ids:
        try:
            payload = run_pipeline(make_single_args(args, source_id))
            output_dir = str(payload.get("outputDir") or "")
            safe = is_safe_output_dir(output_dir)
            if not safe:
                raise PipelineError(f"unsafe output directory name: {output_dir}")
            results.append(
                {
                    "sourceId": source_id,
                    "status": "success",
                    "outputDir": output_dir,
                    "chars": read_article_chars(output_dir),
                    "titlesStatus": str(payload.get("titlesStatus") or read_titles_status(output_dir)),
                    "coverStatus": str(payload.get("coverStatus") or read_cover_status(output_dir)),
                    "error": "",
                }
            )
        except Exception as exc:
            output_dir = find_latest_output_for_source(vault, source_id)
            results.append(
                {
                    "sourceId": source_id,
                    "status": "failed",
                    "outputDir": output_dir,
                    "chars": read_article_chars(output_dir),
                    "titlesStatus": read_titles_status(output_dir),
                    "coverStatus": read_cover_status(output_dir),
                    "error": str(exc),
                }
            )
    counts = registry_counts(vault)
    summary = write_batch_summary(vault, results, counts)
    return {
        "summaryPath": str(summary),
        "sourceIds": source_ids,
        "results": results,
        "registryStatus": counts,
    }


def find_latest_output_for_source(vault: Path, source_id: str) -> str:
    candidates: list[Path] = []
    for metadata_path in (vault / "04-Outputs").glob("*/metadata.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for item in metadata.get("sourceMaterials", []) or []:
            if isinstance(item, dict) and item.get("sourceId") == source_id:
                candidates.append(metadata_path.parent)
                break
            if isinstance(item, str) and item == source_id:
                candidates.append(metadata_path.parent)
                break
    if not candidates:
        return ""
    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run up to 5 ContentFactory single-article pipelines serially.")
    parser.add_argument("--vault", default=str(DEFAULT_VAULT))
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--profile", default=str(DEFAULT_VAULT / "02-Profiles" / "ahong-running-rewrite.md"))
    parser.add_argument("--corpus", default=str(DEFAULT_VAULT / "03-Corpus" / "ahong-running-style.md"))
    parser.add_argument("--env-file", default="")
    parser.add_argument("--text-model", default="", help="Deprecated; external LLM article generation is disabled.")
    parser.add_argument("--article-attempts", type=int, default=5)
    parser.add_argument("--cover-provider", default="codex-imagegen")
    parser.add_argument("--cover-model", default="")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    try:
        result = run_batch(args)
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
