#!/usr/bin/env python3
"""Generate standardized cover images for ContentFactory outputs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
IMAGINE_SCRIPT = SCRIPT_DIR.parents[1] / "baoyu-imagine" / "scripts" / "main.ts"
COVER_RATIO = "4:3"
COVER_WIDTH = 1200
COVER_HEIGHT = 900
CODEX_PROVIDER = "codex-imagegen"
EXTERNAL_GENERATOR_TIMEOUT_SECONDS = 180
SIPS_TIMEOUT_SECONDS = 15
COMMAND_REPORT_CHARS = 500


class CoverImageError(RuntimeError):
    pass


def command_log_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_log_label(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip("-") or "command"


def coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def write_command_logs(
    logs_dir: Path,
    *,
    label: str,
    stdout: str,
    stderr: str,
    command: list[str],
    returncode: int,
    timeout_seconds: int,
) -> dict[str, str]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{command_log_timestamp()}-{safe_log_label(label)}"
    stdout_path = logs_dir / f"{prefix}.stdout.log"
    stderr_path = logs_dir / f"{prefix}.stderr.log"
    meta_path = logs_dir / f"{prefix}.meta.json"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "command": command,
                "returncode": returncode,
                "timeoutSeconds": timeout_seconds,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "stdoutLogPath": str(stdout_path),
        "stderrLogPath": str(stderr_path),
        "metaLogPath": str(meta_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate images/cover.png for one or more output directories.")
    parser.add_argument("output_dirs", nargs="*", help="Path(s) to 04-Outputs/YYYY-MM-DD-slug directories.")
    parser.add_argument("--scan", default="", help="Scan one 04-Outputs directory for outputs without generated covers.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum scanned outputs to process. Default: 5.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing images/cover.png.")
    parser.add_argument("--provider", default=CODEX_PROVIDER, help="Cover provider marker. ContentFactory covers must use Codex direct image generation.")
    parser.add_argument("--model", default="", help="Ignored for Codex direct image generation; kept for CLI compatibility.")
    parser.add_argument("--quality", default="normal", help="Ignored for Codex direct image generation; kept for CLI compatibility.")
    parser.add_argument("--env-file", default=os.environ.get("CONTENT_FACTORY_COVER_ENV_FILE", ""), help="Optional .env file to load before generation.")
    return parser.parse_args()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        raise CoverImageError(f"Env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CoverImageError(f"metadata.json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoverImageError(f"metadata.json is not valid JSON: {exc}") from exc


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_cover(metadata: dict[str, Any], payload: dict[str, Any]) -> None:
    images = metadata.setdefault("images", {})
    cover = images.setdefault("cover", {})
    cover.update(payload)


def mark_failed(metadata_path: Path, metadata: dict[str, Any] | None, message: str) -> None:
    if metadata is None:
        return
    update_cover(
        metadata,
        {
            "status": "failed",
            "promptFile": "cover-prompt.md",
            "outputPath": "",
            "ratio": COVER_RATIO,
            "error": message,
        },
    )
    write_metadata(metadata_path, metadata)


def cover_status(metadata: dict[str, Any]) -> str:
    return str(metadata.get("images", {}).get("cover", {}).get("status", ""))


def generator_command(prompt_path: Path, raw_image_path: Path, args: argparse.Namespace) -> list[str]:
    override = os.environ.get("CONTENT_FACTORY_COVER_GENERATOR")
    if override:
        command = shlex.split(override)
    else:
        command = ["npx", "-y", "bun", str(IMAGINE_SCRIPT)]

    command += [
        "--promptfiles",
        str(prompt_path),
        "--image",
        str(raw_image_path),
        "--ar",
        COVER_RATIO,
        "--json",
    ]
    if not override:
        command += ["--provider", args.provider, "--quality", args.quality]
        if args.model:
            command += ["--model", args.model]
    return command


def run_generator(prompt_path: Path, raw_image_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if args.provider != CODEX_PROVIDER:
        raise CoverImageError(
            "External cover image providers are disabled for ContentFactory. "
            "Use Codex direct image generation, then copy the generated image to images/cover.png."
        )
    command = generator_command(prompt_path, raw_image_path, args)
    logs_dir = raw_image_path.parent / "logs"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=EXTERNAL_GENERATOR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = coerce_output(exc.stdout)
        stderr = coerce_output(exc.stderr)
        logs = write_command_logs(
            logs_dir,
            label="image-generator-timeout",
            stdout=stdout,
            stderr=stderr,
            command=command,
            returncode=124,
            timeout_seconds=EXTERNAL_GENERATOR_TIMEOUT_SECONDS,
        )
        detail = stderr.strip() or stdout.strip() or "no output"
        raise CoverImageError(
            f"Image generation timed out after {EXTERNAL_GENERATOR_TIMEOUT_SECONDS}s; "
            f"no retry was attempted. Last output: {detail[:COMMAND_REPORT_CHARS]}. Logs: {logs}"
        ) from exc
    write_command_logs(
        logs_dir,
        label="image-generator",
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        command=command,
        returncode=result.returncode,
        timeout_seconds=EXTERNAL_GENERATOR_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr = (result.stderr.strip() or result.stdout.strip())[:COMMAND_REPORT_CHARS]
        raise CoverImageError(f"Image generation failed: {stderr}")
    if not raw_image_path.exists():
        raise CoverImageError("Image generation finished but did not create an image file.")

    stdout = result.stdout.strip()
    try:
        payload = json.loads(stdout[stdout.find("{") :]) if "{" in stdout else {}
    except json.JSONDecodeError:
        payload = {}
    return payload


def mark_codex_generation_required(metadata_path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    update_cover(
        metadata,
        {
            "status": "prompt_ready",
            "promptFile": "cover-prompt.md",
            "outputPath": "images/cover.png",
            "ratio": COVER_RATIO,
            "provider": CODEX_PROVIDER,
            "model": "codex-direct-image-generation",
            "generationMode": "codex_tool_required",
            "note": (
                "Use Codex direct image generation only. Do not call external image APIs "
                "and do not download images from the web."
            ),
        },
    )
    write_metadata(metadata_path, metadata)
    return metadata["images"]["cover"]


def normalize_png(raw_image_path: Path, final_image_path: Path) -> None:
    final_image_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "sips",
        "-s",
        "format",
        "png",
        "-p",
        str(COVER_HEIGHT),
        str(COVER_WIDTH),
        "--padColor",
        "FFFFFF",
        str(raw_image_path),
        "--out",
        str(final_image_path),
    ]
    logs_dir = final_image_path.parent / "logs"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SIPS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = coerce_output(exc.stdout)
        stderr = coerce_output(exc.stderr)
        logs = write_command_logs(
            logs_dir,
            label="sips-normalize-timeout",
            stdout=stdout,
            stderr=stderr,
            command=command,
            returncode=124,
            timeout_seconds=SIPS_TIMEOUT_SECONDS,
        )
        raise CoverImageError(
            f"Timed out after {SIPS_TIMEOUT_SECONDS}s while normalizing cover image with sips: "
            f"{stderr.strip()[:COMMAND_REPORT_CHARS]}. Logs: {logs}"
        ) from exc
    write_command_logs(
        logs_dir,
        label="sips-normalize",
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        command=command,
        returncode=result.returncode,
        timeout_seconds=SIPS_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise CoverImageError(
            f"Failed to normalize cover image to {COVER_WIDTH}x{COVER_HEIGHT} PNG: "
            f"{result.stderr.strip()[:COMMAND_REPORT_CHARS]}"
        )


def read_dimensions(image_path: Path) -> tuple[int, int]:
    command = ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image_path)]
    logs_dir = image_path.parent / "logs"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SIPS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = coerce_output(exc.stdout)
        stderr = coerce_output(exc.stderr)
        logs = write_command_logs(
            logs_dir,
            label="sips-dimensions-timeout",
            stdout=stdout,
            stderr=stderr,
            command=command,
            returncode=124,
            timeout_seconds=SIPS_TIMEOUT_SECONDS,
        )
        raise CoverImageError(
            f"Timed out after {SIPS_TIMEOUT_SECONDS}s while reading image dimensions with sips: "
            f"{stderr.strip()[:COMMAND_REPORT_CHARS]}. Logs: {logs}"
        ) from exc
    write_command_logs(
        logs_dir,
        label="sips-dimensions",
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        command=command,
        returncode=result.returncode,
        timeout_seconds=SIPS_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise CoverImageError(f"Failed to read image dimensions: {result.stderr.strip()[:COMMAND_REPORT_CHARS]}")
    width = height = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("pixelWidth:"):
            width = int(stripped.split(":", 1)[1].strip())
        if stripped.startswith("pixelHeight:"):
            height = int(stripped.split(":", 1)[1].strip())
    if width is None or height is None:
        raise CoverImageError("Could not parse image dimensions.")
    return width, height


def generate_one(output_dir: Path, args: argparse.Namespace, *, batch: bool = False) -> dict[str, Any]:
    metadata_path = output_dir / "metadata.json"
    prompt_path = output_dir / "cover-prompt.md"
    image_path = output_dir / "images" / "cover.png"
    raw_image_path = output_dir / "images" / "cover.raw"

    metadata: dict[str, Any] | None = None
    try:
        metadata = load_metadata(metadata_path)
        if batch and image_path.exists() and cover_status(metadata) == "generated" and not args.force:
            return {"output": output_dir, "status": "skipped", "detail": "cover already generated"}
        if not prompt_path.exists():
            raise CoverImageError(f"cover-prompt.md not found: {prompt_path}")
        if image_path.exists() and not args.force:
            raise CoverImageError(f"images/cover.png already exists. Pass --force to overwrite.")

        if args.provider == CODEX_PROVIDER:
            cover = mark_codex_generation_required(metadata_path, metadata)
            return {
                "output": output_dir,
                "status": "manual_required",
                "detail": "Codex direct image generation required; no external image provider was called.",
                "cover": cover,
            }

        image_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_image_path.exists():
            raw_image_path.unlink()
        payload = run_generator(prompt_path, raw_image_path, args)
        normalize_png(raw_image_path, image_path)
        width, height = read_dimensions(image_path)
        if width != COVER_WIDTH or height != COVER_HEIGHT:
            raise CoverImageError(f"Generated cover has invalid dimensions: {width}x{height}")

        provider = payload.get("provider") or args.provider
        model = payload.get("model") or args.model or os.environ.get(f"{provider.upper()}_IMAGE_MODEL", "")
        update_cover(
            metadata,
            {
                "status": "generated",
                "promptFile": "cover-prompt.md",
                "outputPath": "images/cover.png",
                "ratio": COVER_RATIO,
                "width": COVER_WIDTH,
                "height": COVER_HEIGHT,
                "provider": provider,
                "model": model,
                "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )
        write_metadata(metadata_path, metadata)
        if raw_image_path.exists():
            raw_image_path.unlink()
        return {"output": output_dir, "status": "success", "detail": "generated", "cover": metadata["images"]["cover"]}
    except CoverImageError as exc:
        mark_failed(metadata_path, metadata, str(exc))
        return {"output": output_dir, "status": "failed", "detail": str(exc)}


def discover_outputs(scan_root: Path, limit: int, force: bool) -> list[Path]:
    candidates: list[Path] = []
    for metadata_path in sorted(scan_root.glob("*/metadata.json")):
        output_dir = metadata_path.parent
        prompt_path = output_dir / "cover-prompt.md"
        if not prompt_path.exists():
            continue
        try:
            metadata = load_metadata(metadata_path)
        except CoverImageError:
            continue
        image_path = output_dir / "images" / "cover.png"
        if image_path.exists() and cover_status(metadata) == "generated" and not force:
            continue
        candidates.append(output_dir)
        if len(candidates) >= limit:
            break
    return candidates


def batch_summary_path(outputs_root: Path) -> Path:
    batch_dir = outputs_root / "batch-runs"
    batch_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    index = 1
    while True:
        path = batch_dir / f"{date}-cover-batch-{index:02d}.md"
        if not path.exists():
            return path
        index += 1


def write_batch_summary(outputs_root: Path, results: list[dict[str, Any]]) -> Path:
    path = batch_summary_path(outputs_root)
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    lines = [
        "---",
        "type: cover_batch_summary",
        f"created_at: {created_at}",
        f"item_count: {len(results)}",
        "---",
        "",
        "# Cover Batch Summary",
        "",
        "| output | status | detail |",
        "| --- | --- | --- |",
    ]
    for result in results:
        output = Path(result["output"]).name
        detail = str(result.get("detail", "")).replace("\n", " ")
        lines.append(f"| {output} | {result['status']} | {detail} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def common_outputs_root(output_dirs: list[Path], scan_root: Path | None) -> Path:
    if scan_root is not None:
        return scan_root
    if not output_dirs:
        return Path.cwd()
    return output_dirs[0].parent


def main() -> int:
    args = parse_args()
    if args.env_file:
        try:
            load_dotenv(Path(args.env_file).expanduser().resolve())
        except CoverImageError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    scan_root: Path | None = None
    output_dirs = [Path(item).expanduser().resolve() for item in args.output_dirs]
    if args.scan:
        scan_root = Path(args.scan).expanduser().resolve()
        output_dirs.extend(discover_outputs(scan_root, max(args.limit, 0), args.force))

    if not output_dirs:
        print("No output directories provided.", file=sys.stderr)
        return 2

    is_batch = bool(args.scan) or len(output_dirs) > 1
    if not is_batch:
        result = generate_one(output_dirs[0], args, batch=False)
        if result["status"] in {"success", "manual_required", "skipped"}:
            print(json.dumps(result.get("cover", result), ensure_ascii=False, indent=2))
            return 0
        print(result["detail"], file=sys.stderr)
        return 1

    results = [generate_one(output_dir, args, batch=True) for output_dir in output_dirs]
    summary = write_batch_summary(common_outputs_root(output_dirs, scan_root), results)
    print(
        json.dumps(
            {
                "summary": str(summary),
                "results": [
                    {
                        "output": str(result["output"]),
                        "status": result["status"],
                        "detail": result.get("detail", ""),
                    }
                    for result in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
