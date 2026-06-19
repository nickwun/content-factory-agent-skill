#!/usr/bin/env python3
"""Run one ContentFactory article pipeline from unused DOCX to used output."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from normalize_docx_material import normalize_one
from source_registry import (
    discover_docx_files,
    index_by_source_id,
    read_registry,
    update_markdown_source_status,
    update_record,
    upsert_docx_records,
    utc_now,
)


SCRIPT_DIR = Path(__file__).resolve().parent
COVER_SCRIPT = SCRIPT_DIR / "generate_cover_image.py"
TITLE_SCRIPT = SCRIPT_DIR / "generate_titles.py"
DEFAULT_VAULT = Path("/Users/hui/Documents/ContentFactoryVault")
DEFAULT_PROFILE = DEFAULT_VAULT / "02-Profiles" / "ahong-running-rewrite.md"
DEFAULT_CORPUS = DEFAULT_VAULT / "03-Corpus" / "ahong-running-style.md"
ARTICLE_GENERATOR_TIMEOUT_SECONDS = 180
COVER_SCRIPT_TIMEOUT_SECONDS = 180
TITLE_SCRIPT_TIMEOUT_SECONDS = 60
COMMAND_REPORT_CHARS = 500


class PipelineError(RuntimeError):
    pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        raise PipelineError(f"Env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def command_log_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_log_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "command"


def coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def write_command_logs(
    output_dir: Path,
    *,
    label: str,
    stdout: str,
    stderr: str,
    command: list[str],
    returncode: int,
    timeout_seconds: int,
) -> dict[str, str]:
    logs_dir = output_dir / "logs"
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


def parse_frontmatter_text(markdown: str) -> dict[str, str]:
    if not markdown.startswith("---\n"):
        return {}
    end = markdown.find("\n---\n", 4)
    if end < 0:
        return {}
    data: dict[str, str] = {}
    for line in markdown[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def file_stem_id(path: Path, fallback: str) -> str:
    data = parse_frontmatter_text(path.read_text(encoding="utf-8"))
    return data.get("profile_id") or data.get("corpus_id") or fallback


def ensure_registry(vault: Path) -> list[dict[str, Any]]:
    registry = read_registry(vault)
    if registry:
        return registry
    files = discover_docx_files(vault)
    if not files:
        return []
    return upsert_docx_records(vault, files)


def choose_source(vault: Path, source_id: str = "") -> dict[str, Any]:
    registry = ensure_registry(vault)
    if source_id:
        record = index_by_source_id(registry).get(source_id)
        if not record:
            raise PipelineError(f"sourceId not found: {source_id}")
        return record

    for record in registry:
        if str(record.get("status") or "unused") == "unused":
            return record
    raise PipelineError("No unused DOCX source found in source-registry.json.")


def append_note(vault: Path, source_id: str, note: str, status: str = "failed") -> dict[str, Any]:
    record = index_by_source_id(read_registry(vault)).get(source_id)
    if not record:
        raise PipelineError(f"sourceId not found while adding note: {source_id}")
    notes = list(record.get("notes") or [])
    notes.append(note)
    return update_record(vault, source_id, {"status": status, "notes": notes})


def mark_source(
    vault: Path,
    source_id: str,
    *,
    status: str,
    output_dir: Path | None = None,
    profile_id: str = "",
    corpus_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    updates: dict[str, Any] = {"status": status}
    if status == "used":
        updates["usedAt"] = utc_now()
    if output_dir is not None:
        updates["usedByOutput"] = str(output_dir)
    if profile_id:
        updates["profile"] = profile_id
    if corpus_id:
        updates["corpus"] = corpus_id
    if note:
        record = index_by_source_id(read_registry(vault)).get(source_id)
        notes = list((record or {}).get("notes") or [])
        notes.append(note)
        updates["notes"] = notes
    record = update_record(vault, source_id, updates)
    normalized_path = str(record.get("normalizedPath") or "")
    if normalized_path:
        update_markdown_source_status(
            Path(normalized_path),
            source_status=status,
            used_at=str(record.get("usedAt") or ""),
            used_by_output=str(record.get("usedByOutput") or ""),
        )
    return record


def safe_output_slug(title: str, source_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    parts = re.findall(r"[a-z0-9]+", ascii_text)
    slug = "-".join(parts).strip("-")
    safe_source_id = re.sub(r"[^a-z0-9-]+", "-", source_id.lower()).strip("-")
    if len(slug) < 8:
        slug = f"{slug}-{safe_source_id}" if slug else safe_source_id
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:96].strip("-") or safe_source_id


def output_dir_for(vault: Path, title: str, source_id: str) -> tuple[Path, str]:
    date = datetime.now().strftime("%Y-%m-%d")
    slug = safe_output_slug(title, source_id)
    base = vault / "04-Outputs" / f"{date}-{slug}"
    if not base.exists():
        return base, slug
    index = 2
    while True:
        next_slug = f"{slug}-{index:02d}"
        candidate = vault / "04-Outputs" / f"{date}-{next_slug}"
        if not candidate.exists():
            return candidate, next_slug
        index += 1


def compact_source(markdown: str, max_chars: int = 12000) -> str:
    body = re.sub(r"^---\n.*?\n---\n", "", markdown, flags=re.S).strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars] + "\n\n[素材过长，后文已截断用于本次生成。]"


def build_generation_prompt(
    *,
    source_id: str,
    source_markdown: str,
    profile_text: str,
    corpus_text: str,
    profile_path: Path,
    corpus_path: Path,
) -> str:
    return f"""你是 ContentFactoryVault 的无 UI 文章生产 Agent。请严格基于给定素材、Profile 和 Corpus 生成一篇新的 Markdown 公众号文章。

