#!/usr/bin/env python3
"""Validate ContentFactory title candidates and report Codex title tasks when missing.

This script intentionally does not generate titles with external LLM APIs. Codex
should write titles.md and metadata.titles directly; this script verifies that
the expected title state exists before downstream build/publish steps continue.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class TitleGenerationError(RuntimeError):
    def __init__(self, message: str, *, status: str = "codex_title_required") -> None:
        super().__init__(message)
        self.status = status


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise TitleGenerationError(f"metadata.json not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TitleGenerationError(f"metadata.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TitleGenerationError("metadata.json must contain a JSON object.")
    return payload


def first_title(article: str) -> str:
    for line in article.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def module_titles(article: str) -> list[str]:
    return [match.strip() for match in re.findall(r"^##\s*0[1-9]、(.+)$", article, flags=re.M)]


def compact_article(article: str, max_chars: int = 1600) -> str:
    body = article.strip()
    return body if len(body) <= max_chars else body[:max_chars] + "\n\n[后文已截断，仅用于 Codex 补标题任务说明。]"


def build_codex_title_task(output_dir: Path, article: str, metadata: dict[str, Any]) -> str:
    title = str(metadata.get("title") or first_title(article) or "").strip()
    modules = "、".join(module_titles(article)[:5]) or "未检测到 01/02/03 模块标题"
    return "\n".join(
        [
            "codex_title_required",
            "",
            f"Output directory: {output_dir}",
            "",
            "请由 Codex 基于 article.md 手动写入标题候选，不要调用外部 LLM API。",
            "",
            "需要写入：",
            "- titles.md",
            "- metadata.json.titles / metadata.titles",
            "",
            "结构要求：",
            "- pain_point: 5 个贴合文章主题的标题",
            "- cognitive_gap: 5 个贴合文章主题的标题",
            "- recommended.primary / recommended.secondary / recommended.reason",
            "",
            f"文章标题：{title}",
            f"模块线索：{modules}",
            "",
            "文章摘要：",
            compact_article(article),
        ]
    )


def validate_titles_payload(titles: Any) -> dict[str, Any]:
    if not isinstance(titles, dict):
        raise TitleGenerationError("codex_title_required: metadata.titles is missing.")
    pain_point = titles.get("pain_point")
    cognitive_gap = titles.get("cognitive_gap")
    recommended = titles.get("recommended")
    if not isinstance(pain_point, list) or len([item for item in pain_point if str(item).strip()]) != 5:
        raise TitleGenerationError("codex_title_required: metadata.titles.pain_point must contain exactly 5 titles.")
    if not isinstance(cognitive_gap, list) or len([item for item in cognitive_gap if str(item).strip()]) != 5:
        raise TitleGenerationError("codex_title_required: metadata.titles.cognitive_gap must contain exactly 5 titles.")
    if not isinstance(recommended, dict):
        raise TitleGenerationError("codex_title_required: metadata.titles.recommended is missing.")
    for key in ["primary", "secondary", "reason"]:
        if not str(recommended.get(key) or "").strip():
            raise TitleGenerationError(f"codex_title_required: metadata.titles.recommended.{key} is missing.")
    return titles


def validate_titles_markdown(path: Path) -> None:
    if not path.is_file():
        raise TitleGenerationError(f"codex_title_required: titles.md is missing: {path}")
    text = path.read_text(encoding="utf-8")
    required = ["# 标题候选", "## 击中痛点型", "## 认知差型", "## 推荐首选"]
    missing = [item for item in required if item not in text]
    if missing:
        raise TitleGenerationError(f"codex_title_required: titles.md is missing sections: {', '.join(missing)}")


def validate_titles_for_output(output_dir: Path) -> dict[str, Any]:
    article_path = output_dir / "article.md"
    metadata_path = output_dir / "metadata.json"
    titles_path = output_dir / "titles.md"
    if not article_path.exists():
        raise TitleGenerationError(f"article.md not found: {article_path}", status="failed")
    article = article_path.read_text(encoding="utf-8")
    metadata = load_json(metadata_path)
    try:
        validate_titles_markdown(titles_path)
        titles = validate_titles_payload(metadata.get("titles"))
    except TitleGenerationError as exc:
        if exc.status == "codex_title_required":
            task = build_codex_title_task(output_dir, article, metadata)
            raise TitleGenerationError(f"{exc}\n\n{task}", status="codex_title_required") from exc
        raise
    return {
        "outputDir": str(output_dir),
        "status": "ready",
        "titlesPath": str(titles_path),
        "painPointCount": len(titles["pain_point"]),
        "cognitiveGapCount": len(titles["cognitive_gap"]),
        "primary": str(titles["recommended"]["primary"]),
        "secondary": str(titles["recommended"]["secondary"]),
        "warnings": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate ContentFactory title candidates; report codex_title_required when missing."
    )
    parser.add_argument("output_dirs", nargs="+")
    parser.add_argument("--env-file", default="", help="Accepted for compatibility; not read by this script.")
    parser.add_argument("--model", default="", help="Deprecated; external LLM title generation is disabled.")
    parser.add_argument("--no-ai", action="store_true", help="Deprecated; fallback title generation is disabled.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in args.output_dirs:
        output_dir = Path(item).expanduser().resolve()
        try:
            results.append(validate_titles_for_output(output_dir))
        except TitleGenerationError as exc:
            failures.append({"outputDir": str(output_dir), "status": exc.status, "error": str(exc)})
        except Exception as exc:
            failures.append({"outputDir": str(output_dir), "status": "failed", "error": str(exc)})
    print(json.dumps({"results": results, "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
