import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "quality_check_output.py"


VALID_ARTICLE = """# 跑步距离怎么选

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
"""


class QualityCheckOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "04-Outputs"
        self.output = self.root / "2026-05-20-safe-slug"
        self.output.mkdir(parents=True)
        self.write_output(self.output, VALID_ARTICLE, cover=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_output(self, output: Path, article: str, *, cover: bool, source_path: Path | None = None) -> None:
        output.mkdir(parents=True, exist_ok=True)
        (output / "article.md").write_text(article, encoding="utf-8")
        (output / "brief.md").write_text("# Brief\n\n说明", encoding="utf-8")
        (output / "cover-prompt.md").write_text("# Cover Prompt\n\n4:3", encoding="utf-8")
        (output / "titles.md").write_text(
            "# 标题候选\n\n## 击中痛点型\n\n1. 痛点一\n2. 痛点二\n3. 痛点三\n4. 痛点四\n5. 痛点五\n\n## 认知差型\n\n1. 认知一\n2. 认知二\n3. 认知三\n4. 认知四\n5. 认知五\n\n## 推荐首选\n\n- 首选标题：痛点一\n- 备选标题：认知一\n- 推荐理由：测试。\n",
            encoding="utf-8",
        )
        if cover:
            image = output / "images" / "cover.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"fake-png")
        metadata = {
            "type": "output_metadata",
            "title": "跑步距离怎么选",
            "slug": output.name[11:],
            "outputDir": str(output),
            "profileId": "profile-ahong-running-rewrite",
            "corpusId": "corpus-ahong-running-style",
            "images": {
                "cover": {
                    "status": "generated" if cover else "prompt_ready",
                    "outputPath": "images/cover.png",
                    "ratio": "4:3",
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
            "sourceMaterials": [
                {
                    "sourceId": "source-test",
                    "normalizedPath": str(source_path),
                    "title": "测试素材",
                }
            ]
            if source_path
            else [],
        }
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_ready_output_updates_metadata_and_report(self) -> None:
        result = self.run_script(str(self.output))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["results"][0]["status"], "ready_for_edit")
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        quality = metadata["quality"]
        self.assertEqual(quality["status"], "ready_for_edit")
        self.assertGreaterEqual(quality["score"], 85)
        self.assertEqual(quality["issues"], [])
        report = (self.output / "quality-report.md").read_text(encoding="utf-8")
        self.assertIn("建议进入网页工作台编辑：是", report)

    def test_missing_cover_and_meta_narration_need_revision(self) -> None:
        bad = self.root / "2026-05-20-bad"
        self.write_output(bad, VALID_ARTICLE + "\n\n素材里提到这个观点。\n", cover=False)

        result = self.run_script(str(bad))

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((bad / "metadata.json").read_text(encoding="utf-8"))
        quality = metadata["quality"]
        self.assertEqual(quality["status"], "needs_revision")
        self.assertTrue(any("元叙述" in item for item in quality["issues"]))
        self.assertTrue(any("cover" in item for item in quality["issues"]))

    def test_batch_writes_quality_summary(self) -> None:
        second = self.root / "2026-05-20-second"
        self.write_output(second, VALID_ARTICLE, cover=True)

        result = self.run_script(str(self.output), str(second))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        summary = Path(payload["summaryPath"])
        self.assertTrue(summary.exists())
        self.assertIn("quality_batch_summary", summary.read_text(encoding="utf-8"))

    def test_warns_when_article_contains_source_sentence(self) -> None:
        source = self.root / "source.md"
        source.write_text("把呼吸跑顺，把腿脚跑热，把第二天的精神状态也照顾好，这才是普通人最值得珍惜的进步。", encoding="utf-8")
        copied = self.root / "2026-05-20-copied"
        self.write_output(copied, VALID_ARTICLE, cover=True, source_path=source)

        result = self.run_script(str(copied))

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((copied / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(any("疑似复制素材原句" in item for item in metadata["quality"]["warnings"]))

    def test_missing_titles_need_revision(self) -> None:
        missing = self.root / "2026-05-20-missing-titles"
        self.write_output(missing, VALID_ARTICLE, cover=True)
        (missing / "titles.md").unlink()
        metadata = json.loads((missing / "metadata.json").read_text(encoding="utf-8"))
        metadata.pop("titles")
        (missing / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        result = self.run_script(str(missing))

        self.assertEqual(result.returncode, 0, result.stderr)
        quality = json.loads((missing / "metadata.json").read_text(encoding="utf-8"))["quality"]
        self.assertEqual(quality["status"], "needs_revision")
        self.assertTrue(any("titles" in item for item in quality["issues"]))

    def test_web_story_checks_pass_for_grounded_article(self) -> None:
        source = self.root / "web-story-source.md"
        source.write_text(
            "吴浩然是湖北咸宁的普通跑者，2017年开始重新跑步，后来在衡水湖马拉松跑出2小时19分29秒。",
            encoding="utf-8",
        )
        web_story = self.root / "2026-05-20-web-story"
        self.write_output(web_story, VALID_ARTICLE.replace("跑步距离怎么选", "普通人跑步，最怕的不是起点低"), cover=True, source_path=source)
        metadata = json.loads((web_story / "metadata.json").read_text(encoding="utf-8"))
        metadata.update(
            {
                "sourceType": "web_story",
                "webSourceId": "web-source-test",
                "url": "https://example.com/story",
                "sourceName": "腾讯",
                "originalTitle": "从纯业余跑者成为国家级健将",
                "profileId": "profile-ahong-running-story-rewrite",
            }
        )
        (web_story / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        result = self.run_script(str(web_story))

        self.assertEqual(result.returncode, 0, result.stderr)
        quality = json.loads((web_story / "metadata.json").read_text(encoding="utf-8"))["quality"]
        self.assertEqual(quality["webStoryChecks"]["factual_boundary_check"], "pass")
        self.assertEqual(quality["webStoryChecks"]["no_first_person_impersonation"], "pass")
        self.assertEqual(quality["webStoryChecks"]["no_unverified_details"], "pass")
        self.assertEqual(quality["webStoryChecks"]["no_lowbrow_oddity_angle"], "pass")
        self.assertEqual(quality["webStoryChecks"]["no_mocking_subject"], "pass")
        self.assertEqual(quality["webStoryChecks"]["no_accident_as_joke"], "pass")
        self.assertEqual(quality["webStoryChecks"]["no_clickbait_exaggeration"], "pass")
        report = (web_story / "quality-report.md").read_text(encoding="utf-8")
        self.assertIn("## Web Story 专属检查", report)

    def test_web_story_checks_flag_impersonation_and_lowbrow_angle(self) -> None:
        bad = self.root / "2026-05-20-bad-web-story"
        article = VALID_ARTICLE.replace(
            "很多人一开始跑步，都会急着问自己，到底该跑 5 公里，还是直接冲 10 公里。",
            "我叫吴浩然，当年我在衡水湖冲线时所有人都震惊了，这个故事最奇葩的地方特别适合拿来猎奇。",
        )
        self.write_output(bad, article, cover=True)
        metadata = json.loads((bad / "metadata.json").read_text(encoding="utf-8"))
        metadata.update(
            {
                "sourceType": "web_story",
                "webSourceId": "web-source-test",
                "url": "https://example.com/story",
                "sourceName": "腾讯",
                "originalTitle": "从纯业余跑者成为国家级健将",
                "profileId": "profile-ahong-running-story-rewrite",
            }
        )
        (bad / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        result = self.run_script(str(bad))

        self.assertEqual(result.returncode, 0, result.stderr)
        quality = json.loads((bad / "metadata.json").read_text(encoding="utf-8"))["quality"]
        self.assertEqual(quality["status"], "rejected")
        self.assertEqual(quality["webStoryChecks"]["no_first_person_impersonation"], "fail")
        self.assertEqual(quality["webStoryChecks"]["no_lowbrow_oddity_angle"], "fail")

    def test_oddity_story_checks_flag_mocking_accident_joke_and_clickbait(self) -> None:
        bad = self.root / "2026-05-20-bad-oddity-web-story"
        article = VALID_ARTICLE.replace(
            "很多人一开始跑步，都会急着问自己，到底该跑 5 公里，还是直接冲 10 公里。",
            "大家都把她当笑柄看，这种赛道事故太好笑了。千万别跑步，否则大脑会失控。",
        )
        self.write_output(bad, article, cover=True)
        metadata = json.loads((bad / "metadata.json").read_text(encoding="utf-8"))
        metadata.update(
            {
                "sourceType": "web_story",
                "webCategory": "oddity",
                "webSourceId": "web-source-oddity-test",
                "url": "https://example.com/oddity",
                "sourceName": "网易",
                "originalTitle": "女子跑马途中动作异常",
                "profileId": "profile-ahong-running-story-rewrite",
            }
        )
        (bad / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        result = self.run_script(str(bad))

        self.assertEqual(result.returncode, 0, result.stderr)
        quality = json.loads((bad / "metadata.json").read_text(encoding="utf-8"))["quality"]
        self.assertEqual(quality["status"], "needs_revision")
        self.assertEqual(quality["webStoryChecks"]["no_mocking_subject"], "fail")
        self.assertEqual(quality["webStoryChecks"]["no_accident_as_joke"], "fail")
        self.assertEqual(quality["webStoryChecks"]["no_clickbait_exaggeration"], "fail")


if __name__ == "__main__":
    unittest.main()
