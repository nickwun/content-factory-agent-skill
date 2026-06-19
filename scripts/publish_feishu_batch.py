#!/usr/bin/env python3
"""Publish a small batch of ready ContentFactory outputs to Feishu via feishu-cli."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = Path("/Users/hui/Documents/ContentFactoryVault/04-Outputs")
DEFAULT_BUILDER = SCRIPT_DIR / "build_feishu_publish_markdown.py"
DEFAULT_PUBLISHER = SCRIPT_DIR / "publish_to_feishu_cli.py"
DEFAULT_CLI = Path("/Users/hui/.local/bin/feishu-cli")
DEFAULT_SETTINGS_DB = Path("/Users/hui/Documents/distributing-web/data/content-agent.sqlite")
DEFAULT_LOCK_PATH = Path("/Users/hui/Documents/distributing-web/.codex_locks/feishu_publish.lock")
SKIP_REVIEW_STATUSES = {"ready_for_wechat", "copied_to_wechat", "published_to_wechat"}
LOCK_STALE_SECONDS = 30 * 60
SINGLE_PUBLISH_TIMEOUT_SECONDS = 180
BUILD_MARKDOWN_TIMEOUT_SECONDS = 60


class FeishuBatchError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def run_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeishuBatchError(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeishuBatchError(f"Expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def acquire_publish_lock(lock_path: Path, task_name: str) -> dict[str, Any]:
    lock_path = lock_path.expanduser().resolve()
    if lock_path.exists():
        try:
            payload = load_json(lock_path)
        except Exception:
            payload = {}
        started_at = str(payload.get("started_at") or "")
        started = parse_utc(started_at)
        age = (datetime.now(timezone.utc) - started).total_seconds() if started else None
        if age is not None and age <= LOCK_STALE_SECONDS:
            raise FeishuBatchError(
                f"active Feishu publish lock exists: {lock_path} "
                f"(task={payload.get('task_name', '')}, current_article={payload.get('current_article', '')}, "
                f"current_step={payload.get('current_step', '')})"
            )
        raise FeishuBatchError(
            f"stale Feishu publish lock exists: {lock_path}. "
            "It is older than 30 minutes; inspect it manually before publishing again."
        )
    payload = {
        "pid": os.getpid(),
        "started_at": utc_now(),
        "task_name": task_name,
        "current_article": "",
        "current_step": "starting",
        "last_error": "",
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(lock_path, payload)
    return payload


def update_publish_lock(
    lock_path: Path,
    *,
    current_article: str = "",
    current_step: str = "",
    last_error: str = "",
) -> None:
    if not lock_path.exists():
        return
    try:
        payload = load_json(lock_path)
    except Exception:
        payload = {}
    if current_article:
        payload["current_article"] = current_article
    if current_step:
        payload["current_step"] = current_step
    if last_error:
        payload["last_error"] = last_error
        payload["failed_at"] = utc_now()
    payload["updated_at"] = utc_now()
    write_json(lock_path, payload)


def release_publish_lock(lock_path: Path) -> None:
    if lock_path.exists():
        lock_path.unlink()


def run_state_path(root: Path, run_id: str) -> Path:
    return root / "batch-runs" / run_id / "run_state.json"


def load_run_state(path: Path, run_id: str) -> dict[str, Any]:
    if path.exists():
        return load_json(path)
    return {
        "run_id": run_id,
        "started_at": utc_now(),
        "finished_at": "",
        "articles": {},
    }


def default_article_state(article_slug: str) -> dict[str, Any]:
    return {
        "article_slug": article_slug,
        "current_stage": "pending",
        "cover_uploaded": False,
        "doc_token": "",
        "doc_imported": False,
        "blocks_checked": False,
        "permission_added": False,
        "published": False,
        "requires_remote_check": False,
        "skipped_reason": "",
        "last_error": "",
        "started_at": "",
        "finished_at": "",
    }


def update_article_state(
    state: dict[str, Any],
    path: Path,
    article_slug: str,
    **updates: Any,
) -> dict[str, Any]:
    articles = state.setdefault("articles", {})
    article = articles.setdefault(article_slug, default_article_state(article_slug))
    if not article.get("started_at"):
        article["started_at"] = utc_now()
    article.update(updates)
    if updates.get("current_stage") in {"published", "failed", "skipped", "dry_run", "blocked_remote_check"}:
        article["finished_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state)
    return article


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


def is_ready_unpublished(output_dir: Path) -> bool:
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        return False
    required_files = [
        output_dir / "article.md",
        output_dir / "titles.md",
        output_dir / "images" / "cover.png",
    ]
    if any(not path.is_file() for path in required_files):
        return False
    metadata = load_json(metadata_path)
    quality = metadata.get("quality")
    if not isinstance(quality, dict) or quality.get("status") != "ready_for_edit":
        return False
    feishu = get_feishu(metadata)
    if feishu.get("status") == "published" and feishu.get("documentUrl"):
        return False
    review = feishu.get("review") if isinstance(feishu.get("review"), dict) else {}
    if review.get("status") in SKIP_REVIEW_STATUSES:
        return False
    return True


def select_outputs(root: Path, limit: int) -> list[Path]:
    outputs: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        if child.name in {"batch-runs", "feishu-cli-probe"}:
            continue
        if is_ready_unpublished(child):
            outputs.append(child)
        if len(outputs) >= limit:
            break
    return outputs


def is_path_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_publish_scope(args: argparse.Namespace) -> tuple[Path, Path | None]:
    output_arg = getattr(args, "output_dir", None)
    root_arg = getattr(args, "root", None)
    if output_arg:
        output_dir = output_arg.expanduser().resolve()
        if not output_dir.is_dir():
            raise FeishuBatchError(f"--output-dir must be an existing directory: {output_dir}")
        if not (output_dir / "metadata.json").is_file():
            raise FeishuBatchError(f"--output-dir must contain metadata.json: {output_dir}")
        root = root_arg.expanduser().resolve() if root_arg else output_dir.parent.resolve()
        if not is_path_inside(output_dir, root):
            raise FeishuBatchError(f"--output-dir must be inside --root: output={output_dir}, root={root}")
        return root, output_dir
    root = root_arg.expanduser().resolve() if root_arg else DEFAULT_ROOT.expanduser().resolve()
    return root, None


def output_needs_publish_attempt(output_dir: Path) -> bool:
    metadata = load_json(output_dir / "metadata.json")
    feishu = get_feishu(metadata)
    if feishu.get("status") == "published" and feishu.get("documentUrl"):
        return False
    if feishu.get("requiresRemoteCheck"):
        return False
    return True


def run_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
    logs_dir: Path | None = None,
    label: str = "command",
) -> subprocess.CompletedProcess[str]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timeout_message = f"{label} timed out after {timeout_seconds}s; remote state must be checked before retrying."
        stderr = (stderr.rstrip() + "\n" + timeout_message).strip()
        result = subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-") or "command"
        stamp = run_id_now()
        (logs_dir / f"{stamp}-{safe_label}.stdout.log").write_text(result.stdout or "", encoding="utf-8")
        (logs_dir / f"{stamp}-{safe_label}.stderr.log").write_text(result.stderr or "", encoding="utf-8")
        (logs_dir / f"{stamp}-{safe_label}.meta.json").write_text(
            json.dumps(
                {
                    "command": command,
                    "returncode": result.returncode,
                    "elapsedSeconds": round(time.monotonic() - started, 3),
                    "timeoutSeconds": timeout_seconds,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return result


def ensure_feishu_markdown(output_dir: Path, builder: Path, logs_dir: Path) -> dict[str, Any]:
    publish_markdown = output_dir / "feishu-publish.md"
    if publish_markdown.is_file():
        metadata = load_json(output_dir / "metadata.json")
        feishu = get_feishu(metadata)
        if feishu.get("status") != "prepared":
            feishu.update({"status": "prepared", "markdownFile": "feishu-publish.md", "preparedAt": utc_now()})
            write_json(output_dir / "metadata.json", metadata)
        return {"status": "exists", "stdout": "", "stderr": ""}

    result = run_command(
        ["python3", str(builder), str(output_dir)],
        timeout_seconds=BUILD_MARKDOWN_TIMEOUT_SECONDS,
        logs_dir=logs_dir,
        label=f"{output_dir.name}-build-feishu-markdown",
    )
    if result.returncode != 0:
        raise FeishuBatchError(result.stderr.strip() or result.stdout.strip() or "failed to build feishu-publish.md")
    return {"status": "built", "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def owner_args(args: argparse.Namespace) -> list[str]:
    values = [
        ("--owner-email", args.owner_email or os.environ.get("FEISHU_OWNER_EMAIL", "")),
        ("--owner-user-id", args.owner_user_id or os.environ.get("FEISHU_OWNER_USER_ID", "")),
        ("--owner-open-id", args.owner_open_id or os.environ.get("FEISHU_OWNER_OPEN_ID", "")),
        ("--owner-union-id", args.owner_union_id or os.environ.get("FEISHU_OWNER_UNION_ID", "")),
    ]
    flattened: list[str] = []
    for flag, value in values:
        value = str(value or "").strip()
        if value:
            flattened.extend([flag, value])
    return flattened


def has_owner_config(args: argparse.Namespace, env: dict[str, str]) -> bool:
    values = [
        args.owner_user_id,
        args.owner_email,
        args.owner_open_id,
        args.owner_union_id,
        env.get("FEISHU_OWNER_USER_ID", ""),
        env.get("FEISHU_OWNER_EMAIL", ""),
        env.get("FEISHU_OWNER_OPEN_ID", ""),
        env.get("FEISHU_OWNER_UNION_ID", ""),
    ]
    return any(str(value or "").strip() for value in values)


def owner_required_error() -> FeishuBatchError:
    return FeishuBatchError(
        "Batch Feishu publishing requires owner permission config. Set one of "
        "FEISHU_OWNER_USER_ID / FEISHU_OWNER_OPEN_ID / FEISHU_OWNER_UNION_ID / FEISHU_OWNER_EMAIL "
        "or pass the matching --owner-* flag. Use --allow-permission-skip only for an intentional "
        "permission-skip probe."
    )


def mark_publish_failed(output_dir: Path, error: str, *, requires_remote_check: bool = False) -> None:
    metadata_path = output_dir / "metadata.json"
    try:
        metadata = load_json(metadata_path)
    except Exception:
        return
    feishu = get_feishu(metadata)
    if feishu.get("status") != "published":
        feishu.update(
            {
                "status": "failed",
                "error": error,
                "backend": "feishu-cli",
                "failedAt": utc_now(),
                "requiresRemoteCheck": requires_remote_check or bool(feishu.get("requiresRemoteCheck")),
            }
        )
        feishu.pop("documentId", None)
        feishu.pop("documentUrl", None)
        metadata["updatedAt"] = utc_now()
        write_json(metadata_path, metadata)


def parse_image_upload_result(report_path: Path) -> str:
    if not report_path.is_file():
        return ""
    text = report_path.read_text(encoding="utf-8")
    match = re.search(r"image upload result：`([^`]+)`", text)
    return match.group(1) if match else ""


def write_pending_review(output_dir: Path) -> None:
    metadata_path = output_dir / "metadata.json"
    metadata = load_json(metadata_path)
    feishu = get_feishu(metadata)
    if feishu.get("status") != "published":
        return
    checked_at = utc_now()
    review = {
        "status": "pending_review",
        "checkedAt": checked_at,
        "result": "待人工检查",
        "notes": "Feishu CLI 批量发布成功，待人工检查飞书复制到公众号后台效果。",
    }
    feishu["review"] = review
    metadata["updatedAt"] = checked_at
    write_json(metadata_path, metadata)

    check_path = output_dir / "feishu-check.md"
    block = "\n".join(
        [
            "# 飞书文档人工检查记录",
            "",
            "## 1. 文档信息",
            "",
            f"- documentUrl: {feishu.get('documentUrl', '')}",
            f"- documentId: {feishu.get('documentId', '')}",
            f"- backend: {feishu.get('backend', '')}",
            f"- checkedAt: {checked_at}",
            "",
            "## 2. 飞书结构检查",
            "",
            "- 封面图是否显示：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "- 推荐标题是否清楚：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "- 标题候选是否完整：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "- 正文是否完整：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "## 3. 复制到公众号检查",
            "",
            "- 从飞书复制到公众号后台是否成功：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "- 手机预览是否通过：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "## 4. 问题记录",
            "",
            "- 问题 1：",
            "",
            "## 5. 最终结论",
            "",
            "- 是否可作为后续飞书发布模板：",
            "  - [ ] 可以",
            "  - [ ] 暂不可以",
            "",
            "<!-- feishu-review-status:start -->",
            "",
            "## 6. 发布后状态",
            "",
            "- review.status: `pending_review`",
            f"- checkedAt: `{checked_at}`",
            "- result: 待人工检查",
            "- notes: Feishu CLI 批量发布成功，待人工检查飞书复制到公众号后台效果。",
            "",
            "<!-- feishu-review-status:end -->",
            "",
        ]
    )
    check_path.write_text(block, encoding="utf-8")


def refresh_article_state_from_metadata(state: dict[str, Any], state_path: Path, output_dir: Path) -> None:
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        return
    metadata = load_json(metadata_path)
    feishu = get_feishu(metadata)
    permission = feishu.get("permission") if isinstance(feishu.get("permission"), dict) else {}
    image_result = parse_image_upload_result(output_dir / "publish-report.md")
    existing = state.setdefault("articles", {}).get(output_dir.name, {})
    requires_remote_check = bool(existing.get("requires_remote_check") or feishu.get("requiresRemoteCheck"))
    update_article_state(
        state,
        state_path,
        output_dir.name,
        current_stage="metadata_checked",
        cover_uploaded=image_result == "1/1",
        doc_token=str(feishu.get("documentId") or ""),
        doc_imported=bool(feishu.get("documentId")),
        permission_added=permission.get("status") == "granted",
        published=feishu.get("status") == "published" and bool(feishu.get("documentUrl")),
        requires_remote_check=requires_remote_check,
    )


def summarize_output(output_dir: Path, *, returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
    metadata = load_json(output_dir / "metadata.json")
    feishu = get_feishu(metadata)
    status = str(feishu.get("status") or ("published" if returncode == 0 else "failed"))
    permission = feishu.get("permission") if isinstance(feishu.get("permission"), dict) else {}
    report_path = output_dir / "publish-report.md"
    return {
        "outputDir": str(output_dir),
        "status": status,
        "documentId": str(feishu.get("documentId") or ""),
        "documentUrl": str(feishu.get("documentUrl") or ""),
        "backend": str(feishu.get("backend") or ""),
        "permissionStatus": str(permission.get("status") or ""),
        "permissionGrantedTo": str(permission.get("grantedTo") or ""),
        "imageUploadResult": parse_image_upload_result(report_path),
        "reportPath": str(report_path) if report_path.is_file() else "",
        "error": str(feishu.get("error") or stderr.strip() or stdout.strip() or "") if status == "failed" else "",
    }


def publish_one(
    output_dir: Path,
    args: argparse.Namespace,
    env: dict[str, str],
    *,
    state: dict[str, Any],
    state_path: Path,
    logs_dir: Path,
    lock_path: Path,
) -> dict[str, Any]:
    update_publish_lock(lock_path, current_article=output_dir.name, current_step="metadata_check")
    refresh_article_state_from_metadata(state, state_path, output_dir)
    metadata = load_json(output_dir / "metadata.json")
    feishu = get_feishu(metadata)
    article_state = state.get("articles", {}).get(output_dir.name, {})
    if article_state.get("requires_remote_check") or feishu.get("requiresRemoteCheck"):
        error = (
            "remote state check required before retrying side-effecting Feishu publish steps; "
            "inspect Feishu and local metadata/run_state first."
        )
        update_publish_lock(
            lock_path,
            current_article=output_dir.name,
            current_step="blocked_remote_check",
            last_error=error,
        )
        update_article_state(
            state,
            state_path,
            output_dir.name,
            current_stage="blocked_remote_check",
            last_error=error,
            requires_remote_check=True,
            skipped_reason="requires_remote_check",
        )
        return {
            "outputDir": str(output_dir),
            "status": "blocked_remote_check",
            "documentId": str(feishu.get("documentId") or ""),
            "documentUrl": str(feishu.get("documentUrl") or ""),
            "backend": str(feishu.get("backend") or ""),
            "permissionStatus": "",
            "permissionGrantedTo": "",
            "imageUploadResult": parse_image_upload_result(output_dir / "publish-report.md"),
            "reportPath": "",
            "error": error,
            "skippedReason": "requires_remote_check",
            "buildStatus": "not_run",
        }
    if feishu.get("status") == "published" and feishu.get("documentUrl"):
        update_article_state(
            state,
            state_path,
            output_dir.name,
            current_stage="skipped",
            published=True,
            skipped_reason="already_published",
        )
        summary = summarize_output(output_dir, returncode=0, stdout="", stderr="")
        summary["status"] = "skipped"
        summary["skippedReason"] = "already_published"
        return summary
    if args.dry_run:
        update_article_state(state, state_path, output_dir.name, current_stage="dry_run")
        return {
            "outputDir": str(output_dir),
            "status": "dry_run",
            "documentId": str(feishu.get("documentId") or ""),
            "documentUrl": str(feishu.get("documentUrl") or ""),
            "backend": str(feishu.get("backend") or ""),
            "permissionStatus": "",
            "permissionGrantedTo": "",
            "imageUploadResult": parse_image_upload_result(output_dir / "publish-report.md"),
            "reportPath": "",
            "error": "",
            "buildStatus": "not_run",
        }
    try:
        update_publish_lock(lock_path, current_article=output_dir.name, current_step="build_feishu_markdown")
        update_article_state(state, state_path, output_dir.name, current_stage="build_feishu_markdown")
        build_result = ensure_feishu_markdown(output_dir, args.builder, logs_dir)
    except Exception as exc:
        error = str(exc)
        mark_publish_failed(output_dir, error)
        summary = summarize_output(output_dir, returncode=1, stdout="", stderr=error)
        summary["buildStatus"] = "failed"
        update_article_state(state, state_path, output_dir.name, current_stage="failed", last_error=error)
        return summary

    update_publish_lock(lock_path, current_article=output_dir.name, current_step="publish_cli")
    update_article_state(state, state_path, output_dir.name, current_stage="publish_cli")
    command = [
        "python3",
        str(args.publisher),
        str(output_dir),
        "--cli",
        str(args.cli),
        "--settings-db",
        str(args.settings_db),
        *owner_args(args),
    ]
    result = run_command(
        command,
        env=env,
        timeout_seconds=args.single_timeout,
        logs_dir=logs_dir,
        label=f"{output_dir.name}-publish-one",
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "publish failed"
        mark_publish_failed(output_dir, error, requires_remote_check=result.returncode == 124)
        update_article_state(
            state,
            state_path,
            output_dir.name,
            current_stage="failed",
            last_error=error,
            requires_remote_check=result.returncode == 124,
        )
    else:
        write_pending_review(output_dir)
        refresh_article_state_from_metadata(state, state_path, output_dir)
        update_article_state(state, state_path, output_dir.name, current_stage="published", published=True)
    summary = summarize_output(output_dir, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    summary["buildStatus"] = build_result["status"]
    return summary


def next_summary_path(root: Path) -> Path:
    runs_dir = root / "batch-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    date = today()
    for index in range(1, 100):
        path = runs_dir / f"{date}-feishu-publish-batch-{index:02d}.md"
        if not path.exists():
            return path
    raise FeishuBatchError("Too many batch summaries for today.")


def write_batch_summary(root: Path, results: list[dict[str, Any]], selected: list[Path]) -> Path:
    path = next_summary_path(root)
    success_count = sum(1 for item in results if item.get("status") == "published")
    failed_count = sum(1 for item in results if item.get("status") == "failed")
    lines = [
        "---",
        "type: feishu_publish_batch_summary",
        f"created_at: {utc_now()}",
        f"selected_count: {len(selected)}",
        f"success_count: {success_count}",
        f"failed_count: {failed_count}",
        "---",
        "",
        "# Feishu Publish Batch Summary",
        "",
        f"- 本次选择：`{len(selected)}`",
        f"- 成功：`{success_count}`",
        f"- 失败：`{failed_count}`",
        "",
        "| Output | 状态 | 文档 | 权限 | 图片 | 错误 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        output_name = Path(str(item.get("outputDir") or "")).name
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{output_name}`",
                    f"`{item.get('status', '')}`",
                    str(item.get("documentUrl") or ""),
                    f"`{item.get('permissionStatus', '')}`",
                    f"`{item.get('imageUploadResult', '')}`",
                    str(item.get("error") or item.get("skippedReason") or "").replace("\n", " ")[:160],
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def publish_batch(args: argparse.Namespace) -> dict[str, Any]:
    root, output_dir = resolve_publish_scope(args)
    limit = min(max(args.limit, 1), 5)
    run_id = args.run_id or run_id_now()
    env = os.environ.copy()
    if args.owner_user_id:
        env["FEISHU_OWNER_USER_ID"] = args.owner_user_id
    if args.owner_email:
        env["FEISHU_OWNER_EMAIL"] = args.owner_email
    if args.owner_open_id:
        env["FEISHU_OWNER_OPEN_ID"] = args.owner_open_id
    if args.owner_union_id:
        env["FEISHU_OWNER_UNION_ID"] = args.owner_union_id

    selected = [output_dir] if output_dir else select_outputs(root, limit)
    requires_owner = any(output_needs_publish_attempt(path) for path in selected)
    if requires_owner and not args.dry_run and not args.allow_permission_skip and not has_owner_config(args, env):
        raise owner_required_error()

    state_path = run_state_path(root, run_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = load_run_state(state_path, run_id)
    lock_path = args.lock_path.expanduser().resolve()
    acquire_publish_lock(lock_path, f"feishu_publish_batch:{run_id}")
    logs_dir = state_path.parent / "logs"
    try:
        update_publish_lock(lock_path, current_step="selected_outputs")
        state["selectionMode"] = "output_dir" if output_dir else "root_scan"
        state["selected"] = [str(path) for path in selected]
        write_json(state_path, state)
    except Exception as exc:
        update_publish_lock(lock_path, current_step="failed", last_error=str(exc))
        raise

    try:
        results = [
            publish_one(
                output_dir,
                args,
                env,
                state=state,
                state_path=state_path,
                logs_dir=logs_dir,
                lock_path=lock_path,
            )
            for output_dir in selected
        ]
        state["finished_at"] = utc_now()
        write_json(state_path, state)
        summary_path = write_batch_summary(root, results, selected)
        release_publish_lock(lock_path)
        return {
            "root": str(root),
            "runId": run_id,
            "statePath": str(state_path),
            "selectedCount": len(selected),
            "summaryPath": str(summary_path),
            "results": results,
        }
    except Exception as exc:
        update_publish_lock(lock_path, current_step="failed", last_error=str(exc))
        state["last_error"] = str(exc)
        write_json(state_path, state)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish up to 5 ready ContentFactory outputs to Feishu.")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--builder", type=Path, default=DEFAULT_BUILDER)
    parser.add_argument("--publisher", type=Path, default=DEFAULT_PUBLISHER)
    parser.add_argument("--cli", type=Path, default=DEFAULT_CLI)
    parser.add_argument("--settings-db", type=Path, default=DEFAULT_SETTINGS_DB)
    parser.add_argument("--owner-email", default="")
    parser.add_argument("--owner-user-id", default="")
    parser.add_argument("--owner-open-id", default="")
    parser.add_argument("--owner-union-id", default="")
    parser.add_argument("--allow-permission-skip", action="store_true")
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--single-timeout", type=int, default=SINGLE_PUBLISH_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = publish_batch(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
