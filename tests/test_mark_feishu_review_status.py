import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "mark_feishu_review_status.py"


class MarkFeishuReviewStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "2026-05-20-output"
        self.output.mkdir(parents=True)
        self.write_metadata()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_metadata(self, status: str = "published") -> None:
        metadata = {
            "title": "测试文章",
            "publish": {
                "feishu": {
                    "status": status,
                    "documentId": "DOC123",
                    "documentUrl": "https://feishu.cn/docx/DOC123",
                    "backend": "feishu-cli",
                }
            },
        }
        (self.output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_script(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.output), *extra],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_marks_review_status_and_creates_feishu_check(self) -> None:
        result = self.run_script("--status", "ready_for_wechat", "--notes", "手机预览通过")

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        review = metadata["publish"]["feishu"]["review"]
        self.assertEqual(review["status"], "ready_for_wechat")
        self.assertEqual(review["notes"], "手机预览通过")
        self.assertTrue(review["checkedAt"])
        check = (self.output / "feishu-check.md").read_text(encoding="utf-8")
        self.assertIn("review.status: `ready_for_wechat`", check)
        self.assertIn("手机预览通过", check)
        self.assertIn("https://feishu.cn/docx/DOC123", check)

    def test_updates_existing_feishu_check_block(self) -> None:
        (self.output / "feishu-check.md").write_text(
            "# 飞书文档人工检查记录\n\n- checkedAt:\n\n"
            "<!-- feishu-review-status:start -->\nold\n<!-- feishu-review-status:end -->\n",
            encoding="utf-8",
        )

        result = self.run_script("--status", "needs_edit", "--result", "需要改标题", "--notes", "标题不够准")

        self.assertEqual(result.returncode, 0, result.stderr)
        check = (self.output / "feishu-check.md").read_text(encoding="utf-8")
        self.assertIn("review.status: `needs_edit`", check)
        self.assertIn("需要改标题", check)
        self.assertIn("标题不够准", check)
        self.assertNotIn("\nold\n", check)

    def test_refuses_unpublished_output(self) -> None:
        self.write_metadata(status="prepared")

        result = self.run_script("--status", "pending_review")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be published", result.stderr)


if __name__ == "__main__":
    unittest.main()
