#!/usr/bin/env python3
"""Generate title candidate groups for one or more ContentFactory outputs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BANNED_TITLE_WORDS = ["震惊", "崩溃", "千万别"]


TITLE_FORMULA_GUIDE = """参考 Word 标题方法论：
- 首选结构：具体对象 + 读者身份 + 冲突问题 + 结果/代价。
- 具体对象优先使用正文里的跑步距离、配速、频率、年龄、人群，例如 5公里、10公里、半马、40岁后、普通跑者。
- 常用标题模型：
  1. 标准测试型：普通人跑完X公里，算什么水平？
  2. 反常识纠偏型：很多人以为X，其实Y。
  3. 数字清单提醒型：40岁后跑步，这3件事比配速更重要。
  4. 比较选择型：每天跑5公里，还是隔天跑10公里？
  5. 长期结果型：坚持跑步一年后，身体会发生什么？
  6. 年龄/阶段提醒型：人到中年，跑步最怕的不是慢。
- 吸收参考标题的冲突感，但降低恐吓、羞辱、擦边和医学绝对化。
- 将吓人标题改成提醒型标题，将猎奇标题改成普通跑者自查型标题。"""


class TitleGenerationError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        raise TitleGenerationError(f"Env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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


def first_title(article: str) -> str:
    for line in article.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def module_titles(article: str) -> list[str]:
    return [match.strip() for match in re.findall(r"^##\s*0[1-9]、(.+)$", article, flags=re.M)]


def compact_article(article: str, max_chars: int = 5000) -> str:
    body = article.strip()
    return body if len(body) <= max_chars else body[:max_chars] + "\n\n[后文已截断用于标题生成。]"


def extract_key_terms(text: str) -> list[str]:
    patterns = [
        r"\d+\s*公里",
        r"\d+\s*分钟",
        r"\d+\s*小时",
        r"\d+岁",
        r"半马",
        r"全马",
        r"马拉松",
        r"配速",
        r"晨跑",
        r"夜跑",
        r"普通跑者",
        r"大众跑者",
        r"中年",
    ]
    terms: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            term = re.sub(r"\s+", "", str(match))
            if term and term not in terms:
                terms.append(term)
    return terms


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
            raise TitleGenerationError(f"Title generator did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TitleGenerationError("Title generator returned non-object JSON.")
    return payload


def build_title_prompt(article: str, metadata: dict[str, Any]) -> str:
    title = str(metadata.get("title") or first_title(article) or "").strip()
    modules = "、".join(module_titles(article)[:5])
    return f"""请为下面这篇公众号文章生成标题候选。只输出 JSON，不要 Markdown 代码围栏。

JSON 字段：
- pain_point: 5 个标题字符串
- cognitive_gap: 5 个标题字符串
- recommended:
  - primary: 首选标题
  - secondary: 备选标题
  - reason: 推荐理由，1-2 句

标题要求：
- pain_point 要击中读者真实焦虑、困惑、代价感，让读者觉得“这说的是我”。
- cognitive_gap 要有反直觉、重新理解、认知翻转，让读者觉得“原来还能这么看”。
- 必须和文章事实强相关，不得脱离正文。
- 不要标题党，不要夸大健康风险。
- 不要使用“震惊、崩溃、千万别”等低质词。
- 不要所有标题都用同一种句式。
- 不要复制原文章标题，也不要复制 corpus 中标题或句子。

{TITLE_FORMULA_GUIDE}

原文章标题：{title}
模块线索：{modules}

## 文章正文

