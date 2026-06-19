#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from source_registry import (
    ensure_record_for_file,
    file_hash,
    upsert_markdown_frontmatter,
    update_record,
)

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


HEADER_FOOTER_PATTERNS = [
    re.compile(r"^页眉[:：]?", re.I),
    re.compile(r"^页脚[:：]?", re.I),
    re.compile(r"^第\s*\d+\s*页$"),
    re.compile(r"^\d+\s*/\s*\d+$"),
    re.compile(r"^-?\s*\d+\s*-$"),
    re.compile(r"^原创\s+.+"),
    re.compile(r"^\d{4}年\d{2}月\d{2}日\s+\d{2}:\d{2}"),
    re.compile(r"^点击.*关注$"),
    re.compile(r"^本文来自.+"),
]

WECHAT_FOOTER_START_PATTERNS = [
    re.compile(r"^●"),
    re.compile(r"^预览时标签不可点$"),
    re.compile(r"^阅读$"),
    re.compile(r"^微信扫一扫"),
    re.compile(r"^知道了$"),
    re.compile(r"^取消\s+允许$"),
    re.compile(r"^分析$"),
    re.compile(r"^视频\s+小程序"),
]


@dataclass
class ExtractedBlock:
    kind: str
    text: str


@dataclass
class NormalizeResult:
    title: str
    markdown: str
    found_images: bool
    found_tables: bool
    cleaned_author_lines: bool
    cleaned_leading_cover_image: bool
    cleaned_wechat_footer: bool
    review_notes: list[str]


