import json
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "generate_titles.py"


ARTICLE = """# 跑步距离怎么选

很多人一开始跑步，都会急着问自己，到底该跑 5 公里，还是直接冲 10 公里。

## 01、先跑舒服

如果你刚开始恢复训练，先把 5 公里跑得轻松，比硬撑 10 公里更有意义。

## 02、再看恢复

真正适合你的距离，不只看跑步那一刻，也看第二天身体的反馈。

## 03、长期更值

跑步最怕的不是慢，而是一下子把热情跑伤了。
"""


class GenerateTitlesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "2026-05-20-safe-slug"
        self.output.mkdir(parents=True)
        (self.output / "article.md").write_text(ARTICLE, encoding="utf-8")
        (self.output / "brief.md").write_text("# Brief\n", encoding="utf-8")
        (self.output / "cover-prompt.md").write_text("# Cover Prompt\n", encoding="utf-8")
        metadata = {
            "type": "output_metadata",
            "title": "跑步距离怎么选",
            "slug": "safe-slug",
            "outputDir": str(self.output),
            "profileId": "profile-ahong-running-rewrite",
            "corpusId": "corpus-ahong-running-style",
            "images": {"cover": {"status": "generated", "outputPath": "images/cover.png"}},
        }
        (self.output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def load_script_module(self):
        spec = importlib.util.spec_from_file_location("generate_titles_under_test", SCRIPT)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        return module

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.output), "--no-ai"],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_generates_titles_markdown_and_metadata(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        titles_md = (self.output / "titles.md").read_text(encoding="utf-8")
        self.assertIn("## 击中痛点型", titles_md)
        self.assertIn("## 认知差型", titles_md)
        self.assertIn("## 推荐首选", titles_md)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(len(metadata["titles"]["pain_point"]), 5)
        self.assertEqual(len(metadata["titles"]["cognitive_gap"]), 5)
        self.assertTrue(metadata["titles"]["recommended"]["primary"])
        self.assertTrue(metadata["titles"]["recommended"]["secondary"])

    def test_does_not_modify_existing_article_or_prompt_files(self) -> None:
        before = {
            name: (self.output / name).read_text(encoding="utf-8")
            for name in ["article.md", "brief.md", "cover-prompt.md"]
        }

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        after = {
            name: (self.output / name).read_text(encoding="utf-8")
            for name in ["article.md", "brief.md", "cover-prompt.md"]
        }
        self.assertEqual(before, after)

    def test_prompt_contains_reference_title_formula_library(self) -> None:
        module = self.load_script_module()
        prompt = module.build_title_prompt(ARTICLE, {"title": "跑步距离怎么选"})

        self.assertIn("具体对象 + 读者身份 + 冲突问题 + 结果/代价", prompt)
        self.assertIn("标准测试型", prompt)
        self.assertIn("比较选择型", prompt)
        self.assertIn("长期结果型", prompt)
        self.assertIn("降低恐吓", prompt)

    def test_fallback_titles_use_reference_title_patterns(self) -> None:
        module = self.load_script_module()
        payload = module.fallback_titles(ARTICLE, {"title": "跑步距离怎么选"})
        titles = payload["pain_point"] + payload["cognitive_gap"]

        self.assertTrue(any("普通跑者" in title for title in titles))
        self.assertTrue(any("5公里" in title and "10公里" in title for title in titles))
        self.assertTrue(any("为什么" in title or "到底" in title for title in titles))
        self.assertFalse(any("你以为自己跑得不够" in title for title in titles))


if __name__ == "__main__":
    unittest.main()
