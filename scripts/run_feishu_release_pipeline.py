#!/usr/bin/env python3
"""Guarded Feishu release orchestrator for existing ContentFactory outputs.

Version 1 scans and prepares existing outputs. It may run an explicit
--output-dir batch dry-run in guarded mode, but it never executes a real
Feishu publish command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = Path("/Users/hui/Documents/ContentFactoryVault/04-Outputs")
QUALITY_SCRIPT = SCRIPT_DIR / "quality_check_output.py"
BUILD_SCRIPT = SCRIPT_DIR / "build_feishu_publish_markdown.py"
BATCH_SCRIPT = SCRIPT_DIR / "publish_feishu_batch.py"
RISK_KEYWORDS = ("heart-risk", "liver", "drinking", "red-flags")
READY_QUALITY_STATUSES = {"ready_for_edit", "ready", "publish_ready", "ready_to_publish"}


class ReleasePipelineError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("feishu-release-%Y%m%dT%H%M%SZ")


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleasePipelineError(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleasePipelineError(f"Expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_feishu(metadata: dict[str, Any]) -> dict[str, Any]:
    publish = metadata.get("publish") if isinstance(metadata.get("publish"), dict) else {}
    feishu = publish.get("feishu") if isinstance(publish.get("feishu"), dict) else {}
    return feishu


def has_non_ascii_path(path: Path) -> bool:
    return any(ord(char) > 127 for char in path.name)


def risk_reason(output_dir: Path) -> str:
    lower = output_dir.name.lower()
    if any(keyword in lower for keyword in RISK_KEYWORDS):
        return "risky_topic"
    if has_non_ascii_path(output_dir):
        return "risky_topic"
    return ""


def historical_blocks(root: Path) -> dict[str, str]:
    blocked: dict[str, str] = {}
    for state_path in (root / "batch-runs").glob("*/run_state.json"):
        try:
            state = load_json(state_path)
        except Exception:
            continue
        articles = state.get("articles") if isinstance(state.get("articles"), dict) else {}
        for slug, item in articles.items():
            if not isinstance(item, dict):
                continue
            if (
                item.get("requires_remote_check")
                or item.get("current_stage") in {"blocked", "blocked_remote_check"}
                or item.get("skipped_reason") == "requires_remote_check"
            ):
                blocked[str(slug)] = str(state_path)
    return blocked


def scan_outputs(root: Path) -> list[Path]:
    outputs: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        if child.name in {".codex_locks", "batch-runs", "feishu-cli-probe"}:
            continue
        if (child / "metadata.json").is_file():
            outputs.append(child)
    return outputs


def basic_skip_reason(output_dir: Path, metadata: dict[str, Any], blocked: dict[str, str], *, include_risky: bool) -> str:
    feishu = get_feishu(metadata)
    if feishu.get("status") == "published" or feishu.get("documentUrl"):
        return "already_published"
    if feishu.get("requiresRemoteCheck") or feishu.get("requires_remote_check"):
        return "requires_remote_check"
    if output_dir.name in blocked:
        return "historical_blocked"
    if not include_risky:
        risk = risk_reason(output_dir)
        if risk:
            return risk
    if not (output_dir / "article.md").is_file():
        return "codex_article_required"
    if not (output_dir / "images" / "cover.png").is_file():
        return "codex_image_required"
    return ""


def run_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        return subprocess.CompletedProcess(command, 124, stdout, stderr + f"\nTimeout after {timeout}s")


def ensure_quality(output_dir: Path, metadata: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    quality = metadata.get("quality") if isinstance(metadata.get("quality"), dict) else {}
    status = str(quality.get("status") or "")
    if not status:
        result = run_command(["python3", str(QUALITY_SCRIPT), str(output_dir)], timeout=120)
        if result.returncode != 0:
            return False, "quality_check_failed", metadata
        metadata = load_json(output_dir / "metadata.json")
        quality = metadata.get("quality") if isinstance(metadata.get("quality"), dict) else {}
        status = str(quality.get("status") or "")
    if status not in READY_QUALITY_STATUSES:
        return False, f"quality_not_ready:{status or 'missing'}", metadata
    return True, "", metadata


def first_title(article: str) -> str:
    for line in article.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def module_titles(article: str) -> list[str]:
    return [match.strip() for match in re.findall(r"^##\s*0[1-9]、(.+)$", article, flags=re.M)]


def keyword_set(text: str) -> set[str]:
    candidates = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]+", text)
    stop = {"一个", "不是", "可以", "自己", "很多", "因为", "所以", "这个", "时候", "文章", "标题"}
    return {item.lower() for item in candidates if item not in stop}


def titles_structurally_ready(metadata: dict[str, Any], output_dir: Path) -> bool:
    if not (output_dir / "titles.md").is_file():
        return False
    titles = metadata.get("titles")
    if not isinstance(titles, dict):
        return False
    pain_point = titles.get("pain_point")
    cognitive_gap = titles.get("cognitive_gap")
    recommended = titles.get("recommended")
    return (
        isinstance(pain_point, list)
        and len([item for item in pain_point if str(item).strip()]) == 5
        and isinstance(cognitive_gap, list)
        and len([item for item in cognitive_gap if str(item).strip()]) == 5
        and isinstance(recommended, dict)
        and all(str(recommended.get(key) or "").strip() for key in ["primary", "secondary", "reason"])
    )


def title_state_ready(metadata: dict[str, Any], output_dir: Path) -> bool:
    return titles_structurally_ready(metadata, output_dir)


def inspect_diagnostic(output_dir: Path, metadata: dict[str, Any], blocked: dict[str, str], *, include_risky: bool) -> dict[str, Any]:
    feishu = get_feishu(metadata)
    quality = metadata.get("quality") if isinstance(metadata.get("quality"), dict) else {}
    quality_status = str(quality.get("status") or "")
    article_exists = (output_dir / "article.md").is_file()
    cover_exists = (output_dir / "images" / "cover.png").is_file()
    titles_md_exists = (output_dir / "titles.md").is_file()
    metadata_titles_exists = isinstance(metadata.get("titles"), dict)
    titles_ready = title_state_ready(metadata, output_dir)
    feishu_publish_exists = (output_dir / "feishu-publish.md").is_file()
    document_url = str(feishu.get("documentUrl") or "")
    publish_status = str(feishu.get("status") or "")
    requires_remote_check = bool(feishu.get("requiresRemoteCheck") or feishu.get("requires_remote_check"))
    historical_block_path = blocked.get(output_dir.name, "")
    risk = risk_reason(output_dir) if not include_risky else ""
    gaps: list[str] = []
    missing: list[str] = []
    suggested_actions: list[str] = []

    if not article_exists:
        gaps.append("codex_article_required")
        missing.append("article.md")
        suggested_actions.append("Codex should write article.md.")
    if not cover_exists:
        gaps.append("codex_image_required")
        missing.append("images/cover.png")
        suggested_actions.append("Codex image generation should create images/cover.png.")
    if not titles_ready:
        gaps.append("codex_title_required")
        if not titles_md_exists:
            missing.append("titles.md")
        if not metadata_titles_exists:
            missing.append("metadata.titles")
        if "titles.md" not in missing and "metadata.titles" not in missing:
            missing = ["valid titles.md", "valid metadata.titles"]
        suggested_actions.append("Codex should write titles.md and metadata.titles.")
    if not quality_status:
        gaps.append("quality_check_required")
        missing.append("metadata.quality.status")
        suggested_actions.append("Run local quality_check_output.py after titles are present.")
    elif quality_status not in READY_QUALITY_STATUSES:
        gaps.append("quality_revision_required")
        suggested_actions.append("Revise the article, then rerun local quality_check_output.py.")
    can_need_feishu_publish = (
        article_exists
        and cover_exists
        and not (publish_status == "published" or document_url)
        and not requires_remote_check
        and not historical_block_path
        and not risk
        and not (quality_status and quality_status not in READY_QUALITY_STATUSES)
    )
    if not feishu_publish_exists and can_need_feishu_publish:
        gaps.append("feishu_publish_required")
        missing.append("feishu-publish.md")
        suggested_actions.append("Build feishu-publish.md after quality is ready.")

    if publish_status == "published" or document_url:
        primary_status = "already_published"
    elif requires_remote_check:
        primary_status = "requires_remote_check"
    elif historical_block_path:
        primary_status = "historically_blocked"
    elif risk:
        primary_status = "risk_excluded"
    elif gaps:
        priority = [
            "codex_article_required",
            "codex_image_required",
            "codex_title_required",
            "quality_revision_required",
            "quality_check_required",
            "feishu_publish_required",
        ]
        primary_status = next((item for item in priority if item in gaps), gaps[0])
    else:
        primary_status = "ready_for_guarded_dry_run"

    return {
        "slug": output_dir.name,
        "path": str(output_dir),
        "outputDir": str(output_dir),
        "primaryStatus": primary_status,
        "status": primary_status,
        "gaps": gaps,
        "missing": missing,
        "suggestedActions": suggested_actions,
        "suggestedAction": suggested_actions[0] if suggested_actions else "",
        "articleExists": article_exists,
        "coverExists": cover_exists,
        "titlesMdExists": titles_md_exists,
        "metadataTitlesExists": metadata_titles_exists,
        "qualityStatus": quality_status,
        "feishuPublishExists": feishu_publish_exists,
        "publishFeishuStatus": publish_status,
        "documentUrl": document_url,
        "requiresRemoteCheck": requires_remote_check,
        "historicalBlockPath": historical_block_path,
        "riskReason": risk,
    }


def title_consistency_ok(article: str, metadata: dict[str, Any]) -> bool:
    titles = metadata.get("titles") if isinstance(metadata.get("titles"), dict) else {}
    recommended = titles.get("recommended") if isinstance(titles.get("recommended"), dict) else {}
    title_text = " ".join(
        [
            str(recommended.get("primary") or ""),
            str(recommended.get("secondary") or ""),
            *[str(item) for item in titles.get("pain_point", [])[:2] if isinstance(titles.get("pain_point"), list)],
            *[str(item) for item in titles.get("cognitive_gap", [])[:2] if isinstance(titles.get("cognitive_gap"), list)],
        ]
    )
    article_keywords = keyword_set(first_title(article) + "\n" + "\n".join(module_titles(article)) + "\n" + article[:800])
    title_keywords = keyword_set(title_text)
    if not article_keywords or not title_keywords:
        return False
    overlap = article_keywords & title_keywords
    return bool(overlap) and not (
        {"5公里", "10公里", "配速", "公里", "距离"} & title_keywords and not {"5公里", "10公里", "配速", "公里", "距离"} & article_keywords
    )


def ensure_titles(output_dir: Path, metadata: dict[str, Any], *, allow_title_fallback: bool) -> tuple[bool, str, dict[str, Any]]:
    article = (output_dir / "article.md").read_text(encoding="utf-8")
    if titles_structurally_ready(metadata, output_dir):
        return (True, "", metadata) if title_consistency_ok(article, metadata) else (False, "needs_manual_title_review", metadata)
    if allow_title_fallback:
        return False, "needs_manual_title_review", metadata
    return False, "codex_title_required", metadata


def build_feishu_markdown(output_dir: Path) -> tuple[bool, str]:
    if (output_dir / "feishu-publish.md").is_file():
        return True, "exists"
    result = run_command(["python3", str(BUILD_SCRIPT), str(output_dir)], timeout=60)
    if result.returncode != 0:
        return False, "build_feishu_publish_failed"
    return True, "built"


def hash_file(path: Path) -> dict[str, Any]:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(path), "size": path.stat().st_size, "sha256": h.hexdigest()}


def write_backup_manifest(run_dir: Path, outputs: list[Path]) -> Path:
    files: list[dict[str, Any]] = []
    names = ["metadata.json", "article.md", "titles.md", "quality-report.md", "feishu-publish.md"]
    for output in outputs:
        for name in names:
            path = output / name
            if path.is_file():
                files.append(hash_file(path))
        cover = output / "images" / "cover.png"
        if cover.is_file():
            files.append(hash_file(cover))
    manifest = {"createdAt": utc_now(), "files": files}
    path = run_dir / "backup_manifest.json"
    write_json(path, manifest)
    return path


def write_summary(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = run_dir / "summary.md"
    lines = [
        "---",
        "type: feishu_release_pipeline_summary",
        f"created_at: {utc_now()}",
        f"run_id: {payload['runId']}",
        "---",
        "",
        "# Feishu Release Pipeline Summary",
        "",
        f"- mode: `{payload['mode']}`",
        f"- selectedCount: `{payload['selectedCount']}`",
        f"- preparedCount: `{len(payload['prepared'])}`",
        "",
        "## Prepared",
        "",
    ]
    for item in payload["prepared"]:
        lines.append(f"- `{Path(item['outputDir']).name}`: {item.get('status', '')}")
    lines.extend(["", "## Skipped", ""])
    for item in payload["skipped"]:
        lines.append(f"- `{item['slug']}`: `{item['reason']}`")
    lines.extend(["", "## Risks", ""])
    for item in payload["risks"]:
        lines.append(f"- `{item['slug']}`: `{item['reason']}`")
    lines.extend(["", "## Codex Required Tasks", ""])
    for item in payload.get("codexRequiredTasks", []):
        lines.append(f"- `{item['slug']}`: {', '.join(item['tasks'])}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_codex_required_tasks(run_dir: Path, tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return ""
    path = run_dir / "codex_required_tasks.md"
    lines = [
        "# Codex Required Tasks",
        "",
        "These items require Codex-authored content before Feishu publish preparation can continue.",
        "",
    ]
    for item in tasks:
        lines.extend(
            [
                f"## {item['slug']}",
                "",
                f"- outputDir: `{item['outputDir']}`",
                f"- reason: `{item['reason']}`",
            ]
        )
        for task in item["tasks"]:
            lines.append(f"- task: {task}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def codex_task_from_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    missing = item.get("missing") if isinstance(item.get("missing"), list) else []
    codex_required = {"article.md", "images/cover.png", "titles.md", "metadata.titles", "valid titles.md", "valid metadata.titles"}
    required = [name for name in missing if name in codex_required]
    return {
        "slug": item["slug"],
        "path": item["path"],
        "outputDir": item["outputDir"],
        "required": required,
        "status": item["primaryStatus"],
        "primaryStatus": item["primaryStatus"],
        "gaps": item.get("gaps", []),
        "suggestedAction": item.get("suggestedAction") or "Codex should complete the missing content before prepare.",
        "suggestedActions": item.get("suggestedActions", []),
        "tasks": required,
    }


def batch_dry_run(root: Path, outputs: list[Path], run_id: str, allow_permission_skip: bool) -> dict[str, Any]:
    if not outputs:
        return {"selectedCount": 0, "results": [], "statePath": "", "summaryPath": ""}
    command = [
        "python3",
        str(BATCH_SCRIPT),
        "--root",
        str(root),
        "--limit",
        str(len(outputs)),
        "--dry-run",
        "--run-id",
        f"{run_id}-batch-dry-run",
    ]
    for output in outputs:
        command.extend(["--output-dir", str(output)])
    if allow_permission_skip:
        command.append("--allow-permission-skip")
    result = run_command(command, timeout=180)
    if result.returncode != 0:
        raise ReleasePipelineError(result.stderr.strip() or result.stdout.strip() or "batch dry-run failed")
    return json.loads(result.stdout)


def guarded_command(root: Path, outputs: list[Path], run_id: str, allow_permission_skip: bool) -> str:
    parts = [
        "python3",
        str(BATCH_SCRIPT),
        "--root",
        str(root),
    ]
    for output in outputs:
        parts.extend(["--output-dir", str(output)])
    parts.extend(["--limit", str(len(outputs)), "--run-id", f"{run_id}-execute"])
    if allow_permission_skip:
        parts.append("--allow-permission-skip")
    return " ".join(shlex.quote(part) for part in parts)


def run_release(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    run_id = args.run_id or default_run_id()
    run_dir = root / "batch-runs" / run_id
    blocked = historical_blocks(root)
    outputs = scan_outputs(root)
    skipped: list[dict[str, str]] = []
    risks: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    prepared: list[dict[str, str]] = []
    codex_tasks: list[dict[str, Any]] = []
    risk_excluded: list[dict[str, Any]] = []
    quality_required: list[dict[str, Any]] = []
    revision_required: list[dict[str, Any]] = []
    feishu_publish_required: list[dict[str, Any]] = []
    ready_for_prepare: list[dict[str, Any]] = []
    ready_for_guarded_dry_run: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    already_published: list[dict[str, Any]] = []
    all_unpublished_diagnostics: list[dict[str, Any]] = []
    published_count = 0

    for output in outputs:
        metadata = load_json(output / "metadata.json")
        if args.mode == "inspect":
            diagnostic = inspect_diagnostic(output, metadata, blocked, include_risky=args.include_risky)
            status = diagnostic["primaryStatus"]
            gaps = diagnostic.get("gaps", [])
            if status == "already_published":
                published_count += 1
                already_published.append(diagnostic)
                skipped.append({"slug": output.name, "outputDir": str(output), "reason": status})
                continue
            all_unpublished_diagnostics.append(diagnostic)
            reason = basic_skip_reason(output, metadata, blocked, include_risky=args.include_risky)
            if not reason:
                candidates.append({"slug": output.name, "outputDir": str(output)})
            else:
                skipped.append({"slug": output.name, "outputDir": str(output), "reason": status})
            if {"codex_article_required", "codex_image_required", "codex_title_required"} & set(gaps):
                codex_tasks.append(codex_task_from_diagnostic(diagnostic))
            if "quality_check_required" in gaps:
                quality_required.append(diagnostic)
            if "quality_revision_required" in gaps:
                revision_required.append(diagnostic)
            if "feishu_publish_required" in gaps:
                blocked_by = [gap for gap in gaps if gap != "feishu_publish_required"]
                feishu_item = {**diagnostic, "blockedBy": blocked_by}
                feishu_publish_required.append(feishu_item)
                if not blocked_by:
                    ready_for_prepare.append(feishu_item)
            if status == "ready_for_guarded_dry_run":
                ready_for_guarded_dry_run.append(diagnostic)
            if status == "risk_excluded":
                risk_excluded.append(diagnostic)
                risks.append({"slug": output.name, "outputDir": str(output), "reason": status})
            if status in {"requires_remote_check", "historically_blocked"}:
                blocked_items.append(diagnostic)
            continue

        reason = basic_skip_reason(output, metadata, blocked, include_risky=args.include_risky)
        if reason:
            item = {"slug": output.name, "outputDir": str(output), "reason": reason}
            skipped.append(item)
            if reason == "risky_topic":
                risks.append(item)
            continue
        candidates.append({"slug": output.name, "outputDir": str(output)})
        if args.mode == "inspect":
            continue
        ok, reason, metadata = ensure_quality(output, metadata)
        if not ok:
            skipped.append({"slug": output.name, "outputDir": str(output), "reason": reason})
            continue
        ok, reason, metadata = ensure_titles(output, metadata, allow_title_fallback=args.allow_title_fallback)
        if not ok:
            skipped.append({"slug": output.name, "outputDir": str(output), "reason": reason})
            if reason == "codex_title_required":
                codex_tasks.append(
                    {
                        "slug": output.name,
                        "outputDir": str(output),
                        "reason": reason,
                        "tasks": ["write titles.md", "write metadata.titles"],
                    }
                )
            elif reason == "needs_manual_title_review":
                codex_tasks.append(
                    {
                        "slug": output.name,
                        "outputDir": str(output),
                        "reason": reason,
                        "tasks": ["review or rewrite titles.md", "review or rewrite metadata.titles"],
                    }
                )
            continue
        ok, status = build_feishu_markdown(output)
        if not ok:
            skipped.append({"slug": output.name, "outputDir": str(output), "reason": status})
            continue
        prepared.append({"slug": output.name, "outputDir": str(output), "status": status})
        if len(prepared) >= args.count:
            break

    selected_paths = [Path(item["outputDir"]) for item in prepared]
    batch_result = {"selectedCount": 0, "results": [], "statePath": "", "summaryPath": ""}
    if args.mode == "guarded":
        batch_result = batch_dry_run(root, selected_paths, run_id, args.allow_permission_skip)

    paths = {
        "runState": "",
        "summary": "",
        "backupManifest": "",
        "codexRequiredTasks": "",
    }
    guarded_publish_command = ""
    if args.mode == "guarded" and selected_paths:
        guarded_publish_command = guarded_command(root, selected_paths, run_id, args.allow_permission_skip)

    payload = {
        "root": str(root),
        "runId": run_id,
        "mode": args.mode,
        "totalCount": len(outputs),
        "publishedCount": published_count if args.mode == "inspect" else 0,
        "unpublishedCount": len(all_unpublished_diagnostics) if args.mode == "inspect" else 0,
        "selectedCount": len(selected_paths),
        "candidates": candidates,
        "skipped": skipped,
        "prepared": prepared,
        "risks": risks,
        "codexRequiredTasks": codex_tasks,
        "qualityRequired": quality_required,
        "revisionRequired": revision_required,
        "feishuPublishRequired": feishu_publish_required,
        "readyForPrepare": ready_for_prepare,
        "readyForGuardedDryRun": ready_for_guarded_dry_run,
        "riskExcluded": risk_excluded,
        "blocked": blocked_items,
        "alreadyPublished": already_published,
        "allUnpublishedDiagnostics": all_unpublished_diagnostics,
        "batchDryRun": batch_result,
        "guardedPublishCommand": guarded_publish_command,
        "paths": paths,
    }
    if args.mode == "inspect":
        return payload

    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "run_id": run_id,
        "mode": args.mode,
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "candidates": candidates,
        "skipped": skipped,
        "prepared": prepared,
        "risks": risks,
        "codexRequiredTasks": codex_tasks,
        "batchDryRun": batch_result,
        "guardedPublishCommand": guarded_publish_command,
    }
    state_path = run_dir / "run_state.json"
    write_json(state_path, state)
    payload["paths"]["runState"] = str(state_path)
    payload["paths"]["codexRequiredTasks"] = write_codex_required_tasks(run_dir, codex_tasks)
    summary_path = write_summary(run_dir, payload)
    backup_path = write_backup_manifest(run_dir, selected_paths)
    payload["paths"]["summary"] = str(summary_path)
    payload["paths"]["backupManifest"] = str(backup_path)
    write_json(
        state_path,
        {
            **state,
            "summaryPath": str(summary_path),
            "backupManifestPath": str(backup_path),
            "codexRequiredTasksPath": payload["paths"]["codexRequiredTasks"],
        },
    )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a guarded Feishu release from existing ContentFactory outputs.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--mode", choices=["inspect", "prepare", "guarded"], default="inspect")
    parser.add_argument("--allow-permission-skip", action="store_true")
    parser.add_argument(
        "--allow-title-fallback",
        action="store_true",
        help="Permit legacy local title fallback detection only as a manual-review stop; never marks an article publish-ready.",
    )
    parser.add_argument("--risk-policy", choices=["conservative"], default="conservative")
    parser.add_argument("--include-risky", action="store_true")
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = run_release(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