必须输出 JSON，且只能输出 JSON，不要 Markdown 代码围栏。JSON 字段：
- title: 文章标题
- article: 完整 Markdown 正文
- brief: 生成简报 Markdown
- coverPrompt: 公众号头图 prompt Markdown

硬性规则：
- article 必须从 `# 标题` 开始。
- article 必须使用 01、02、03 模块结构。
- article 总字数控制在 1100-1300 字。
- 篇幅规划：开头 2-3 个短段；每个模块至少 4 个短段；结尾 2 个短段。
- 如果正文少于 1100 字，本次输出视为失败。不要输出短稿、提纲稿或摘要稿。
- article 不得出现“素材里提到”“素材里说”“原文说”“根据资料”“待重写素材”等元叙述。
- 不得复制素材原句，不得复制 corpus 原句。
- coverPrompt 只服务头图，输出比例 4:3。
- 头图不要模板化：必须根据文章主题选择不同视觉主体、画面场景、色调、构图和风格，不要连续使用“清晨公园跑道 + 中国中年男性跑者”的固定画面。
- 如果画面包含人物，人物必须符合中文公众号语境下的中国人/亚洲中国面孔，可以是男性或女性，可以是青年、中年或银发跑者；不要默认中国中年男性。
- coverPrompt 必须明确写出人物性别和年龄段，并说明为什么符合文章主题；除非主题明确要求男性，否则不要默认男性。
- 批量生产时，封面人物性别要大致均衡，5篇一批目标为2女3男或3女2男；年龄和场景也要与各自文章主题错开。
- 场景必须跟随文章主题变化：可选城市街区、社区跑道、江边绿道、冬天街景、室内拉伸、家中换鞋、办公室下班后、体检/健康提醒的克制场景、赛事终点、雨后路面等。
- 风格也要变化：可选纪实摄影、温暖插画、杂志感封面、极简海报、柔和胶片、扁平插画；构图可选跑步中景、背影、鞋/表局部、家门口换鞋、室内恢复、城市下班路、江边绿道、跑团远景对比等，但不得出现文字、logo、欧美人物、非中国跑者、医疗恐吓或夸张表情。

sourceId: {source_id}
profilePath: {profile_path}
corpusPath: {corpus_path}

## Profile

{profile_text}

## Corpus

{corpus_text}

## 待处理素材 Markdown

