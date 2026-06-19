import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
import sys


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "run_single_article_pipeline.py"
SCAN_SCRIPT = SKILL_DIR / "scripts" / "scan_docx_materials.py"
sys.path.insert(0, str(SKILL_DIR / "scripts"))
from run_single_article_pipeline import build_generation_prompt, fallback_cover_prompt  # noqa: E402


FAKE_ARTICLE_GENERATOR = """#!/usr/bin/env python3
import argparse
import json
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--prompt-file", required=True)
parser.add_argument("--output-dir", required=True)
args = parser.parse_args()

prompt = open(args.prompt_file, encoding="utf-8").read()
if "FAIL_ARTICLE" in prompt:
    print("fake article failed", file=sys.stderr)
    raise SystemExit(9)

cover_body = "FAIL_GENERATION" if "FAIL_COVER" in prompt else "中国中年跑者在清晨公园跑道上慢跑，4:3，禁止文字和 logo。"
article = \"\"\"# 跑步距离怎么选

很多人一开始跑步，都会急着问自己，到底该跑 5 公里，还是直接冲 10 公里。

其实，距离不是面子，身体能不能稳稳接住，才是更重要的事。

## 01、先跑舒服

如果你刚开始恢复训练，先把 5 公里跑得轻松，比硬撑 10 公里更有意义。

你能跑完，还能正常吃饭、睡觉、上班，这才说明训练正在帮你。

很多时候，我们不是跑得不够努力，而是太急着证明自己。把呼吸跑顺，把腿脚跑热，把第二天的精神状态也照顾好，这才是普通人最值得珍惜的进步。

距离可以慢慢加，但身体的信任不能丢。你越懂得留一点余地，越容易把跑步留在生活里。

对普通人来说，跑步不是一次考试，而是一种和身体重新相处的方式。你不用急着证明自己能跑多远，先证明自己能跑得舒服、跑得安全、跑完还愿意继续。

当你把这个底子打稳，5 公里和 10 公里就不再是压力，而会变成一种日常节奏。

很多中年跑者最容易忽略的，就是生活本身也在消耗体力。工作、睡眠、饭局、情绪，都会影响你当天能跑到哪里。

所以别只问自己能不能跑更远，也要问自己跑完以后，还能不能保持平静和轻松。

## 02、再看恢复

真正适合你的距离，不只看跑步那一刻，也看第二天身体的反馈。

如果膝盖沉、心率飘、整个人发空，那就说明身体还没准备好。

一个成熟的跑者，不会只盯着手表上的数字。睡眠、食欲、工作状态、情绪稳定，这些看似琐碎的反馈，其实都在告诉你训练有没有过量。

能长期跑下去的人，往往不是最狠的人，而是最会听身体说话的人。该加量时往前走，该恢复时停一停，这不是退步，是给下一次出发攒力气。

如果你总是跑完就累到不想动，说明这个距离暂时还不是朋友，而是负担。把距离降一点，把心率稳一点，把恢复做好一点，训练反而会更扎实。

跑步这件事，最怕只看当天的痛快，不看后面的代价。真正聪明的安排，是让今天的训练帮到明天的自己。

你可以给自己一个很简单的标准：跑完以后，身体微微疲惫，但心里是舒展的；第二天醒来，腿脚有感觉，但不影响生活。

如果能做到这一点，说明这个距离正在变成你的能力，而不是你的负担。

## 03、长期更值

跑步最怕的不是慢，而是一下子把热情跑伤了。

先稳住节奏，再慢慢加量，普通人也能把跑步变成一件长久的好事。

你可以把目标放远一点，但脚下要落得稳一点。今天少跑两公里，不代表你输了；今天没有逞强，反而可能让你一年后还在路上。

所以，别急着用距离定义自己。适合你的跑步，应该让你更健康、更轻松，也更愿意明天继续出门。

跑起来已经很好，稳稳跑下去，才是真正的本事。

等你有一天发现，跑步不再只是打卡，而是让生活变得更有秩序，你就会明白，距离只是表面，真正改变你的，是那份越来越稳定的自我照顾。

慢慢来，不丢人。普通人的跑步，不需要惊天动地，只需要一次次安全地出门，再一次次带着好状态回家。
\"\"\"
payload = {
    "title": "跑步距离怎么选",
    "article": article,
    "brief": "# Brief\\n\\n- 基于原始素材重写。\\n- 遵守阿洪风格和短段落结构。",
    "coverPrompt": "# Cover Prompt\\n\\n## 文章标题\\n\\n跑步距离怎么选\\n\\n## 视觉主体\\n\\n" + cover_body,
}
print(json.dumps(payload, ensure_ascii=False))
"""


