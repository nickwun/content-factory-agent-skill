import hashlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "generate_cover_image.py"


FAKE_GENERATOR = """#!/usr/bin/env python3
import base64
import json
import sys
from pathlib import Path

args = sys.argv[1:]
image = Path(args[args.index("--image") + 1])
prompt = Path(args[args.index("--promptfiles") + 1])
if "FAIL_GENERATION" in prompt.read_text(encoding="utf-8"):
    print("fake generation failed", file=sys.stderr)
    raise SystemExit(7)
image.parent.mkdir(parents=True, exist_ok=True)
png = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lx4n1wAAAABJRU5ErkJggg=="
)
image.write_bytes(png)
print(json.dumps({"savedImage": str(image), "provider": "fake-provider", "model": "fake-model"}))
"""


class GenerateCoverImageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "04-Outputs" / "case"
        self.output.mkdir(parents=True)
        self.script = Path(self.tmp.name) / "fake_generator.py"
        self.script.write_text(FAKE_GENERATOR, encoding="utf-8")
        self.script.chmod(0o755)
        self.write_base_files()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_base_files(self) -> None:
        (self.output / "article.md").write_text("# 文章\n\n正文", encoding="utf-8")
        (self.output / "brief.md").write_text("# Brief\n\n说明", encoding="utf-8")
        (self.output / "cover-prompt.md").write_text("# Cover Prompt\n\n## Output Ratio\n\n4:3", encoding="utf-8")
        (self.output / "metadata.json").write_text(
            json.dumps(
                {
                    "type": "output_metadata",
                    "outputId": "output-case",
                    "title": "测试文章",
                    "images": {
                        "cover": {
                            "status": "prompt_ready",
                            "promptFile": "cover-prompt.md",
                            "outputPath": "images/cover.png",
                            "ratio": "4:3",
                        }
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def make_output(self, name: str, *, prompt: bool = True, generated: bool = False, prompt_text: str = "# Cover Prompt\n\n## Output Ratio\n\n4:3") -> Path:
        output = self.output.parent / name
        output.mkdir(parents=True)
        (output / "article.md").write_text(f"# {name}\n\n正文", encoding="utf-8")
        (output / "brief.md").write_text("# Brief\n\n说明", encoding="utf-8")
        if prompt:
            (output / "cover-prompt.md").write_text(prompt_text, encoding="utf-8")
        metadata = {
            "type": "output_metadata",
            "outputId": f"output-{name}",
            "title": name,
            "images": {
                "cover": {
                    "status": "generated" if generated else "prompt_ready",
                    "promptFile": "cover-prompt.md",
                    "outputPath": "images/cover.png",
                    "ratio": "4:3",
                }
            },
        }
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        if generated:
            image = output / "images" / "cover.png"
            image.parent.mkdir()
            image.write_bytes(b"existing")
        return output

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CONTENT_FACTORY_COVER_GENERATOR"] = str(self.script)
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.output), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def read_cover_meta(self) -> dict:
        return json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]

    def digest(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_missing_cover_prompt_fails_and_writes_failed_metadata(self) -> None:
        (self.output / "cover-prompt.md").unlink()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        cover = self.read_cover_meta()
        self.assertEqual(cover["status"], "failed")
        self.assertIn("cover-prompt.md", cover["error"])

    def test_existing_cover_without_force_refuses_to_overwrite(self) -> None:
        image = self.output / "images" / "cover.png"
        image.parent.mkdir()
        image.write_bytes(b"existing")

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(image.read_bytes(), b"existing")
        cover = self.read_cover_meta()
        self.assertEqual(cover["status"], "failed")
        self.assertIn("--force", cover["error"])

    def test_success_marks_codex_generation_required_without_external_image(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        image = self.output / "images" / "cover.png"
        self.assertFalse(image.exists())
        cover = self.read_cover_meta()
        self.assertEqual(cover["status"], "prompt_ready")
        self.assertEqual(cover["promptFile"], "cover-prompt.md")
        self.assertEqual(cover["outputPath"], "images/cover.png")
        self.assertEqual(cover["ratio"], "4:3")
        self.assertEqual(cover["provider"], "codex-imagegen")
        self.assertEqual(cover["model"], "codex-direct-image-generation")
        self.assertEqual(cover["generationMode"], "codex_tool_required")

    def test_does_not_modify_article_brief_or_cover_prompt(self) -> None:
        tracked = [self.output / "article.md", self.output / "brief.md", self.output / "cover-prompt.md"]
        before = {path: self.digest(path) for path in tracked}

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        after = {path: self.digest(path) for path in tracked}
        self.assertEqual(before, after)

    def test_force_allows_requeueing_existing_cover_for_codex_generation(self) -> None:
        image = self.output / "images" / "cover.png"
        image.parent.mkdir()
        image.write_bytes(b"existing")

        result = self.run_script("--force")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image.read_bytes(), b"existing")
        cover = self.read_cover_meta()
        self.assertEqual(cover["status"], "prompt_ready")
        self.assertEqual(cover["generationMode"], "codex_tool_required")

    def test_batch_processes_success_skip_and_failure_without_interrupting(self) -> None:
        success = self.make_output("success")
        skipped = self.make_output("skipped", generated=True)
        failed = self.make_output("failed", prompt=False)

        result = self.run_script(str(success), str(skipped), str(failed))

        self.assertEqual(result.returncode, 0, result.stderr)
        success_cover = json.loads((success / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]
        skipped_cover = json.loads((skipped / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]
        failed_cover = json.loads((failed / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]
        self.assertEqual(success_cover["status"], "prompt_ready")
        self.assertEqual(success_cover["generationMode"], "codex_tool_required")
        self.assertEqual(skipped_cover["status"], "generated")
        self.assertEqual((skipped / "images" / "cover.png").read_bytes(), b"existing")
        self.assertEqual(failed_cover["status"], "failed")
        self.assertIn("cover-prompt.md", failed_cover["error"])
        summaries = sorted((self.output.parent / "batch-runs").glob("*-cover-batch-*.md"))
        self.assertEqual(len(summaries), 1)
        summary = summaries[0].read_text(encoding="utf-8")
        self.assertIn("success | manual_required", summary)
        self.assertIn("skipped | skipped", summary)
        self.assertIn("failed | failed", summary)

    def test_scan_mode_selects_uncovered_outputs_and_respects_limit(self) -> None:
        (self.output / "images").mkdir()
        (self.output / "images" / "cover.png").write_bytes(b"existing")
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        metadata["images"]["cover"]["status"] = "generated"
        (self.output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        first = self.make_output("scan-first")
        second = self.make_output("scan-second")
        generated = self.make_output("scan-generated", generated=True)

        result = self.run_script("--scan", str(self.output.parent), "--limit", "1")

        self.assertEqual(result.returncode, 0, result.stderr)
        first_status = json.loads((first / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]["status"]
        second_status = json.loads((second / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]["status"]
        generated_status = json.loads((generated / "metadata.json").read_text(encoding="utf-8"))["images"]["cover"]["status"]
        self.assertEqual(first_status, "prompt_ready")
        self.assertEqual(second_status, "prompt_ready")
        self.assertEqual(generated_status, "generated")
        summaries = sorted((self.output.parent / "batch-runs").glob("*-cover-batch-*.md"))
        self.assertEqual(len(summaries), 1)
        summary = summaries[0].read_text(encoding="utf-8")
        self.assertIn("scan-first | manual_required", summary)
        self.assertNotIn("scan-second", summary)


if __name__ == "__main__":
    unittest.main()