def slugify(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", value.strip())
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned.strip("-") or "docx-material"


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_noise_line(text: str) -> bool:
    normalized = normalize_whitespace(text)
    if not normalized:
        return True
    return any(pattern.search(normalized) for pattern in HEADER_FOOTER_PATTERNS)


def is_footer_start(text: str) -> bool:
    normalized = normalize_whitespace(text)
    return any(pattern.search(normalized) for pattern in WECHAT_FOOTER_START_PATTERNS)


def is_author_or_source_line(text: str) -> bool:
    normalized = normalize_whitespace(text)
    if not normalized:
        return False
    if normalized in {"跑步老王", "跑步指南"}:
        return True
    if len(normalized) <= 16 and any(token in normalized for token in ["作者", "编辑", "来源", "公众号"]):
        return True
    return False


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def paragraph_style(paragraph: ET.Element) -> str:
    style = paragraph.find("./w:pPr/w:pStyle", NS)
    return style.attrib.get(f"{{{NS['w']}}}val", "") if style is not None else ""


def paragraph_is_list(paragraph: ET.Element) -> bool:
    return paragraph.find("./w:pPr/w:numPr", NS) is not None


def paragraph_has_image(paragraph: ET.Element) -> bool:
    return paragraph.find(".//w:drawing", NS) is not None or paragraph.find(".//a:blip", NS) is not None


def paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for text in paragraph.findall(".//w:t", NS):
        if text.text:
            parts.append(text.text)
    return normalize_whitespace("".join(parts))


def map_paragraph_to_block(paragraph: ET.Element) -> ExtractedBlock | None:
    has_image = paragraph_has_image(paragraph)
    text = paragraph_text(paragraph)
    if has_image and not text:
        return ExtractedBlock("image", "[图片占位：原文此处有图片]")
    if is_noise_line(text):
        return ExtractedBlock("image", "[图片占位：原文此处有图片]") if has_image else None

    style = paragraph_style(paragraph).lower()
    if style in {"title", "标题"}:
        return ExtractedBlock("title", text)
    if "heading1" in style or style in {"heading 1", "标题 1", "标题1"}:
        return ExtractedBlock("heading1", text)
    if "heading2" in style or style in {"heading 2", "标题 2", "标题2"}:
        return ExtractedBlock("heading2", text)
    if "heading3" in style or style in {"heading 3", "标题 3", "标题3"}:
        return ExtractedBlock("heading3", text)
    if "quote" in style or "引用" in style:
        return ExtractedBlock("quote", text)
    if paragraph_is_list(paragraph):
        return ExtractedBlock("list", text)
    return ExtractedBlock("paragraph", text)


def iter_body_blocks(root: ET.Element) -> tuple[list[ExtractedBlock], dict[str, bool]]:
    body = root.find("./w:body", NS)
    if body is None:
        return [], {
            "cleaned_author_lines": False,
            "cleaned_leading_cover_image": False,
            "cleaned_wechat_footer": False,
        }
    blocks: list[ExtractedBlock] = []
    cleaned_author_lines = False
    cleaned_wechat_footer = False
    for child in body:
        name = local_name(child.tag)
        if name == "p":
            block = map_paragraph_to_block(child)
            if block:
                if is_footer_start(block.text):
                    cleaned_wechat_footer = True
                    break
                if block.kind == "paragraph" and len(blocks) <= 2 and is_author_or_source_line(block.text):
                    cleaned_author_lines = True
                    continue
                blocks.append(block)
        elif name == "tbl":
            blocks.append(ExtractedBlock("table", "[表格占位：原文此处有表格，请人工复核]"))
    cleaned_leading_cover_image = remove_leading_cover_image(blocks)
    return blocks, {
        "cleaned_author_lines": cleaned_author_lines,
        "cleaned_leading_cover_image": cleaned_leading_cover_image,
        "cleaned_wechat_footer": cleaned_wechat_footer,
    }


def remove_leading_cover_image(blocks: list[ExtractedBlock]) -> bool:
    first_body_text_seen = False
    for index, block in enumerate(blocks):
        if block.kind in {"title", "heading1", "heading2", "heading3"}:
            continue
        if block.kind == "image" and not first_body_text_seen:
            del blocks[index]
            return True
        if block.kind not in {"image", "table"}:
            first_body_text_seen = True
    return False


def blocks_to_markdown(blocks: list[ExtractedBlock]) -> tuple[str, str]:
    title = next((block.text for block in blocks if block.kind == "title"), "")
    if not title:
        title = next((block.text for block in blocks if block.kind.startswith("heading")), "")
    if not title:
        title = "未命名素材"

    lines: list[str] = []
    title_written = False
    previous = ""

    for block in blocks:
        if block.kind == "title":
            if not title_written:
                lines.extend([f"# {block.text}", ""])
                title_written = True
            continue
        if block.kind == "heading1":
            lines.extend([f"## {block.text}", ""])
        elif block.kind == "heading2":
            lines.extend([f"### {block.text}", ""])
        elif block.kind == "heading3":
            lines.extend([f"#### {block.text}", ""])
        elif block.kind == "list":
            lines.extend([f"- {block.text}", ""])
        elif block.kind == "quote":
            lines.extend([f"> {block.text}", ""])
        elif block.kind == "image":
            if previous != block.text:
                lines.extend([block.text, ""])
        elif block.kind == "table":
            lines.extend([block.text, ""])
        else:
            lines.extend([block.text, ""])
        previous = block.text

    if not title_written:
        lines = [f"# {title}", "", *lines]

    markdown = "\n".join(lines).strip() + "\n"
    return title, markdown


def normalize_docx(path: Path) -> NormalizeResult:
    with zipfile.ZipFile(path) as archive:
        try:
            document_xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("DOCX 中缺少 word/document.xml，无法提取正文。") from exc

    root = ET.fromstring(document_xml)
    blocks, cleanup_flags = iter_body_blocks(root)
    found_images = any(block.kind == "image" for block in blocks)
    found_tables = any(block.kind == "table" for block in blocks)
    title, markdown = blocks_to_markdown(blocks)

    review_notes: list[str] = []
    if found_images:
        review_notes.append("发现图片，已保留图片占位，请人工确认是否需要补图。")
    if found_tables:
        review_notes.append("发现表格，已保留表格占位，请人工复核表格内容。")
    if len(markdown.strip()) < 80:
        review_notes.append("提取正文较短，请人工确认 Word 内容是否完整。")

    return NormalizeResult(
        title=title,
        markdown=markdown,
        found_images=found_images,
        found_tables=found_tables,
        cleaned_author_lines=cleanup_flags["cleaned_author_lines"],
        cleaned_leading_cover_image=cleanup_flags["cleaned_leading_cover_image"],
        cleaned_wechat_footer=cleanup_flags["cleaned_wechat_footer"],
        review_notes=review_notes,
    )


def build_cleaning_note(
    source: Path,
    markdown_path: Path,
    result: NormalizeResult,
    source_id: str = "",
    content_hash: str = "",
) -> str:
    review = "；".join(result.review_notes) if result.review_notes else "无明显风险。"
    can_rewrite = "否" if any("较短" in note for note in result.review_notes) else "是"
    return "\n".join(
        [
            "---",
            "type: cleaning_note",
            *( [f"sourceId: {source_id}"] if source_id else [] ),
            f"source_path: {source}",
            f"markdown_path: {markdown_path}",
            *( [f"contentHash: {content_hash}"] if content_hash else [] ),
            f"title: {result.title}",
            "---",
            "",
            f"# Cleaning Note: {result.title}",
            "",
            f"- 原始文件路径：`{source}`",
            f"- 输出 Markdown 路径：`{markdown_path}`",
            f"- 提取到的标题：{result.title}",
            *( [f"- sourceId：`{source_id}`"] if source_id else [] ),
            *( [f"- contentHash：`{content_hash}`"] if content_hash else [] ),
            f"- 是否发现图片：{'是' if result.found_images else '否'}",
            f"- 是否发现表格：{'是' if result.found_tables else '否'}",
            f"- 是否清理作者行：{'是' if result.cleaned_author_lines else '否'}",
            f"- 是否清理首图占位：{'是' if result.cleaned_leading_cover_image else '否'}",
            f"- 是否清理公众号尾部污染：{'是' if result.cleaned_wechat_footer else '否'}",
            f"- 是否存在需要人工复核的地方：{review}",
            f"- 是否可以进入仿写流程：{can_rewrite}",
            "",
        ]
    )


def normalize_one(vault: Path, source: Path) -> dict[str, str]:
    output_dir = vault / "01-Materials" / "rewrite-sources"
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(source.stem)
    markdown_path = output_dir / f"{slug}.md"
    note_path = output_dir / f"{slug}.cleaning-note.md"

    record = ensure_record_for_file(vault, source)
    source_id = str(record["sourceId"])
    content_hash = file_hash(source)
    result = normalize_docx(source)
    markdown_path.write_text(
        upsert_markdown_frontmatter(
            result.markdown,
            {
                "sourceId": source_id,
                "sourceStatus": "normalized",
                "sourceFile": source.name,
                "contentHash": content_hash,
                "usedAt": "",
                "usedByOutput": "",
            },
        ),
        encoding="utf-8",
    )
    note_path.write_text(
        build_cleaning_note(source, markdown_path, result, source_id, content_hash),
        encoding="utf-8",
    )
    next_updates = {
        "normalizedPath": str(markdown_path.resolve()),
        "contentHash": content_hash,
        "title": result.title,
    }
    if str(record.get("status") or "unused") not in {"used", "processing"}:
        next_updates["status"] = "normalized"
    update_record(
        vault,
        source_id,
        next_updates,
    )
    return {
        "sourceId": source_id,
        "source_path": str(source),
        "markdown_path": str(markdown_path),
        "cleaning_note_path": str(note_path),
        "title": result.title,
    }


def find_docx_files(vault: Path) -> list[Path]:
    raw_dir = vault / "01-Materials" / "docx-raw"
    return sorted(path for path in raw_dir.glob("*.docx") if not path.name.startswith("~$"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize ContentFactoryVault Word .docx material into clean Markdown.",
    )
    parser.add_argument("--vault", default="/Users/hui/Documents/ContentFactoryVault")
    parser.add_argument("--file", default="")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    sources = [Path(args.file).expanduser().resolve()] if args.file else find_docx_files(vault)
    if not sources:
        print("No .docx files found in 01-Materials/docx-raw.", file=sys.stderr)
        return 1

    results = [normalize_one(vault, source) for source in sources]
    print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