FAKE_COVER_GENERATOR = """#!/usr/bin/env python3
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


def write_docx(path: Path, title: str = "跑步距离怎么选", marker: str = "") -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:p><w:r><w:t>{marker}很多跑者不知道如何选择 5 公里和 10 公里。</w:t></w:r></w:p>
    <w:p><w:r><w:t>素材强调要根据基础、恢复和长期目标来安排距离。</w:t></w:r></w:p>
    <w:p><w:r><w:t>如果刚开始恢复训练，先把较短距离跑得舒服，比盲目追求更长距离更安全。</w:t></w:r></w:p>
    <w:p><w:r><w:t>如果已经有稳定跑量，第二天也没有明显疲劳，就可以循序渐进增加距离。</w:t></w:r></w:p>
    <w:p><w:r><w:t>普通跑者最重要的是长期坚持，不要因为一次逞强影响后续训练。</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""")
        archive.writestr("word/document.xml", document_xml)


class RunSingleArticlePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "ContentFactoryVault"
        self.raw_dir = self.vault / "01-Materials" / "docx-raw"
        self.profile = self.vault / "02-Profiles" / "ahong-running-rewrite.md"
        self.corpus = self.vault / "03-Corpus" / "ahong-running-style.md"
        self.raw_dir.mkdir(parents=True)
        self.profile.parent.mkdir(parents=True)
        self.corpus.parent.mkdir(parents=True)
        self.profile.write_text(
            textwrap.dedent(
                """\
                ---
                type: profile
                profile_id: profile-ahong-running-rewrite
                name: 阿洪风格跑步公众号仿写
                processing_modes:
                  - rewrite
                platform: wechat_article
                min_chars: 1100
                max_chars: 1300
                ---

                # Profile

                忠于素材，不要出现素材里提到。
                """
            ),
            encoding="utf-8",
        )
        self.corpus.write_text(
            textwrap.dedent(
                """\
                ---
                type: corpus
                corpus_id: corpus-ahong-running-style
                name: 阿洪跑步公众号风格语料库
                ---

                # Corpus

                短句、留白、01/02/03 模块。
                """
            ),
            encoding="utf-8",
        )
        self.article_generator = Path(self.tmp.name) / "fake_article_generator.py"
        self.cover_generator = Path(self.tmp.name) / "fake_cover_generator.py"
        self.article_generator.write_text(FAKE_ARTICLE_GENERATOR, encoding="utf-8")
        self.cover_generator.write_text(FAKE_COVER_GENERATOR, encoding="utf-8")
        self.article_generator.chmod(0o755)
        self.cover_generator.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CONTENT_FACTORY_ARTICLE_GENERATOR"] = str(self.article_generator)
        env["CONTENT_FACTORY_COVER_GENERATOR"] = str(self.cover_generator)
        env["CONTENT_FACTORY_TITLES_NO_AI"] = "1"
        return env

    def register_source(self, *, marker: str = "", title: str = "跑步距离怎么选") -> str:
        source = self.raw_dir / f"{title}.docx"
        write_docx(source, title=title, marker=marker)
        result = subprocess.run(
            ["python3", str(SCAN_SCRIPT), "--vault", str(self.vault)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads((self.vault / "01-Materials" / "source-registry.json").read_text(encoding="utf-8"))
        return registry[0]["sourceId"]

    def run_pipeline(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--vault",
                str(self.vault),
                "--profile",
                str(self.profile),
                "--corpus",
                str(self.corpus),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=self.env(),
        )

    def registry_record(self, source_id: str) -> dict:
        registry = json.loads((self.vault / "01-Materials" / "source-registry.json").read_text(encoding="utf-8"))
        return next(item for item in registry if item["sourceId"] == source_id)

    def test_successful_pipeline_normalizes_prepares_cover_prompt_and_marks_source_used(self) -> None:
        source_id = self.register_source()

        result = self.run_pipeline("--source-id", source_id)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        output_dir = Path(payload["outputDir"])
        self.assertTrue((output_dir / "article.md").exists())
        self.assertTrue((output_dir / "brief.md").exists())
        self.assertTrue((output_dir / "metadata.json").exists())
        self.assertTrue((output_dir / "titles.md").exists())
        self.assertTrue((output_dir / "cover-prompt.md").exists())
        self.assertFalse((output_dir / "images" / "cover.png").exists())
        self.assertTrue((output_dir / "pipeline-summary.md").exists())
        metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["images"]["cover"]["status"], "prompt_ready")
        self.assertEqual(metadata["images"]["cover"]["generationMode"], "codex_tool_required")
        self.assertEqual(len(metadata["titles"]["pain_point"]), 5)
        self.assertEqual(len(metadata["titles"]["cognitive_gap"]), 5)
        self.assertRegex(output_dir.name, r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
        self.assertEqual(metadata["slug"], output_dir.name.removeprefix(output_dir.name[:11]))
        self.assertEqual(metadata["outputDir"], str(output_dir))
        self.assertEqual(metadata["title"], "跑步距离怎么选")
        self.assertFalse(re.search(r"[\u4e00-\u9fff？，,\s]", output_dir.name))
        record = self.registry_record(source_id)
        self.assertEqual(record["status"], "used")
        self.assertEqual(record["usedByOutput"], str(output_dir))
        source_markdown = Path(record["normalizedPath"]).read_text(encoding="utf-8")
        self.assertIn("sourceStatus: used", source_markdown)
        self.assertIn(f"usedByOutput: {output_dir}", source_markdown)

    def test_article_success_with_codex_cover_pending_still_marks_source_used(self) -> None:
        source_id = self.register_source(marker="FAIL_COVER ")

        result = self.run_pipeline("--source-id", source_id)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        output_dir = Path(payload["outputDir"])
        metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["images"]["cover"]["status"], "prompt_ready")
        self.assertEqual(metadata["images"]["cover"]["generationMode"], "codex_tool_required")
        record = self.registry_record(source_id)
        self.assertEqual(record["status"], "used")

    def test_article_generation_failure_does_not_mark_source_used(self) -> None:
        source_id = self.register_source(marker="FAIL_ARTICLE ")

        result = self.run_pipeline("--source-id", source_id)

        self.assertNotEqual(result.returncode, 0)
        record = self.registry_record(source_id)
        self.assertEqual(record["status"], "failed")
        self.assertFalse(record.get("usedAt"))
        self.assertIn("article_generation_failed", "\n".join(record.get("notes") or []))

    def test_generation_prompt_requires_diverse_cover_direction(self) -> None:
        prompt = build_generation_prompt(
            source_id="source-test",
            source_markdown="跑步素材",
            profile_text="Profile",
            corpus_text="Corpus",
            profile_path=Path("profile.md"),
            corpus_path=Path("corpus.md"),
        )

        self.assertIn("头图不要模板化", prompt)
        self.assertIn("可以是男性或女性", prompt)
        self.assertIn("不要默认中国中年男性", prompt)
        self.assertIn("场景必须跟随文章主题变化", prompt)

    def test_fallback_cover_prompt_allows_gender_scene_and_style_variation(self) -> None:
        prompt = fallback_cover_prompt("冬天跑步会伤肺吗")

        self.assertIn("中国普通跑者，可以是男性或女性", prompt)
        self.assertIn("不要固定为清晨公园跑道", prompt)
        self.assertIn("根据主题选择", prompt)
        self.assertIn("多样化", prompt)
        self.assertNotIn("中国中年跑者或亚洲中国面孔的普通跑者", prompt)


if __name__ == "__main__":
    unittest.main()
