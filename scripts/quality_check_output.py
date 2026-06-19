#!/usr/bin/env python3
"""Quality check ContentFactory output directories."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORBIDDEN_META_NARRATION = ["素材里", "原文说", "原文提到", "根据资料", "待重写素材", "本文素材"]
CORPUS_FRAGMENTS = [
    "只要你跑起来了",
    "跑起来，真的很棒",
    "慢也是一种修行",
    "自己就是自己最好的朋友",
    "跑步很好，但要适量",
]
WEB_STORY_REQUIRED_METADATA = ["sourceType", "webSourceId", "url", "sourceName", "originalTitle"]
WEB_STORY_IMPERSONATION_PATTERNS = [
    r"我叫[^，。\n]*(吴浩然|冯唐|埃克瓦尔)",
    r"我是[^，。\n]*(吴浩然|冯唐|埃克瓦尔)",
    r"我在衡水湖",
    r"我在昆明",
    r"我跑进了?220",
    r"我辞职回到老家",
    r"我冲向终点",
    r"我的马拉松生涯",
]
WEB_STORY_UNVERIFIED_MARKERS = [
    "据说",
    "网传",
    "有人爆料",
    "我猜",
    "想必",
    "肯定是因为",
    "一定是因为",
    "所有人都震惊",
]
WEB_STORY_LOWBROW_MARKERS = [
    "猎奇",
    "低俗",
    "笑死人",
    "社死",
    "围观",
    "奇葩的地方",
    "当笑话看",
]
WEB_STORY_MOCKING_MARKERS = [
    "当笑柄",
    "笑柄",
    "拿来取笑",
    "拿来嘲笑",
    "出洋相",
    "丢人现眼",
    "太滑稽",
    "活该",
]
WEB_STORY_ACCIDENT_JOKE_MARKERS = [
    "事故太好笑",
    "把事故当段子",
    "拿事故当段子",
    "当成乐子",
    "看热闹不嫌事大",
    "笑料",
]
WEB_STORY_CLICKBAIT_EXAGGERATION_MARKERS = [
    "千万别跑步",
    "跑步会让大脑失控",
    "大脑会失控",
    "跑马会毁掉身体",
    "所有人都震惊",
    "吓坏所有人",
    "惊心一幕",
    "太可怕了",
]


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def article_char_count(article: str) -> int:
    without_title = re.sub(r"^# .*$", "", article, count=1, flags=re.M)
    return len(re.sub(r"\s+", "", without_title))


def first_title(article: str) -> str:
    for line in article.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def repeated_long_lines(article: str) -> list[str]:
    lines = [line.strip() for line in article.splitlines() if len(line.strip()) >= 28]
    seen: set[str] = set()
    repeats: list[str] = []
    for line in lines:
        if line in seen and line not in repeats:
            repeats.append(line)
        seen.add(line)
    return repeats[:3]


def strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)


def normalize_text_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text)


def extract_copy_fragments(text: str, *, min_len: int = 34) -> list[str]:
    text = strip_frontmatter(text)
    parts = re.split(r"[\n。！？!?；;]+", text)
    fragments: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = normalize_text_for_match(re.sub(r"^[#>\-\d\s、.]+", "", part.strip()))
        if len(cleaned) < min_len or cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)
    return fragments


def collect_exact_copy_hits(article: str, paths: list[Path], *, label: str) -> list[str]:
    normalized_article = normalize_text_for_match(article)
    hits: list[str] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            fragments = extract_copy_fragments(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
        for fragment in fragments:
            if fragment in normalized_article:
                preview = fragment[:42] + ("..." if len(fragment) > 42 else "")
                hits.append(f"{label}：{path.name} / {preview}")
                if len(hits) >= 3:
                    return hits
    return hits


def validate_titles_payload(metadata: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    titles = metadata.get("titles") if metadata else None
    if not isinstance(titles, dict):
        return ["metadata 缺少 titles"]
    pain_point = titles.get("pain_point")
    cognitive_gap = titles.get("cognitive_gap")
    if not isinstance(pain_point, list) or len([item for item in pain_point if str(item).strip()]) != 5:
        problems.append("titles.pain_point 不是 5 个有效标题")
    if not isinstance(cognitive_gap, list) or len([item for item in cognitive_gap if str(item).strip()]) != 5:
        problems.append("titles.cognitive_gap 不是 5 个有效标题")
    recommended = titles.get("recommended")
    if not isinstance(recommended, dict):
        problems.append("titles.recommended 缺失")
    else:
        for key in ["primary", "secondary", "reason"]:
            if is_missing(recommended.get(key)):
                problems.append(f"titles.recommended.{key} 缺失")
    return problems


def source_material_paths(metadata: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for item in metadata.get("sourceMaterials") or []:
        if isinstance(item, dict) and item.get("normalizedPath"):
            paths.append(Path(str(item["normalizedPath"])).expanduser())
    return paths


def run_web_story_checks(article: str, metadata: dict[str, Any]) -> tuple[dict[str, str], list[str], list[str]]:
    checks = {
        "factual_boundary_check": "pass",
        "no_first_person_impersonation": "pass",
        "no_unverified_details": "pass",
        "no_lowbrow_oddity_angle": "pass",
        "no_mocking_subject": "pass",
        "no_accident_as_joke": "pass",
        "no_clickbait_exaggeration": "pass",
    }
    issues: list[str] = []
    warnings: list[str] = []

    missing = [key for key in WEB_STORY_REQUIRED_METADATA if is_missing(metadata.get(key))]
    source_paths = source_material_paths(metadata)
    if missing or not source_paths or any(not path.exists() for path in source_paths):
        checks["factual_boundary_check"] = "fail"
        details = []
        if missing:
            details.append("metadata 缺少 " + "、".join(missing))
        if not source_paths:
            details.append("metadata.sourceMaterials 缺少 normalizedPath")
        else:
            missing_paths = [str(path) for path in source_paths if not path.exists()]
            if missing_paths:
                details.append("normalizedPath 不存在：" + "；".join(missing_paths))
        issues.append("web story factual_boundary_check 未通过：" + "；".join(details))

    impersonation_hits = [
        pattern
        for pattern in WEB_STORY_IMPERSONATION_PATTERNS
        if re.search(pattern, article)
    ]
    if impersonation_hits:
        checks["no_first_person_impersonation"] = "fail"
        issues.append("web story no_first_person_impersonation 未通过：疑似把新闻人物经历写成作者亲历")

    unverified_hits = [item for item in WEB_STORY_UNVERIFIED_MARKERS if item in article]
    if unverified_hits:
        checks["no_unverified_details"] = "fail"
        issues.append("web story no_unverified_details 未通过：出现未核实表达 " + "、".join(unverified_hits[:5]))

    lowbrow_hits = [item for item in WEB_STORY_LOWBROW_MARKERS if item in article]
    if lowbrow_hits:
        checks["no_lowbrow_oddity_angle"] = "fail"
        issues.append("web story no_lowbrow_oddity_angle 未通过：出现低俗化/猎奇化表达 " + "、".join(lowbrow_hits[:5]))

    mocking_hits = [item for item in WEB_STORY_MOCKING_MARKERS if item in article]
    if mocking_hits:
        checks["no_mocking_subject"] = "fail"
        issues.append("web story no_mocking_subject 未通过：疑似嘲笑或贬低当事人 " + "、".join(mocking_hits[:5]))

    accident_joke_hits = [item for item in WEB_STORY_ACCIDENT_JOKE_MARKERS if item in article]
    if accident_joke_hits:
        checks["no_accident_as_joke"] = "fail"
        issues.append("web story no_accident_as_joke 未通过：疑似把风险/异常当成笑料 " + "、".join(accident_joke_hits[:5]))

    clickbait_hits = [item for item in WEB_STORY_CLICKBAIT_EXAGGERATION_MARKERS if item in article]
    if clickbait_hits:
        checks["no_clickbait_exaggeration"] = "fail"
        issues.append("web story no_clickbait_exaggeration 未通过：出现标题党或夸大表达 " + "、".join(clickbait_hits[:5]))

    if all(value == "pass" for value in checks.values()):
        warnings.append("web story 专属检查通过：事实边界、第一人称边界、未核实细节、低俗化/嘲笑/事故笑料/标题党角度均未发现问题")
    return checks, issues, warnings


def check_output(output_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    score = 100

    article_path = output_dir / "article.md"
    brief_path = output_dir / "brief.md"
    prompt_path = output_dir / "cover-prompt.md"
    titles_path = output_dir / "titles.md"
    metadata_path = output_dir / "metadata.json"

    article = ""
    if not article_path.exists():
        issues.append("缺少 article.md")
        score -= 35
    else:
        article = article_path.read_text(encoding="utf-8")
        chars = article_char_count(article)
        if chars < 1100 or chars > 1300:
            issues.append(f"字数不在 1100-1300：{chars}")
            score -= 20
        title = first_title(article)
        if not title or title in {"标题", "文章标题", "未命名文章"}:
            issues.append("标题为空或疑似占位标题")
            score -= 20
        elif len(title) < 6:
            warnings.append(f"标题过短：{title}")
            score -= 5
        modules = re.findall(r"^##\s*0[1-9]、.{1,20}$", article, flags=re.M)
        if len(modules) < 3:
            issues.append("缺少 01 / 02 / 03 模块结构")
            score -= 20
        found_meta = [item for item in FORBIDDEN_META_NARRATION if item in article]
        if found_meta:
            issues.append(f"存在元叙述：{', '.join(found_meta)}")
            score -= 25
        repeats = repeated_long_lines(article)
        if repeats:
            warnings.append("存在重复长句，疑似复制或生成重复：" + " / ".join(repeats))
            score -= 5
        copied_corpus = [item for item in CORPUS_FRAGMENTS if item in article]
        if copied_corpus:
            warnings.append(f"疑似复制 corpus 代表短句：{', '.join(copied_corpus)}")
            score -= 5

    metadata = load_json(metadata_path)
    if not metadata:
        issues.append("metadata.json 缺失或不是合法 JSON")
        score -= 25
    else:
        for key in ["title", "slug", "outputDir", "profileId", "corpusId", "images"]:
            if key not in metadata or is_missing(metadata.get(key)):
                warnings.append(f"metadata 缺少 {key}")
                score -= 5
        metadata_output_dir = str(metadata.get("outputDir") or "")
        if metadata_output_dir and str(Path(metadata_output_dir).expanduser().resolve()) != str(output_dir.resolve()):
            warnings.append("metadata.outputDir 与当前目录不一致")
            score -= 5

    if article and metadata:
        source_paths: list[Path] = []
        source_paths.extend(source_material_paths(metadata))
        source_hits = collect_exact_copy_hits(article, source_paths, label="素材")
        if source_hits:
            warnings.append("疑似复制素材原句：" + " / ".join(source_hits))
            score -= 8

        corpus_paths = []
        if metadata.get("corpusPath"):
            corpus_paths.append(Path(str(metadata["corpusPath"])).expanduser())
        corpus_hits = collect_exact_copy_hits(article, corpus_paths, label="语料")
        if corpus_hits:
            warnings.append("疑似复制 corpus 原句：" + " / ".join(corpus_hits))
            score -= 8

    if not brief_path.exists():
        warnings.append("缺少 brief.md")
        score -= 5
    if not prompt_path.exists():
        warnings.append("缺少 cover-prompt.md")
        score -= 5
    if not titles_path.exists():
        issues.append("缺少 titles.md")
        score -= 10

    cover_meta = metadata.get("images", {}).get("cover", {}) if metadata else {}
    cover_path_value = cover_meta.get("outputPath") or "images/cover.png"
    cover_path = output_dir / str(cover_path_value)
    if not cover_path.exists():
        issues.append("cover.png 不存在")
        score -= 15
    if cover_meta.get("status") != "generated":
        issues.append(f"cover metadata 不是 generated：{cover_meta.get('status', '')}")
        score -= 10

    if metadata:
        title_problems = validate_titles_payload(metadata)
        if title_problems:
            issues.extend(title_problems)
            score -= 10

    web_story_checks: dict[str, str] | None = None
    if article and metadata and metadata.get("sourceType") == "web_story":
        web_story_checks, web_story_issues, web_story_warnings = run_web_story_checks(article, metadata)
        if web_story_issues:
            issues.extend(web_story_issues)
            score -= 12 * len(web_story_issues)
        warnings.extend(web_story_warnings)

    score = max(0, min(100, score))
    if issues:
        status = "rejected" if score < 50 or any("article.md" in item or "metadata.json" in item for item in issues) else "needs_revision"
    else:
        status = "ready_for_edit" if score >= 85 else "needs_revision"

    checked_at = utc_now()
    quality = {
        "status": status,
        "score": score,
        "checkedAt": checked_at,
        "issues": issues,
        "warnings": warnings,
    }
    if web_story_checks is not None:
        quality["webStoryChecks"] = web_story_checks
    metadata["quality"] = quality
    write_json(metadata_path, metadata)
    report_path = write_quality_report(output_dir, quality)
    return {
        "outputDir": str(output_dir),
        "status": status,
        "score": score,
        "issues": issues,
        "warnings": warnings,
        "reportPath": str(report_path),
    }


def write_quality_report(output_dir: Path, quality: dict[str, Any]) -> Path:
    path = output_dir / "quality-report.md"
    issues = quality.get("issues") or []
    warnings = quality.get("warnings") or []
    status = quality["status"]
    if status == "ready_for_edit":
        next_step = "进入网页工作台编辑。"
        recommend = "是"
    elif status == "needs_revision":
        next_step = "先按问题修改或重跑生成，再进入网页工作台。"
        recommend = "否，建议先修正。"
    else:
        next_step = "废弃或重新生成，不建议进入编辑。"
        recommend = "否。"
    lines = [
        "---",
        "type: quality_report",
        f"checked_at: {quality['checkedAt']}",
        f"status: {status}",
        f"score: {quality['score']}",
        "---",
        "",
        "# Quality Report",
        "",
        f"- 总分：{quality['score']}",
        f"- 状态：`{status}`",
        f"- 建议下一步：{next_step}",
        f"- 建议进入网页工作台编辑：{recommend}",
        "",
        "## 主要问题",
        "",
    ]
    lines.extend([f"- {item}" for item in issues] or ["- 无"])
    lines.extend(["", "## 警告", ""])
    lines.extend([f"- {item}" for item in warnings] or ["- 无"])
    web_story_checks = quality.get("webStoryChecks")
    if isinstance(web_story_checks, dict):
        lines.extend(["", "## Web Story 专属检查", ""])
        labels = {
            "factual_boundary_check": "事实边界",
            "no_first_person_impersonation": "不冒充亲历",
            "no_unverified_details": "无未核实细节",
            "no_lowbrow_oddity_angle": "无低俗猎奇角度",
            "no_mocking_subject": "不嘲笑当事人",
            "no_accident_as_joke": "不把事故当笑话",
            "no_clickbait_exaggeration": "无标题党夸大",
        }
        for key, label in labels.items():
            lines.append(f"- {label}（{key}）：`{web_story_checks.get(key, 'missing')}`")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def batch_summary_path(root: Path) -> Path:
    batch_dir = root / "batch-runs"
    batch_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    index = 1
    while True:
        path = batch_dir / f"{date}-quality-batch-{index:02d}.md"
        if not path.exists():
            return path
        index += 1


def write_batch_summary(outputs_root: Path, results: list[dict[str, Any]]) -> Path:
    path = batch_summary_path(outputs_root)
    lines = [
        "---",
        "type: quality_batch_summary",
        f"created_at: {utc_now()}",
        f"item_count: {len(results)}",
        "---",
        "",
        "# Quality Batch Summary",
        "",
        "| output | status | score | issues | report |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for item in results:
        issues = "；".join(item.get("issues") or [])
        lines.append(
            f"| {Path(item['outputDir']).name} | {item['status']} | {item['score']} | {issues} | {item['reportPath']} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def discover_recent(root: Path, limit: int) -> list[Path]:
    outputs = [path for path in root.glob("*/metadata.json") if path.parent.name != "batch-runs"]
    outputs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [path.parent for path in outputs[:limit]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quality check one or more ContentFactory output directories.")
    parser.add_argument("output_dirs", nargs="*")
    parser.add_argument("--scan", default="")
    parser.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dirs = [Path(item).expanduser().resolve() for item in args.output_dirs]
    scan_root: Path | None = None
    if args.scan:
        scan_root = Path(args.scan).expanduser().resolve()
        output_dirs.extend(discover_recent(scan_root, max(args.limit, 1)))
    if not output_dirs:
        print("No output directories provided.", file=sys.stderr)
        return 2

    results = [check_output(output_dir) for output_dir in output_dirs]
    payload: dict[str, Any] = {"results": results}
    if len(output_dirs) > 1 or scan_root is not None:
        root = scan_root or output_dirs[0].parent
        payload["summaryPath"] = str(write_batch_summary(root, results))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
