#!/usr/bin/env python3
"""Mark Feishu post-publish review status for a ContentFactory output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATUSES = {
    "pending_review",
    "ready_for_wechat",
    "needs_edit",
    "rejected",
    "copied_to_wechat",
    "published_to_wechat",
}


class FeishuReviewStatusError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FeishuReviewStatusError(f"metadata.json not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeishuReviewStatusError(f"metadata.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeishuReviewStatusError("metadata.json must contain a JSON object.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def default_result(status: str) -> str:
    mapping = {
        "pending_review": "待人工检查",
        "ready_for_wechat": "可进入公众号人工发布",
        "needs_edit": "需要修改后再发布",
        "rejected": "不建议继续发布",
        "copied_to_wechat": "已复制到公众号后台",
        "published_to_wechat": "已完成公众号发布",
    }
    return mapping[status]


def base_check_markdown(feishu: dict[str, Any], checked_at: str) -> str:
    return "\n".join(
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
            "- 生产信息是否在文末：",
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
            "- 标题 / 段落 / 列表 / 引用是否保留：",
            "  - [ ] 通过",
            "  - [ ] 不通过",
            "  - 备注：",
            "",
            "- 图片是否需要手动处理：",
            "  - [ ] 不需要",
            "  - [ ] 需要",
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
            "- 问题 2：",
            "- 问题 3：",
            "",
            "## 5. 最终结论",
            "",
            "- 是否可作为后续飞书发布模板：",
            "  - [ ] 可以",
            "  - [ ] 暂不可以",
            "",
            "- 结论说明：",
            "",
        ]
    )


def review_block(*, status: str, checked_at: str, result: str, notes: str) -> str:
    return "\n".join(
        [
            "<!-- feishu-review-status:start -->",
            "",
            "## 6. 发布后状态",
            "",
            f"- review.status: `{status}`",
            f"- checkedAt: `{checked_at}`",
            f"- result: {result}",
            f"- notes: {notes}",
            "",
            "<!-- feishu-review-status:end -->",
            "",
        ]
    )


def update_checked_at(text: str, checked_at: str) -> str:
    if re.search(r"^- checkedAt:.*$", text, flags=re.M):
        return re.sub(r"^- checkedAt:.*$", f"- checkedAt: {checked_at}", text, count=1, flags=re.M)
    return text


def sync_feishu_check(output_dir: Path, feishu: dict[str, Any], *, status: str, checked_at: str, result: str, notes: str) -> Path:
    path = output_dir / "feishu-check.md"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = update_checked_at(text, checked_at)
    else:
        text = base_check_markdown(feishu, checked_at)

    block = review_block(status=status, checked_at=checked_at, result=result, notes=notes)
    pattern = r"<!-- feishu-review-status:start -->.*?<!-- feishu-review-status:end -->\n?"
    if re.search(pattern, text, flags=re.S):
        text = re.sub(pattern, block, text, count=1, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + block
    path.write_text(text, encoding="utf-8")
    return path


def mark_review_status(output_dir: Path, *, status: str, result: str, notes: str) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise FeishuReviewStatusError(f"invalid status: {status}")
    output_dir = output_dir.expanduser().resolve()
    metadata_path = output_dir / "metadata.json"
    metadata = load_json(metadata_path)
    feishu = get_feishu(metadata)
    if feishu.get("status") != "published":
        raise FeishuReviewStatusError(f"publish.feishu.status must be published, got: {feishu.get('status', '')}")
    checked_at = utc_now()
    review = {
        "status": status,
        "checkedAt": checked_at,
        "result": result.strip() or default_result(status),
        "notes": notes.strip(),
    }
    feishu["review"] = review
    metadata["updatedAt"] = checked_at
    write_json(metadata_path, metadata)
    check_path = sync_feishu_check(
        output_dir,
        feishu,
        status=status,
        checked_at=checked_at,
        result=review["result"],
        notes=review["notes"],
    )
    return {
        "outputDir": str(output_dir),
        "status": status,
        "checkedAt": checked_at,
        "feishuCheckPath": str(check_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mark Feishu review status for one published output.")
    parser.add_argument("output_dir")
    parser.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    parser.add_argument("--result", default="")
    parser.add_argument("--notes", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = mark_review_status(
            Path(args.output_dir),
            status=args.status,
            result=args.result,
            notes=args.notes,
        )
    except FeishuReviewStatusError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
