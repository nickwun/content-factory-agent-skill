import json
import os
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "run_pipeline_batch.py"
SCAN_SCRIPT = SKILL_DIR / "scripts" / "scan_docx_materials.py"


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
cover_body = "FAIL_GENERATION" if "FAIL_COVER" in prompt else "中国中年跑者在清晨跑道上慢跑，4:3，禁止文字和 logo。"
titles = {
    "pain_point": [
        "普通跑者面对5公里和10公里，到底该怎么选才不容易跑偏？",
        "5公里不是问题，真正容易出错的是忽略恢复",
        "为什么很多人跑步一认真，反而把热情跑丢了？",
        "跑5公里还是10公里，别再互相看不起了",
        "刚开始跑步就想一步到位，普通人最容易吃这个亏",
    ],
    "cognitive_gap": [
        "5公里和10公里的差别，不只是数字和距离",
        "普通跑者先学会跑得舒服，比急着证明自己更重要",
        "跑步不是越远越值，能长期恢复才是真正的有效",
        "看懂第二天身体反馈，你就不会总被公里数牵着走",
        "很多人以为跑得少没用，其实合适的距离更能留住状态",
    ],
    "recommended": {
        "primary": "普通跑者面对5公里和10公里，到底该怎么选才不容易跑偏？",
        "secondary": "5公里和10公里的差别，不只是数字和距离",
        "reason": "首选标题击中普通跑者的选择焦虑，备选标题提供认知翻转。",
    },
}

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
print(json.dumps({
    "title": "跑步距离怎么选",
    "article": article,
    "brief": "# Brief\\n\\n- 小批量测试。",
    "coverPrompt": "# Cover Prompt\\n\\n## 视觉主体\\n\\n" + cover_body,
    "titles": titles,
}, ensure_ascii=False))
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
image.write_bytes(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lx4n1wAAAABJRU5ErkJggg=="
))
print(json.dumps({"provider": "fake-provider", "model": "fake-model"}))
"""


def write_docx(path: Path, title: str, marker: str = "") -> None:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:p><w:r><w:t>{marker}这是一篇跑步素材，主题清晰，适合测试小批量 pipeline。</w:t></w:r></w:p>
    <w:p><w:r><w:t>素材强调普通跑者要根据身体状态安排训练。</w:t></w:r></w:p>
    <w:p><w:r><w:t>同时也提醒跑者关注恢复、长期坚持和健康边界。</w:t></w:r></w:p>
    <w:p><w:r><w:t>正文长度足够，不应该被 cleaning-note 拦截。</w:t></w:r></w:p>
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
        archive.writestr("word/document.xml", xml)


class RunPipelineBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "ContentFactoryVault"
        self.raw_dir = self.vault / "01-Materials" / "docx-raw"
        self.profile = self.vault / "02-Profiles" / "ahong-running-rewrite.md"
        self.corpus = self.vault / "03-Corpus" / "ahong-running-style.md"
        self.raw_dir.mkdir(parents=True)
        self.profile.parent.mkdir(parents=True)
        self.corpus.parent.mkdir(parents=True)
        self.profile.write_text("---\nprofile_id: profile-ahong-running-rewrite\n---\n# Profile\n", encoding="utf-8")
        self.corpus.write_text("---\ncorpus_id: corpus-ahong-running-style\n---\n# Corpus\n", encoding="utf-8")
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
        return env

    def seed_sources(self) -> list[str]:
        write_docx(self.raw_dir / "训练建议.docx", "训练建议")
        write_docx(self.raw_dir / "健康风险.docx", "健康风险", marker="FAIL_ARTICLE ")
        write_docx(self.raw_dir / "情绪价值.docx", "情绪价值", marker="FAIL_COVER ")
        result = subprocess.run(
            ["python3", str(SCAN_SCRIPT), "--vault", str(self.vault)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads((self.vault / "01-Materials" / "source-registry.json").read_text(encoding="utf-8"))
        return [item["sourceId"] for item in registry]

    def seed_many_sources(self, count: int) -> list[str]:
        for index in range(count):
            write_docx(self.raw_dir / f"跑步素材{index}.docx", f"跑步素材{index}")
        result = subprocess.run(
            ["python3", str(SCAN_SCRIPT), "--vault", str(self.vault)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads((self.vault / "01-Materials" / "source-registry.json").read_text(encoding="utf-8"))
        return [item["sourceId"] for item in registry]

    def test_batch_runs_three_serial_items_and_writes_summary(self) -> None:
        source_ids = self.seed_sources()
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--vault",
                str(self.vault),
                "--profile",
                str(self.profile),
                "--corpus",
                str(self.corpus),
                "--source-id",
                source_ids[0],
                "--source-id",
                source_ids[1],
                "--source-id",
                source_ids[2],
            ],
            check=False,
            capture_output=True,
            text=True,
            env=self.env(),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["results"]), 3)
        statuses = [item["status"] for item in payload["results"]]
        self.assertEqual(statuses.count("success"), 2)
        self.assertEqual(statuses.count("failed"), 1)
        self.assertTrue(any(item["status"] == "success" and item["coverStatus"] == "prompt_ready" for item in payload["results"]))
        summary = Path(payload["summaryPath"])
        self.assertTrue(summary.exists())
        summary_text = summary.read_text(encoding="utf-8")
        self.assertIn(source_ids[0], summary_text)
        self.assertIn("Registry Status", summary_text)
        registry = json.loads((self.vault / "01-Materials" / "source-registry.json").read_text(encoding="utf-8"))
        by_id = {item["sourceId"]: item for item in registry}
        selected_statuses = [by_id[source_id]["status"] for source_id in source_ids]
        self.assertEqual(selected_statuses.count("used"), 2)
        self.assertEqual(selected_statuses.count("failed"), 1)

    def test_limit_allows_five_serial_items(self) -> None:
        self.seed_many_sources(6)

        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--vault",
                str(self.vault),
                "--profile",
                str(self.profile),
                "--corpus",
                str(self.corpus),
                "--limit",
                "5",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=self.env(),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["results"]), 5)
        self.assertEqual([item["status"] for item in payload["results"]].count("success"), 5)
        registry = json.loads((self.vault / "01-Materials" / "source-registry.json").read_text(encoding="utf-8"))
        self.assertEqual([item["status"] for item in registry].count("used"), 5)
        self.assertEqual([item["status"] for item in registry].count("unused"), 1)


if __name__ == "__main__":
    unittest.main()