{compact_article(article)}
"""


def run_openrouter_title_generation(prompt: str, model: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise TitleGenerationError("OPENROUTER_API_KEY is not configured.")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    payload = {
        "model": model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.4-mini"),
        "messages": [
            {"role": "system", "content": "你是严谨的中文公众号标题策划，只输出请求的 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.75,
        "max_tokens": 1600,
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
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise TitleGenerationError(f"OpenRouter title generation failed: HTTP {exc.code} {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise TitleGenerationError(f"OpenRouter title generation failed: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TitleGenerationError("OpenRouter response did not include message content.") from exc
    return parse_json_payload(str(content))


def fallback_titles(article: str, metadata: dict[str, Any]) -> dict[str, Any]:
    title = str(metadata.get("title") or first_title(article) or "这次跑步提醒").strip()
    modules = module_titles(article)
    first_module = modules[0] if modules else "别急着逞强"
    second_module = modules[1] if len(modules) > 1 else "身体反馈更重要"
    third_module = modules[2] if len(modules) > 2 else "长期稳定才划算"
    subject = re.sub(r"[？?！!。,.，、:：]+$", "", title)
    terms = extract_key_terms(f"{title}\n{article}")
    primary_term = terms[0] if terms else subject
    comparison = "和".join(terms[:2]) if len(terms) >= 2 else primary_term
    pain_point = [
        f"普通跑者面对{comparison}，到底该怎么选才不容易跑偏？",
        f"{primary_term}不是问题，真正容易出错的是忽略{second_module}",
        f"为什么很多人跑步一认真，反而把恢复和热情都跑丢了？",
        f"{subject}：别只看当天跑了多少，更要看第二天身体怎么回信",
        f"刚开始跑步就想一步到位，普通人最容易吃这个亏",
    ]
    cognitive_gap = [
        f"{comparison}的差别，不只是数字和距离",
        f"普通跑者先学会{first_module}，比急着证明自己更重要",
        f"跑步不是越远越值，能长期恢复才是真正的有效",
        f"看懂{third_module}，你就不会总被配速和公里数牵着走",
        f"很多人以为跑得少没用，其实合适的距离更能留住状态",
    ]
    return {
        "pain_point": pain_point,
        "cognitive_gap": cognitive_gap,
        "recommended": {
            "primary": pain_point[0],
            "secondary": cognitive_gap[0],
            "reason": "首选标题更容易击中普通跑者的自我怀疑和节奏焦虑，备选标题则提供认知翻转，适合强调长期主义。",
        },
    }


def clean_title(value: Any, original_title: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    for word in BANNED_TITLE_WORDS:
        title = title.replace(word, "")
    title = title.strip(" -—")
    if title == original_title:
        title = ""
    return title


def normalize_title_payload(payload: dict[str, Any], article: str, metadata: dict[str, Any]) -> dict[str, Any]:
    original_title = str(metadata.get("title") or first_title(article) or "").strip()
    fallback = fallback_titles(article, metadata)

    def normalize_list(key: str) -> list[str]:
        values = payload.get(key)
        if not isinstance(values, list):
            values = []
        titles: list[str] = []
        for item in values:
            title = clean_title(item, original_title)
            if title and title not in titles:
                titles.append(title)
        for item in fallback[key]:
            title = clean_title(item, original_title)
            if title and title not in titles:
                titles.append(title)
            if len(titles) >= 5:
                break
        return titles[:5]

    pain_point = normalize_list("pain_point")
    cognitive_gap = normalize_list("cognitive_gap")
    recommended = payload.get("recommended")
    if not isinstance(recommended, dict):
        recommended = {}
    primary = clean_title(recommended.get("primary"), original_title) or pain_point[0]
    secondary = clean_title(recommended.get("secondary"), original_title) or cognitive_gap[0]
    reason = re.sub(r"\s+", " ", str(recommended.get("reason") or "")).strip()
    if not reason:
        reason = fallback["recommended"]["reason"]
    return {
        "pain_point": pain_point,
        "cognitive_gap": cognitive_gap,
        "recommended": {
            "primary": primary,
            "secondary": secondary,
            "reason": reason,
        },
    }


def write_titles_markdown(output_dir: Path, titles: dict[str, Any]) -> Path:
    path = output_dir / "titles.md"
    pain_point = titles["pain_point"]
    cognitive_gap = titles["cognitive_gap"]
    recommended = titles["recommended"]
    lines = [
        "# 标题候选",
        "",
        "## 击中痛点型",
        "",
    ]
    lines.extend(f"{index}. {title}" for index, title in enumerate(pain_point, 1))
    lines.extend(["", "## 认知差型", ""])
    lines.extend(f"{index}. {title}" for index, title in enumerate(cognitive_gap, 1))
    lines.extend(
        [
            "",
            "## 推荐首选",
            "",
            f"- 首选标题：{recommended['primary']}",
            f"- 备选标题：{recommended['secondary']}",
            f"- 推荐理由：{recommended['reason']}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def generate_titles_for_output(output_dir: Path, *, model: str = "", no_ai: bool = False) -> dict[str, Any]:
    article_path = output_dir / "article.md"
    metadata_path = output_dir / "metadata.json"
    if not article_path.exists():
        raise TitleGenerationError(f"article.md not found: {article_path}")
    if not metadata_path.exists():
        raise TitleGenerationError(f"metadata.json not found: {metadata_path}")

    article = article_path.read_text(encoding="utf-8")
    metadata = load_json(metadata_path)
    payload: dict[str, Any]
    if no_ai or os.environ.get("CONTENT_FACTORY_TITLES_NO_AI") == "1":
        payload = fallback_titles(article, metadata)
    else:
        try:
            payload = run_openrouter_title_generation(build_title_prompt(article, metadata), model)
        except TitleGenerationError:
            payload = fallback_titles(article, metadata)
    titles = normalize_title_payload(payload, article, metadata)
    titles_path = write_titles_markdown(output_dir, titles)
    metadata["titles"] = titles
    metadata["updatedAt"] = utc_now()
    write_json(metadata_path, metadata)
    return {
        "outputDir": str(output_dir),
        "titlesPath": str(titles_path),
        "painPointCount": len(titles["pain_point"]),
        "cognitiveGapCount": len(titles["cognitive_gap"]),
        "primary": titles["recommended"]["primary"],
        "secondary": titles["recommended"]["secondary"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate title candidate groups for ContentFactory outputs.")
    parser.add_argument("output_dirs", nargs="+")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.4-mini"))
    parser.add_argument("--no-ai", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file:
        load_dotenv(Path(args.env_file).expanduser().resolve())
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in args.output_dirs:
        output_dir = Path(item).expanduser().resolve()
        try:
            results.append(generate_titles_for_output(output_dir, model=args.model, no_ai=args.no_ai))
        except Exception as exc:
            failures.append({"outputDir": str(output_dir), "error": str(exc)})
    print(json.dumps({"results": results, "failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
