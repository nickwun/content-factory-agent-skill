#!/usr/bin/env python3
"""Build feishu-publish.md from a ready ContentFactory output directory."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FeishuPublishBuildError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FeishuPublishBuildError(f"metadata.json not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeishuPublishBuildError(f"metadata.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeishuPublishBuildError("metadata.json must contain a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FeishuPublishBuildError(f"{label} not found: {path}")
    if not path.is_file():
        raise FeishuPublishBuildError(f"{label} is not a file: {path}")


def article_char_count(article: str) -> int:
    without_title = re.sub(r"^# .*$", "", article, count=1, flags=re.M)
    return len(re.sub(r"\s+", "", without_title))


def validate_titles(metadata: dict[str, Any]) -> dict[str, Any]:
    titles = metadata.get("titles")
    if not isinstance(titles, dict):
        raise FeishuPublishBuildError("metadata.titles is missing.")
    pain_point = titles.get("pain_point")
    cognitive_gap = titles.get("cognitive_gap")
    recommended = titles.get("recommended")
    if not isinstance(pain_point, list) or len([item for item in pain_point if str(item).strip()]) != 5:
        raise FeishuPublishBuildError("metadata.titles.pain_point must contain exactly 5 titles.")
    if not isinstance(cognitive_gap, list) or len([item for item in cognitive_gap if str(item).strip()]) != 5:
        raise FeishuPublishBuildError("metadata.titles.cognitive_gap must contain exactly 5 titles.")
    if not isinstance(recommended, dict):
        raise FeishuPublishBuildError("metadata.titles.recommended is missing.")
    for key in ["primary", "secondary", "reason"]:
        if not str(recommended.get(key) or "").strip():
            raise FeishuPublishBuildError(f"metadata.titles.recommended.{key} is missing.")
    return titles


def validate_quality(metadata: dict[str, Any]) -> dict[str, Any]:
    quality = metadata.get("quality")
    if not isinstance(quality, dict):
        raise FeishuPublishBuildError("metadata.quality is missing.")
    status = str(quality.get("status") or "")
    if status != "ready_for_edit":
        raise FeishuPublishBuildError(f"quality.status must be ready_for_edit, got: {status}")
    return quality


def read_source_id(metadata: dict[str, Any]) -> str:
    materials = metadata.get("sourceMaterials")
    if isinstance(materials, list) and materials:
        first = materials[0]
        if isinstance(first, dict):
            return str(first.get("sourceId") or "")
        return str(first)
    return ""


def build_markdown(
    *,
    article: str,
    titles: dict[str, Any],
    metadata: dict[str, Any],
    output_dir: Path,
) -> str:
    recommended = titles["recommended"]
    pain_point = titles["pain_point"]
    cognitive_gap = titles["cognitive_gap"]
    quality = metadata["quality"]
    cover_status = str(metadata.get("images", {}).get("cover", {}).get("status") or "")
    source_id = read_source_id(metadata)
    chars = article_char_count(article)

    lines = [
        "![封面图](images/cover.png)",
        "",
        "# 飞书发布稿",
        "",
        "## 推荐标题",
        "",
        f"- 首选标题：{recommended['primary']}",
        f"- 备选标题：{recommended['secondary']}",
        f"- 推荐理由：{recommended['reason']}",
        "",
        "## 标题候选",
        "",
        "### 击中痛点型",
        "",
    ]
    lines.extend(f"{index}. {title}" for index, title in enumerate(pain_point, 1))
    lines.extend(["", "### 认知差型", ""])
    lines.extend(f"{index}. {title}" for index, title in enumerate(cognitive_gap, 1))
    lines.extend(
        [
            "",
            "## 正文",
            "",
            article.strip(),
            "",
            "## 生产信息",
            "",
            f"- sourceId：`{source_id}`",
            f"- profile：`{metadata.get('profileId', '')}`",
            f"- corpus：`{metadata.get('corpusId', '')}`",
            f"- 字数：`{chars}`",
            f"- quality：`{quality.get('status', '')} / {quality.get('score', '')}`",
            f"- cover：`{cover_status}`",
            f"- createdAt：`{metadata.get('createdAt', '')}`",
            f"- outputDir：`{output_dir}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_feishu_publish_markdown(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    article_path = output_dir / "article.md"
    titles_path = output_dir / "titles.md"
    metadata_path = output_dir / "metadata.json"
    quality_path = output_dir / "quality-report.md"
    cover_path = output_dir / "images" / "cover.png"

    require_file(article_path, "article.md")
    require_file(titles_path, "titles.md")
    require_file(metadata_path, "metadata.json")
    require_file(quality_path, "quality-report.md")
    require_file(cover_path, "images/cover.png")

    metadata = load_json(metadata_path)
    titles = validate_titles(metadata)
    validate_quality(metadata)

    article = article_path.read_text(encoding="utf-8")
    publish_markdown = build_markdown(article=article, titles=titles, metadata=metadata, output_dir=output_dir)
    publish_path = output_dir / "feishu-publish.md"
    publish_path.write_text(publish_markdown, encoding="utf-8")

    publish = metadata.get("publish")
    if not isinstance(publish, dict):
        publish = {}
    feishu = publish.get("feishu")
    if not isinstance(feishu, dict):
        feishu = {}
    feishu.update(
        {
            "status": "prepared",
            "markdownFile": "feishu-publish.md",
            "preparedAt": utc_now(),
        }
    )
    publish["feishu"] = feishu
    metadata["publish"] = publish
    metadata["updatedAt"] = utc_now()
    write_json(metadata_path, metadata)

    return {
        "outputDir": str(output_dir),
        "markdownPath": str(publish_path),
        "status": "prepared",
        "title": metadata.get("title", ""),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build feishu-publish.md for one ready ContentFactory output.")
    parser.add_argument("output_dir")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = build_feishu_publish_markdown(Path(args.output_dir))
    except FeishuPublishBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
