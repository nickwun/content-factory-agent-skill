import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "build_feishu_publish_markdown.py"


ARTICLE = """# 跑步距离怎么选

这是正文第一段。

## 01、先跑舒服

正文内容。
"""


class BuildFeishuPublishMarkdownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "2026-05-20-safe-slug"
        self.output.mkdir(parents=True)
        self.write_output(self.output)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_output(self, output: Path, *, quality_status: str = "ready_for_edit", cover: bool = True) -> None:
        output.mkdir(parents=True, exist_ok=True)
        (output / "article.md").write_text(ARTICLE, encoding="utf-8")
        (output / "titles.md").write_text(
            "# 标题候选\n\n## 击中痛点型\n\n1. 痛点一\n2. 痛点二\n3. 痛点三\n4. 痛点四\n5. 痛点五\n\n## 认知差型\n\n1. 认知一\n2. 认知二\n3. 认知三\n4. 认知四\n5. 认知五\n\n## 推荐首选\n\n- 首选标题：痛点一\n- 备选标题：认知一\n- 推荐理由：测试。\n",
            encoding="utf-8",
        )
        (output / "quality-report.md").write_text("# Quality\n", encoding="utf-8")
        if cover:
            image = output / "images" / "cover.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"fake-png")
        metadata = {
            "type": "output_metadata",
            "title": "跑步距离怎么选",
            "slug": "safe-slug",
            "outputDir": str(output),
            "profileId": "profile-ahong-running-rewrite",
            "corpusId": "corpus-ahong-running-style",
            "sourceMaterials": [{"sourceId": "source-test"}],
            "createdAt": "2026-05-20T00:00:00Z",
            "images": {
                "cover": {
                    "status": "generated",
                    "outputPath": "images/cover.png",
                }
            },
            "titles": {
                "pain_point": ["痛点一", "痛点二", "痛点三", "痛点四", "痛点五"],
                "cognitive_gap": ["认知一", "认知二", "认知三", "认知四", "认知五"],
                "recommended": {
                    "primary": "痛点一",
                    "secondary": "认知一",
                    "reason": "测试。",
                },
            },
            "quality": {
                "status": quality_status,
                "score": 100 if quality_status == "ready_for_edit" else 70,
                "issues": [],
                "warnings": [],
            },
        }
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_script(self, output: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), str(output or self.output)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_builds_feishu_publish_markdown_and_updates_metadata(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        publish_md = (self.output / "feishu-publish.md").read_text(encoding="utf-8")
        self.assertIn("![封面图](images/cover.png)", publish_md)
        self.assertIn("## 推荐标题", publish_md)
        self.assertIn("## 标题候选", publish_md)
        self.assertIn("## 正文", publish_md)
        self.assertIn("## 生产信息", publish_md)
        self.assertIn(ARTICLE, publish_md)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["status"], "prepared")
        self.assertEqual(feishu["markdownFile"], "feishu-publish.md")
        self.assertTrue(feishu["preparedAt"])

    def test_missing_article_fails(self) -> None:
        (self.output / "article.md").unlink()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("article.md", result.stderr)
        self.assertFalse((self.output / "feishu-publish.md").exists())

    def test_missing_titles_fails(self) -> None:
        (self.output / "titles.md").unlink()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("titles", result.stderr)

    def test_missing_cover_fails(self) -> None:
        (self.output / "images" / "cover.png").unlink()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cover.png", result.stderr)

    def test_non_ready_quality_fails(self) -> None:
        self.write_output(self.output, quality_status="needs_revision")

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("quality.status", result.stderr)


if __name__ == "__main__":
    unittest.main()