{compact_source(source_markdown)}
"""


def parse_json_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(stripped[start : end + 1])
        else:
            raise PipelineError(f"Article generator did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PipelineError("Article generator returned a non-object JSON payload.")
    return payload


def run_external_article_generator(prompt: str, output_dir: Path) -> dict[str, Any]:
    command_value = os.environ.get("CONTENT_FACTORY_ARTICLE_GENERATOR", "")
    if not command_value:
        raise PipelineError("CONTENT_FACTORY_ARTICLE_GENERATOR is not configured.")

    command = shlex.split(command_value)
    full_command: list[str] = []
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as tmp:
        tmp.write(prompt)
        prompt_file = Path(tmp.name)
    try:
        full_command = [*command, "--prompt-file", str(prompt_file), "--output-dir", str(output_dir)]
        try:
            result = subprocess.run(
                full_command,
                check=False,
                capture_output=True,
                text=True,
                timeout=ARTICLE_GENERATOR_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = coerce_output(exc.stdout)
            stderr = coerce_output(exc.stderr)
            logs = write_command_logs(
                output_dir,
                label="article-generator-timeout",
                stdout=stdout,
                stderr=stderr,
                command=full_command,
                returncode=124,
                timeout_seconds=ARTICLE_GENERATOR_TIMEOUT_SECONDS,
            )
            message = stderr.strip() or stdout.strip() or "no output"
            raise PipelineError(
                f"Article generation timed out after {ARTICLE_GENERATOR_TIMEOUT_SECONDS}s; "
                f"no retry was attempted. Last output: {message[:COMMAND_REPORT_CHARS]}. Logs: {logs}"
            ) from exc
    finally:
        prompt_file.unlink(missing_ok=True)

    write_command_logs(
        output_dir,
        label="article-generator",
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        command=full_command,
        returncode=result.returncode,
        timeout_seconds=ARTICLE_GENERATOR_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        message = (result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")[:COMMAND_REPORT_CHARS]
        raise PipelineError(f"Article generation failed: {message}")
    return parse_json_payload(result.stdout)


def run_openrouter_article_generator(prompt: str, model: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise PipelineError("OPENROUTER_API_KEY is not configured.")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    payload = {
        "model": model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.4-mini"),
        "messages": [
            {"role": "system", "content": "你是严谨的中文公众号文章生产 Agent，只输出请求的 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": int(os.environ.get("CONTENT_FACTORY_ARTICLE_MAX_TOKENS", "5000")),
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://content-factory.local",
            "X-Title": "Content Factory Agent",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise PipelineError(f"OpenRouter article generation failed: HTTP {exc.code} {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise PipelineError(f"OpenRouter article generation failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PipelineError("OpenRouter response did not include message content.") from exc
    return parse_json_payload(str(content))


def generate_article_payload(prompt: str, output_dir: Path, model: str) -> dict[str, Any]:
    if os.environ.get("CONTENT_FACTORY_ARTICLE_GENERATOR"):
        return run_external_article_generator(prompt, output_dir)
    return run_openrouter_article_generator(prompt, model)


def article_char_count(article: str) -> int:
    without_title = re.sub(r"^# .*$", "", article, count=1, flags=re.M)
    return len(re.sub(r"\s+", "", without_title))


def validate_article(article: str, *, min_chars: int = 1100, max_chars: int = 1300) -> None:
    stripped = article.lstrip()
    if not stripped.startswith("# "):
        raise PipelineError("article must start with one '# ' title line.")
    first_line = stripped.splitlines()[0].strip()
    if first_line in {"# 标题", "# 文章标题", "# 未命名文章"}:
        raise PipelineError("article title line is generic placeholder.")
    modules = re.findall(r"^##\s*0[1-9]、.{1,20}$", article, flags=re.M)
    if len(modules) < 3:
        raise PipelineError("article must include at least 3 numbered modules like '## 01、模块标题'.")
    forbidden = ["素材里提到", "素材里说", "原文说", "原文提到", "根据资料", "待重写素材", "本文素材"]
    found = [item for item in forbidden if item in article]
    if found:
        raise PipelineError(f"article contains internal source narration: {', '.join(found)}")
    count = article_char_count(article)
    if count < min_chars or count > max_chars:
        raise PipelineError(f"article length out of range: {count} chars, expected {min_chars}-{max_chars}.")


def ensure_required_payload(payload: dict[str, Any]) -> dict[str, str]:
    title = str(payload.get("title") or "").strip()
    article = str(payload.get("article") or "").strip()
    brief = str(payload.get("brief") or "").strip()
    cover_prompt = str(payload.get("coverPrompt") or payload.get("cover_prompt") or "").strip()
    if not title:
        title = first_markdown_title(article) or "未命名文章"
    if not article:
        raise PipelineError("Article generator returned empty article.")
    if not article.lstrip().startswith("# "):
        article = f"# {title}\n\n{article}"
    first_line = article.lstrip().splitlines()[0].strip() if article.strip() else ""
    if first_line in {"# 标题", "# 文章标题", "# 未命名文章"} and title:
        article = re.sub(r"^# .*$", f"# {title}", article, count=1, flags=re.M)
    if not brief:
        brief = f"# Brief\n\n- 文章标题：{title}\n- 由单篇 pipeline 自动生成。"
    if not cover_prompt:
        cover_prompt = fallback_cover_prompt(title)
    validate_article(article)
    return {"title": title, "article": article + "\n", "brief": brief + "\n", "coverPrompt": cover_prompt + "\n"}


def first_markdown_title(article: str) -> str:
    for line in article.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def fallback_cover_prompt(title: str) -> str:
    return f"""# Cover Prompt

