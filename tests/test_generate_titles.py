import json
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "generate_titles.py"


ARTICLE = """# 跑步不发朋友圈以后，我反而轻松了

跑步这件事，有时候不是给别人看的。

## 01、刚开始想被看见

刚开始跑步的人，很需要一点外界回应。

## 02、后来更想安静

当跑步进入生活，你会慢慢不再需要每一次都被确认。

## 03、不晒也是热爱

不发朋友圈，不代表不热爱，也可能是终于把跑步还给了自己。
"""


VALID_TITLES = {
    "pain_point": [
        "跑步不发朋友圈以后，我反而轻松了",
        "跑完步不想再晒了，是不是热情变少了？",
        "为什么越认真跑步，越不想把每一次都发出去？",
        "当跑步不再等点赞，你才真正把它还给自己",
        "不发跑步动态的人，可能不是放弃了，而是跑得更稳了",
    ],
    "cognitive_gap": [
        "很多人以为晒跑步才算坚持，其实安静跑下去更难得",
        "跑步最好的变化，不一定出现在朋友圈里",
        "从想被看见到不必证明，是普通跑者成熟的一步",
        "不晒不是冷淡，而是跑步已经长进生活里",
        "比起让别人知道你自律，更重要的是知道自己为什么跑",
    ],
    "recommended": {
        "primary": "跑步不发朋友圈以后，我反而轻松了",
        "secondary": "不发朋友圈以后，我才把跑步还给了自己",
        "reason": "首选标题贴合文章里从外部反馈转向自我感受的变化，语气克制。",
    },
}


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
            "title": "跑步不发朋友圈以后，我反而轻松了",
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

    def write_valid_titles(self) -> None:
        lines = [
            "# 标题候选",
            "",
            "## 击中痛点型",
            "",
            *[f"{index}. {title}" for index, title in enumerate(VALID_TITLES["pain_point"], 1)],
            "",
            "## 认知差型",
            "",
            *[f"{index}. {title}" for index, title in enumerate(VALID_TITLES["cognitive_gap"], 1)],
            "",
            "## 推荐首选",
            "",
            f"- 首选标题：{VALID_TITLES['recommended']['primary']}",
            f"- 备选标题：{VALID_TITLES['recommended']['secondary']}",
            f"- 推荐理由：{VALID_TITLES['recommended']['reason']}",
            "",
        ]
        (self.output / "titles.md").write_text("\n".join(lines), encoding="utf-8")
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        metadata["titles"] = VALID_TITLES
        (self.output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_script(self, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.output), *extra],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_missing_titles_reports_codex_title_required_without_writing_fallback(self) -> None:
        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["failures"][0]["status"], "codex_title_required")
        self.assertIn("codex_title_required", payload["failures"][0]["error"])
        self.assertFalse((self.output / "titles.md").exists())
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        self.assertNotIn("titles", metadata)

    def test_existing_valid_titles_pass_validation_without_modifying_files(self) -> None:
        self.write_valid_titles()
        before = {
            name: (self.output / name).read_text(encoding="utf-8")
            for name in ["article.md", "brief.md", "cover-prompt.md", "titles.md", "metadata.json"]
        }

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["results"][0]["status"], "ready")
        self.assertEqual(payload["results"][0]["painPointCount"], 5)
        self.assertEqual(payload["results"][0]["cognitiveGapCount"], 5)
        after = {
            name: (self.output / name).read_text(encoding="utf-8")
            for name in ["article.md", "brief.md", "cover-prompt.md", "titles.md", "metadata.json"]
        }
        self.assertEqual(before, after)

    def test_openrouter_key_does_not_trigger_title_api_call(self) -> None:
        self.write_valid_titles()
        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = "test-key-that-must-not-be-used"
        env["OPENROUTER_BASE_URL"] = "http://127.0.0.1:9"

        result = self.run_script(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("OpenRouter", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["results"][0]["status"], "ready")

    def test_no_ai_flag_no_longer_generates_fallback_titles(self) -> None:
        result = self.run_script("--no-ai")

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["failures"][0]["status"], "codex_title_required")
        self.assertFalse((self.output / "titles.md").exists())

    def test_prompt_builder_exposes_codex_title_task(self) -> None:
        module = self.load_script_module()
        task = module.build_codex_title_task(self.output, ARTICLE, {"title": "跑步不发朋友圈以后，我反而轻松了"})

        self.assertIn("codex_title_required", task)
        self.assertIn("titles.md", task)
        self.assertIn("metadata.titles", task)
        self.assertIn("跑步不发朋友圈", task)


if __name__ == "__main__":
    unittest.main()
