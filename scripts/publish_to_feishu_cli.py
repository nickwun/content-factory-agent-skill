#!/usr/bin/env python3
"""Publish one prepared ContentFactory output to Feishu with feishu-cli."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CLI = Path("/Users/hui/.local/bin/feishu-cli")
DEFAULT_SETTINGS_DB = Path("/Users/hui/Documents/distributing-web/data/content-agent.sqlite")
IMPORT_TIMEOUT_SECONDS = 120
PERMISSION_TIMEOUT_SECONDS = 30
DOC_BLOCKS_TIMEOUT_SECONDS = 60
COMMAND_REPORT_CHARS = 4000
COMMAND_MAX_OUTPUT_BYTES = 5_000_000


class FeishuPublishError(RuntimeError):
    pass


@dataclass
class LoggedCommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool
    error: str
    stdout_log_path: str
    stderr_log_path: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def truncate_for_report(value: str, limit: int = COMMAND_REPORT_CHARS) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars; full log saved]"


def safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "command"


def run_logged_command(
    command: list[str],
    *,
    label: str,
    timeout_seconds: float,
    logs_dir: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> LoggedCommandResult:
    logs_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{log_timestamp()}-{safe_label(label)}"
    stdout_log_path = logs_dir / f"{prefix}.stdout.log"
    stderr_log_path = logs_dir / f"{prefix}.stderr.log"
    started = time.monotonic()
    timed_out = False
    error = ""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
        returncode = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = coerce_output(exc.stdout)
        stderr = coerce_output(exc.stderr)
        error = f"{label} timed out after {timeout_seconds:g}s"
        if error not in stderr:
            stderr = (stderr.rstrip() + "\n" + error).strip()
    elapsed = time.monotonic() - started
    stdout_log_path.write_text(stdout, encoding="utf-8")
    stderr_log_path.write_text(stderr, encoding="utf-8")
    return LoggedCommandResult(
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_seconds=elapsed,
        timed_out=timed_out,
        error=error,
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
    )


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FeishuPublishError(f"metadata.json not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeishuPublishError(f"metadata.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeishuPublishError("metadata.json must contain a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FeishuPublishError(f"{label} not found: {path}")
    if not path.is_file():
        raise FeishuPublishError(f"{label} is not a file: {path}")


def resolve_feishu_credentials(env: dict[str, str], settings_db: Path) -> dict[str, str]:
    if env.get("FEISHU_APP_ID") and env.get("FEISHU_APP_SECRET"):
        return env
    if settings_db.exists():
        with sqlite3.connect(settings_db) as conn:
            rows = conn.execute(
                "SELECT key, value FROM integration_credentials WHERE key IN ('feishu_app_id', 'feishu_app_secret')"
            ).fetchall()
        values = {str(key): str(value) for key, value in rows}
        if not env.get("FEISHU_APP_ID") and values.get("feishu_app_id"):
            env["FEISHU_APP_ID"] = values["feishu_app_id"]
        if not env.get("FEISHU_APP_SECRET") and values.get("feishu_app_secret"):
            env["FEISHU_APP_SECRET"] = values["feishu_app_secret"]
    if not env.get("FEISHU_APP_ID") or not env.get("FEISHU_APP_SECRET"):
        raise FeishuPublishError(
            "FEISHU_APP_ID / FEISHU_APP_SECRET are not configured. Set env vars or provide a settings DB."
        )
    return env


def get_publish_state(metadata: dict[str, Any]) -> dict[str, Any]:
    publish = metadata.get("publish")
    if not isinstance(publish, dict):
        publish = {}
        metadata["publish"] = publish
    feishu = publish.get("feishu")
    if not isinstance(feishu, dict):
        feishu = {}
        publish["feishu"] = feishu
    return feishu


def recommended_title(metadata: dict[str, Any]) -> str:
    titles = metadata.get("titles")
    if isinstance(titles, dict):
        recommended = titles.get("recommended")
        if isinstance(recommended, dict):
            primary = str(recommended.get("primary") or "").strip()
            if primary:
                return primary
    return str(metadata.get("title") or "内容工厂飞书文档").strip() or "内容工厂飞书文档"


def parse_json_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(stripped[start : end + 1])
        else:
            raise FeishuPublishError(f"feishu-cli stdout did not contain valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeishuPublishError("feishu-cli stdout JSON must be an object.")
    return payload


def parse_document_url(stderr: str, document_id: str) -> str:
    match = re.search(r"https?://[^\s]+/docx/[A-Za-z0-9]+", stderr)
    if match:
        return match.group(0)
    return f"https://feishu.cn/docx/{document_id}" if document_id else ""


def command_for_report(command: list[str]) -> str:
    return " ".join(command)


def resolve_permission_target(args: argparse.Namespace, env: dict[str, str]) -> tuple[str, str, str]:
    candidates = [
        ("user_id", getattr(args, "owner_user_id", "")),
        ("email", getattr(args, "owner_email", "")),
        ("open_id", getattr(args, "owner_open_id", "")),
        ("union_id", getattr(args, "owner_union_id", "")),
        ("user_id", env.get("FEISHU_OWNER_USER_ID", "")),
        ("email", env.get("FEISHU_OWNER_EMAIL", "")),
        ("open_id", env.get("FEISHU_OWNER_OPEN_ID", "")),
        ("union_id", env.get("FEISHU_OWNER_UNION_ID", "")),
    ]
    for member_type, member_id in candidates:
        member_id = str(member_id or "").strip()
        if member_id:
            return member_type, member_id, f"{member_type}:{member_id}"
    return "", "", ""


def grant_edit_permission(
    *,
    cli: Path,
    document_id: str,
    env: dict[str, str],
    args: argparse.Namespace,
    logs_dir: Path,
) -> dict[str, Any]:
    member_type, member_id, granted_to = resolve_permission_target(args, env)
    if not member_id:
        return {
            "status": "skipped",
            "grantedTo": "",
            "perm": "edit",
            "error": "FEISHU_OWNER_USER_ID / FEISHU_OWNER_OPEN_ID / FEISHU_OWNER_UNION_ID / FEISHU_OWNER_EMAIL not configured.",
        }

    command = [
        str(cli),
        "perm",
        "add",
        document_id,
        "--doc-type",
        "docx",
        "--member-type",
        member_type,
        "--member-id",
        member_id,
        "--perm",
        "edit",
    ]
    result = run_logged_command(
        command,
        label="feishu-perm-add",
        timeout_seconds=PERMISSION_TIMEOUT_SECONDS,
        logs_dir=logs_dir,
        env=env,
    )
    if result.returncode == 0:
        return {
            "status": "granted",
            "grantedTo": granted_to,
            "memberType": member_type,
            "perm": "edit",
            "grantedAt": utc_now(),
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "stdoutLogPath": result.stdout_log_path,
            "stderrLogPath": result.stderr_log_path,
            "elapsedSeconds": result.elapsed_seconds,
        }
    timeout_error = (
        "Permission grant timed out. Remote permission state was not verified; "
        "check Feishu permissions and local metadata before retrying."
        if result.timed_out
        else ""
    )
    return {
        "status": "failed",
        "grantedTo": granted_to,
        "memberType": member_type,
        "perm": "edit",
        "failedAt": utc_now(),
        "error": timeout_error
        or result.stderr.strip()
        or result.stdout.strip()
        or f"feishu-cli perm add exited with {result.returncode}",
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "stdoutLogPath": result.stdout_log_path,
        "stderrLogPath": result.stderr_log_path,
        "elapsedSeconds": result.elapsed_seconds,
        "requiresRemoteCheck": result.timed_out,
    }


def write_publish_report(
    output_dir: Path,
    *,
    command: list[str],
    status: str,
    document_id: str = "",
    document_url: str = "",
    image_result: str = "",
    permission: dict[str, Any] | None = None,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
    stdout_log_path: str = "",
    stderr_log_path: str = "",
    blocks_summary: dict[str, Any] | None = None,
) -> Path:
    path = output_dir / "publish-report.md"
    lines = [
        "---",
        "type: feishu_publish_report",
        f"published_at: {utc_now()}",
        f"status: {status}",
        "---",
        "",
        "# Feishu Publish Report",
        "",
        f"- 状态：`{status}`",
        f"- 发布命令：`{command_for_report(command)}`",
        f"- documentId：`{document_id}`",
        f"- documentUrl：{document_url}",
        f"- image upload result：`{image_result}`",
        f"- permission status：`{(permission or {}).get('status', '')}`",
        f"- permission grantedTo：`{(permission or {}).get('grantedTo', '')}`",
    ]
    permission_error = str((permission or {}).get("error") or "")
    if permission_error:
        lines.append(f"- permission error：{permission_error}")
    if error:
        lines.append(f"- error：{error}")
    if stdout_log_path:
        lines.append(f"- stdout log：`{stdout_log_path}`")
    if stderr_log_path:
        lines.append(f"- stderr log：`{stderr_log_path}`")
    if blocks_summary:
        lines.extend(
            [
                f"- blocks checked：`{blocks_summary.get('blocks_count', '')}`",
                f"- image blocks：`{blocks_summary.get('image_blocks_count', '')}`",
                f"- cover found：`{blocks_summary.get('cover_found', '')}`",
                f"- blocks snapshot：`{blocks_summary.get('snapshot_path', '')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## stdout",
            "",
            "```json",
            truncate_for_report(stdout),
            "```",
            "",
            "## stderr",
            "",
            "```text",
            truncate_for_report(stderr),
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def preflight(output_dir: Path, cli: Path, force: bool) -> tuple[dict[str, Any], dict[str, Any], str]:
    require_file(output_dir / "feishu-publish.md", "feishu-publish.md")
    require_file(output_dir / "metadata.json", "metadata.json")
    require_file(output_dir / "images" / "cover.png", "images/cover.png")
    if not cli.exists() or not os.access(cli, os.X_OK):
        raise FeishuPublishError(f"feishu-cli executable not found or not executable: {cli}")

    metadata = load_json(output_dir / "metadata.json")
    feishu = get_publish_state(metadata)
    status = str(feishu.get("status") or "")
    if feishu.get("requiresRemoteCheck"):
        raise FeishuPublishError(
            "publish.feishu.requiresRemoteCheck is set; inspect the remote Feishu document "
            "and local metadata before retrying this side-effecting publish step."
        )
    if status == "published" and feishu.get("documentUrl") and not force:
        raise FeishuPublishError("output is already published; use --force to republish.")
    if status != "prepared" and not (force and status == "published"):
        raise FeishuPublishError(f"publish.feishu.status must be prepared, got: {status}")
    quality = metadata.get("quality")
    quality_status = quality.get("status") if isinstance(quality, dict) else ""
    if quality_status != "ready_for_edit":
        raise FeishuPublishError(f"quality.status must be ready_for_edit, got: {quality_status}")
    return metadata, feishu, recommended_title(metadata)


def publish_to_feishu(
    output_dir: Path,
    *,
    cli: Path,
    settings_db: Path,
    force: bool,
    permission_args: argparse.Namespace,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    cli = cli.expanduser().resolve()
    settings_db = settings_db.expanduser().resolve()
    metadata_path = output_dir / "metadata.json"
    metadata, feishu, title = preflight(output_dir, cli, force)
    env = resolve_feishu_credentials(os.environ.copy(), settings_db)

    command = [
        str(cli),
        "doc",
        "import",
        "feishu-publish.md",
        "--title",
        title,
        "--upload-images",
        "--verbose",
        "-o",
        "json",
    ]
    result = run_logged_command(
        command,
        label="feishu-doc-import",
        timeout_seconds=IMPORT_TIMEOUT_SECONDS,
        logs_dir=output_dir / "logs",
        cwd=output_dir,
        env=env,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""

    if result.returncode != 0:
        error = (
            "Feishu document import timed out after "
            f"{IMPORT_TIMEOUT_SECONDS}s. Remote document state was not verified; "
            "check Feishu and local metadata before retrying."
            if result.timed_out
            else stderr.strip() or stdout.strip() or f"feishu-cli exited with code {result.returncode}"
        )
        feishu.pop("documentId", None)
        feishu.pop("documentUrl", None)
        feishu.update(
            {
                "status": "failed",
                "error": error,
                "backend": "feishu-cli",
                "failedAt": utc_now(),
                "requiresRemoteCheck": result.timed_out,
            }
        )
        write_json(metadata_path, metadata)
        report = write_publish_report(
            output_dir,
            command=command,
            status="failed",
            permission=feishu.get("permission") if isinstance(feishu.get("permission"), dict) else None,
            stdout=stdout,
            stderr=stderr,
            error=error,
            stdout_log_path=result.stdout_log_path,
            stderr_log_path=result.stderr_log_path,
        )
        raise FeishuPublishError(f"feishu-cli publish failed: {error}. Report: {report}")

    try:
        payload = parse_json_payload(stdout)
        document_id = str(payload.get("document_id") or payload.get("documentId") or "").strip()
        if not document_id:
            raise FeishuPublishError("feishu-cli success output did not include document_id.")
        document_url = parse_document_url(stderr, document_id)
        image_result = f"{payload.get('image_success', 0)}/{payload.get('image_total', 0)}"
    except Exception as exc:
        error = str(exc)
        feishu.pop("documentId", None)
        feishu.pop("documentUrl", None)
        feishu.update(
            {
                "status": "failed",
                "error": error,
                "backend": "feishu-cli",
                "failedAt": utc_now(),
            }
        )
        write_json(metadata_path, metadata)
        report = write_publish_report(
            output_dir,
            command=command,
            status="failed",
            permission=feishu.get("permission") if isinstance(feishu.get("permission"), dict) else None,
            stdout=stdout,
            stderr=stderr,
            error=error,
            stdout_log_path=result.stdout_log_path,
            stderr_log_path=result.stderr_log_path,
        )
        raise FeishuPublishError(f"failed to parse feishu-cli publish result: {error}. Report: {report}") from exc

    permission = grant_edit_permission(
        cli=cli,
        document_id=document_id,
        env=env,
        args=permission_args,
        logs_dir=output_dir / "logs",
    )
    blocks_summary = None
    if getattr(permission_args, "check_blocks", False):
        try:
            blocks_summary = fetch_doc_blocks_summary(
                cli=cli,
                document_id=document_id,
                output_dir=output_dir,
                env=env,
                max_output_bytes=getattr(permission_args, "blocks_max_bytes", COMMAND_MAX_OUTPUT_BYTES),
            )
            feishu["blocksCheck"] = {"status": "checked", **blocks_summary}
        except FeishuPublishError as exc:
            feishu["blocksCheck"] = {
                "status": "failed",
                "checkedAt": utc_now(),
                "error": str(exc),
                "requiresRemoteCheck": True,
            }

    feishu.pop("error", None)
    feishu.pop("failedAt", None)
    feishu.update(
        {
            "status": "published",
            "documentId": document_id,
            "documentUrl": document_url,
            "publishedAt": utc_now(),
            "backend": "feishu-cli",
            "permission": {
                key: value
                for key, value in permission.items()
                if key not in {"stdout", "stderr"}
            },
        }
    )
    metadata["updatedAt"] = utc_now()
    write_json(metadata_path, metadata)
    report = write_publish_report(
        output_dir,
        command=command,
        status="published",
        document_id=document_id,
        document_url=document_url,
        image_result=image_result,
        permission=permission,
        stdout=stdout,
        stderr=stderr,
        stdout_log_path=result.stdout_log_path,
        stderr_log_path=result.stderr_log_path,
        blocks_summary=blocks_summary,
    )
    return {
        "outputDir": str(output_dir),
        "documentId": document_id,
        "documentUrl": document_url,
        "reportPath": str(report),
        "status": "published",
        "blocksCheck": blocks_summary,
    }


def fetch_doc_blocks_summary(
    *,
    cli: Path,
    document_id: str,
    output_dir: Path,
    env: dict[str, str],
    max_output_bytes: int = COMMAND_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    command = [str(cli), "doc", "blocks", document_id, "--all", "-o", "json"]
    started = time.monotonic()
    result = run_logged_command(
        command,
        label="feishu-doc-blocks",
        timeout_seconds=DOC_BLOCKS_TIMEOUT_SECONDS,
        logs_dir=output_dir / "logs",
        env=env,
    )
    if result.timed_out:
        raise FeishuPublishError(
            f"feishu-cli doc blocks timed out after {DOC_BLOCKS_TIMEOUT_SECONDS}s. "
            "Remote state was not verified."
        )
    if result.returncode != 0:
        raise FeishuPublishError(result.stderr.strip() or result.stdout.strip() or "feishu-cli doc blocks failed")
    output_size = len(result.stdout.encode("utf-8"))
    if output_size > max_output_bytes:
        raise FeishuPublishError(
            f"feishu-cli doc blocks output exceeded {max_output_bytes} bytes; "
            f"full output log: {result.stdout_log_path}"
        )
    blocks = json.loads(result.stdout)
    if isinstance(blocks, dict):
        blocks = blocks.get("data", {}).get("items") or blocks.get("items") or []
    if not isinstance(blocks, list):
        raise FeishuPublishError("feishu-cli doc blocks output was not a block list")
    images = [block for block in blocks if isinstance(block, dict) and block.get("image")]
    snapshot_path = output_dir / "logs" / f"{log_timestamp()}-doc-blocks-{document_id}.json"
    snapshot_path.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    cover_found = any(
        (image.get("image") or {}).get("width") and (image.get("image") or {}).get("height")
        for image in images
    )
    return {
        "doc_token": document_id,
        "blocks_count": len(blocks),
        "image_blocks_count": len(images),
        "cover_found": bool(cover_found),
        "elapsed": round(time.monotonic() - started, 3),
        "snapshot_path": str(snapshot_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish one prepared ContentFactory output to Feishu via feishu-cli.")
    parser.add_argument("output_dir")
    parser.add_argument("--cli", default=str(DEFAULT_CLI))
    parser.add_argument("--settings-db", default=str(DEFAULT_SETTINGS_DB))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--owner-email", default="")
    parser.add_argument("--owner-user-id", default="")
    parser.add_argument("--owner-open-id", default="")
    parser.add_argument("--owner-union-id", default="")
    parser.add_argument("--check-blocks", action="store_true", help="Explicitly fetch doc blocks after publish.")
    parser.add_argument("--blocks-max-bytes", type=int, default=COMMAND_MAX_OUTPUT_BYTES)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = publish_to_feishu(
            Path(args.output_dir),
            cli=Path(args.cli),
            settings_db=Path(args.settings_db),
            force=args.force,
            permission_args=args,
        )
    except FeishuPublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