## 文章标题

{title}

## 文章主题

大众跑步与生活节奏。

## 核心情绪

温和、坚定、可持续。

## 视觉主体

中国普通跑者，可以是男性或女性，也可以是青年、中年或银发跑者；人物必须是亚洲中国面孔，不要固定为中国中年男性。

必须根据文章主题明确指定性别和年龄段；批量生成时与同批其他封面保持性别、年龄和场景多样性。

## 画面场景

根据主题选择生活化场景，不要固定为清晨公园跑道。可选冬天街景、社区跑道、江边绿道、家门口换鞋、室内拉伸、办公室下班后、赛事终点、雨后路面或健康提醒场景。

同一批 5 篇尽量使用 5 个不同场景，避免连续使用公园跑道。

## 色调

清爽自然、暖光、低饱和。

## 构图

4:3 横图，构图多样化，可以是人物近中景、背影、局部动作、空镜加跑步物件或人物与环境结合，留出公众号封面裁切空间。

## 风格

根据主题选择干净、有生活感的视觉风格，可在纪实摄影、温暖插画、杂志感封面、极简海报、柔和胶片、扁平插画之间变化。

## 禁止元素

文字、logo、西方面孔、欧美人物、非中国跑者、夸张医疗恐吓。

## 输出比例

4:3
"""


def write_output_files(
    *,
    output_dir: Path,
    payload: dict[str, str],
    source_record: dict[str, Any],
    source_markdown_path: Path,
    profile_path: Path,
    corpus_path: Path,
    profile_id: str,
    corpus_id: str,
    slug: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "article.md").write_text(payload["article"], encoding="utf-8")
    (output_dir / "brief.md").write_text(payload["brief"], encoding="utf-8")
    (output_dir / "cover-prompt.md").write_text(payload["coverPrompt"], encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "type": "output_metadata",
        "outputId": f"output-{output_dir.name}",
        "title": payload["title"],
        "slug": slug,
        "outputDir": str(output_dir),
        "status": "draft",
        "processingMode": "rewrite",
        "targetPlatform": "wechat_article",
        "profileId": profile_id,
        "corpusId": corpus_id,
        "profilePath": str(profile_path),
        "corpusPath": str(corpus_path),
        "sourceMaterials": [
            {
                "sourceId": source_record.get("sourceId", ""),
                "rawPath": source_record.get("rawPath", ""),
                "normalizedPath": str(source_markdown_path),
                "title": source_record.get("title", ""),
            }
        ],
        "createdAt": now,
        "updatedAt": now,
        "images": {
            "cover": {
                "promptFile": "cover-prompt.md",
                "outputPath": "images/cover.png",
                "status": "prompt_ready",
                "ratio": "4:3",
            }
        },
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata_path


def run_cover_generation(output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    command = ["python3", str(COVER_SCRIPT), str(output_dir)]
    if args.env_file:
        command += ["--env-file", args.env_file]
    if args.cover_provider:
        command += ["--provider", args.cover_provider]
    if args.cover_model:
        command += ["--model", args.cover_model]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=COVER_SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = coerce_output(exc.stdout)
        stderr = coerce_output(exc.stderr)
        logs = write_command_logs(
            output_dir,
            label="cover-script-timeout",
            stdout=stdout,
            stderr=stderr,
            command=command,
            returncode=124,
            timeout_seconds=COVER_SCRIPT_TIMEOUT_SECONDS,
        )
        return {
            "returnCode": 124,
            "status": "failed",
            "error": (
                f"cover script timed out after {COVER_SCRIPT_TIMEOUT_SECONDS}s; "
                f"no retry was attempted. Last output: {(stderr.strip() or stdout.strip())[:COMMAND_REPORT_CHARS]}. "
                f"Logs: {logs}"
            ),
            "cover": {},
        }
    write_command_logs(
        output_dir,
        label="cover-script",
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        command=command,
        returncode=result.returncode,
        timeout_seconds=COVER_SCRIPT_TIMEOUT_SECONDS,
    )
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    cover = metadata.get("images", {}).get("cover", {})
    return {
        "returnCode": result.returncode,
        "status": cover.get("status", "unknown"),
        "error": cover.get("error", result.stderr.strip()),
        "cover": cover,
    }


def run_title_generation(output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    command = ["python3", str(TITLE_SCRIPT), str(output_dir)]
    if args.env_file:
        command += ["--env-file", args.env_file]
    if args.text_model:
        command += ["--model", args.text_model]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=TITLE_SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = coerce_output(exc.stdout)
        stderr = coerce_output(exc.stderr)
        logs = write_command_logs(
            output_dir,
            label="title-script-timeout",
            stdout=stdout,
            stderr=stderr,
            command=command,
            returncode=124,
            timeout_seconds=TITLE_SCRIPT_TIMEOUT_SECONDS,
        )
        message = stderr.strip() or stdout.strip() or "no output"
        raise PipelineError(
            f"title_generation_timeout: title script timed out after {TITLE_SCRIPT_TIMEOUT_SECONDS}s; "
            f"no retry was attempted. Last output: {message[:COMMAND_REPORT_CHARS]}. Logs: {logs}"
        ) from exc
    write_command_logs(
        output_dir,
        label="title-script",
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        command=command,
        returncode=result.returncode,
        timeout_seconds=TITLE_SCRIPT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PipelineError(f"title_generation_failed: {message}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineError(f"title_generation_failed: invalid JSON stdout: {exc}") from exc
    failures = payload.get("failures") or []
    if failures:
        raise PipelineError(f"title_generation_failed: {failures}")
    results = payload.get("results") or []
    return results[0] if results else {"status": "generated"}


def write_pipeline_summary(
    output_dir: Path,
    *,
    source_id: str,
    source_status: str,
    normalized_path: str,
    article_status: str,
    titles_status: str,
    cover_status: str,
    error: str = "",
) -> Path:
    path = output_dir / "pipeline-summary.md"
    lines = [
        "---",
        "type: single_article_pipeline_summary",
        f"created_at: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}",
        f"sourceId: {source_id}",
        "---",
        "",
        "# Single Article Pipeline Summary",
        "",
        f"- sourceId: `{source_id}`",
        f"- sourceStatus: `{source_status}`",
        f"- normalizedPath: `{normalized_path}`",
        f"- articleStatus: `{article_status}`",
        f"- titlesStatus: `{titles_status}`",
        f"- coverStatus: `{cover_status}`",
    ]
    if error:
        lines.append(f"- error: {error}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    vault = Path(args.vault).expanduser().resolve()
    profile_path = Path(args.profile).expanduser().resolve()
    corpus_path = Path(args.corpus).expanduser().resolve()
    if args.env_file:
        load_dotenv(Path(args.env_file).expanduser().resolve())
    if not profile_path.exists():
        raise PipelineError(f"Profile not found: {profile_path}")
    if not corpus_path.exists():
        raise PipelineError(f"Corpus not found: {corpus_path}")

    record = choose_source(vault, args.source_id)
    source_id = str(record.get("sourceId") or "")
    status = str(record.get("status") or "unused")
    if status != "unused":
        raise PipelineError(f"Single pipeline only accepts unused sources. Current status: {status}")
    raw_path = Path(str(record.get("rawPath") or "")).expanduser().resolve()
    if not raw_path.exists():
        append_note(vault, source_id, f"normalize_failed: raw file not found: {raw_path}")
        raise PipelineError(f"Raw DOCX not found: {raw_path}")

    normalized = normalize_one(vault, raw_path)
    normalized_path = Path(normalized["markdown_path"]).resolve()
    cleaning_note_path = Path(normalized["cleaning_note_path"]).resolve()
    cleaning_note = cleaning_note_path.read_text(encoding="utf-8")
    if "是否可以进入仿写流程：否" in cleaning_note:
        mark_source(vault, source_id, status="failed", note="cleaning_note_blocked: manual review required")
        raise PipelineError(f"Cleaning note blocks rewrite: {cleaning_note_path}")

    title = str(normalized.get("title") or record.get("title") or raw_path.stem)
    output_dir, output_slug = output_dir_for(vault, title, source_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_id = file_stem_id(profile_path, profile_path.stem)
    corpus_id = file_stem_id(corpus_path, corpus_path.stem)
    mark_source(vault, source_id, status="processing", output_dir=output_dir, profile_id=profile_id, corpus_id=corpus_id)

    source_markdown = normalized_path.read_text(encoding="utf-8")
    profile_text = profile_path.read_text(encoding="utf-8")
    corpus_text = corpus_path.read_text(encoding="utf-8")
    prompt = build_generation_prompt(
        source_id=source_id,
        source_markdown=source_markdown,
        profile_text=profile_text,
        corpus_text=corpus_text,
        profile_path=profile_path,
        corpus_path=corpus_path,
    )

    try:
        payload: dict[str, str] | None = None
        last_error: Exception | None = None
        for attempt in range(1, max(args.article_attempts, 1) + 1):
            attempt_prompt = prompt
            if last_error is not None:
                attempt_prompt = (
                    prompt
                    + "\n\n## 上一次生成不合格，必须修正\n\n"
                    + f"- 错误：{last_error}\n"
                    + "- 这一次必须在不改变事实的前提下补足篇幅，完整扩写到 1100-1300 字，不要低于 1100 字。\n"
                    + "- 如果上一次偏短，请优先扩写生活化解释、普通跑者提醒和结尾收束，不要删减已有结构。\n"
                    + "- 每个 01/02/03 模块至少写 5 个短段，结尾再写 2-3 个短段。\n"
                    + "- 标题不能是占位符，必须包含 01/02/03 模块。\n"
                )
            raw_payload = generate_article_payload(attempt_prompt, output_dir, args.text_model)
            try:
                payload = ensure_required_payload(raw_payload)
                break
            except PipelineError as exc:
                last_error = exc
        if payload is None:
            raise PipelineError(str(last_error or "article generation failed validation"))
        write_output_files(
            output_dir=output_dir,
            payload=payload,
            source_record={**record, **normalized},
            source_markdown_path=normalized_path,
            profile_path=profile_path,
            corpus_path=corpus_path,
            profile_id=profile_id,
            corpus_id=corpus_id,
            slug=output_slug,
        )
    except Exception as exc:
        message = f"article_generation_failed: {exc}"
        mark_source(vault, source_id, status="failed", output_dir=output_dir, profile_id=profile_id, corpus_id=corpus_id, note=message)
        write_pipeline_summary(
            output_dir,
            source_id=source_id,
            source_status="failed",
            normalized_path=str(normalized_path),
            article_status="failed",
            titles_status="not_run",
            cover_status="not_run",
            error=message,
        )
        raise PipelineError(message) from exc

    try:
        title_result = run_title_generation(output_dir, args)
    except Exception as exc:
        message = f"title_generation_failed: {exc}"
        mark_source(vault, source_id, status="failed", output_dir=output_dir, profile_id=profile_id, corpus_id=corpus_id, note=message)
        write_pipeline_summary(
            output_dir,
            source_id=source_id,
            source_status="failed",
            normalized_path=str(normalized_path),
            article_status="generated",
            titles_status="failed",
            cover_status="not_run",
            error=message,
        )
        raise PipelineError(message) from exc

    cover_result = run_cover_generation(output_dir, args)
    final_record = mark_source(
        vault,
        source_id,
        status="used",
        output_dir=output_dir,
        profile_id=profile_id,
        corpus_id=corpus_id,
        note=(f"cover_generation_failed: {cover_result['error']}" if cover_result["status"] == "failed" else ""),
    )
    summary_path = write_pipeline_summary(
        output_dir,
        source_id=source_id,
        source_status="used",
        normalized_path=str(normalized_path),
        article_status="generated",
        titles_status="generated",
        cover_status=str(cover_result["status"]),
        error=str(cover_result["error"] or ""),
    )
    return {
        "sourceId": source_id,
        "rawPath": str(raw_path),
        "normalizedPath": str(normalized_path),
        "cleaningNotePath": str(cleaning_note_path),
        "outputDir": str(output_dir),
        "articleStatus": "generated",
        "titlesStatus": "generated",
        "titlesPath": str(title_result.get("titlesPath") or (output_dir / "titles.md")),
        "coverStatus": cover_result["status"],
        "sourceStatus": final_record.get("status"),
        "summaryPath": str(summary_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one DOCX -> article -> cover -> used source pipeline.")
    parser.add_argument("--vault", default=str(DEFAULT_VAULT))
    parser.add_argument("--source-id", default="")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--env-file", default="")
    parser.add_argument("--text-model", default=os.environ.get("OPENROUTER_MODEL", ""))
    parser.add_argument("--article-attempts", type=int, default=5)
    parser.add_argument("--cover-provider", default="codex-imagegen")
    parser.add_argument("--cover-model", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_pipeline(args)
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
