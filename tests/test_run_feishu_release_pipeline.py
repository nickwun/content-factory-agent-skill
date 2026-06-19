import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "run_feishu_release_pipeline.py"


ARTICLE = """# 跑步不发朋友圈以后，我反而轻松了

跑步这件事，有时候不是给别人看的。

## 01、刚开始想被看见

刚开始跑步的人，很需要一点外界回应。你从沙发上站起来，穿上跑鞋，跑完一段从前觉得不可能的路，当然想让别人知道。朋友圈的点赞，有时候像一阵顺风。它会推你一把，让你觉得今天这点汗没有白流。对很多新手来说，分享本身也是一种自我承诺。你发出去了，就好像对自己说：我真的开始了。下一次想偷懒的时候，你会想起有人曾经鼓励过你，也会想起自己说过要坚持。所以，跑步初期爱发朋友圈，并不只是炫耀。它也可能是一个普通人在给自己搭一根拐杖。只要这根拐杖能帮你迈出第一步，它就有价值。

## 02、后来更想安静

可是，跑步一旦变成日常，心态就会慢慢变。你不再那么需要每一次跑完都被确认。因为身体已经给了你答案。睡得更沉了，心情更稳了，爬楼不喘了，整个人也没有那么容易被琐事拖住。这些变化，比十几个点赞更扎实。很多跑者就是在这个阶段，慢慢减少分享。不是因为他们变冷淡了，而是跑步对他们来说，已经从我要证明变成了我愿意这样生活。以前发动态，是为了告诉别人我在坚持。后来不发了，是因为自己已经知道自己在坚持。这个转变，其实很珍贵。它说明你不再把跑步当成表演，也不再把别人的反应当成动力来源。

## 03、不晒也是热爱

有些人不发朋友圈以后，反而跑得更久。因为他终于不用在意今天的数据好不好看，也不用琢磨文案该怎么写。他可以慢一点，可以短一点，可以只是沿着熟悉的路跑一圈。跑步不再是给别人看的成绩单，而是留给自己的小房间。你在里面喘口气，整理情绪，也把那些说不出口的压力慢慢放下。当然，继续分享也没问题。如果朋友圈能给你力量，那就大大方方发。如果你更喜欢安静地跑，也完全不用解释。跑步最好的状态，从来不是让所有人知道你有多自律。而是你越来越清楚，自己为什么要跑。发不发朋友圈，真的没那么重要。重要的是，你还愿意为自己留一段路，还愿意在风里、汗里、呼吸里，把自己一点点慢慢找回来。
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
        "reason": "首选标题贴合文章主题，语气克制。",
    },
}


MISMATCHED_TITLES = {
    "pain_point": [
        "5公里和10公里到底该怎么选？",
        "普通跑者面对距离选择，别再跑偏",
        "配速太慢是不是没效果？",
        "每天跑5公里还是隔天跑10公里？",
        "刚开始跑步就想一步到位，普通人最容易吃亏",
    ],
    "cognitive_gap": [
        "5公里和10公里的差别，不只是数字",
        "距离不是越远越好",
        "配速慢一点反而更适合普通人",
        "公里数背后是恢复能力",
        "跑得少未必没用",
    ],
    "recommended": {
        "primary": "5公里和10公里到底该怎么选？",
        "secondary": "距离不是越远越好",
        "reason": "测试跑题标题。",
    },
}


class RunFeishuReleasePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "04-Outputs"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_titles_md(self, output: Path, titles: dict) -> None:
        lines = [
            "# 标题候选",
            "",
            "## 击中痛点型",
            "",
            *[f"{index}. {title}" for index, title in enumerate(titles["pain_point"], 1)],
            "",
            "## 认知差型",
            "",
            *[f"{index}. {title}" for index, title in enumerate(titles["cognitive_gap"], 1)],
            "",
            "## 推荐首选",
            "",
            f"- 首选标题：{titles['recommended']['primary']}",
            f"- 备选标题：{titles['recommended']['secondary']}",
            f"- 推荐理由：{titles['recommended']['reason']}",
            "",
        ]
        (output / "titles.md").write_text("\n".join(lines), encoding="utf-8")

    def write_output(
        self,
        name: str,
        *,
        quality_status: str | None = "ready_for_edit",
        titles: dict | None = VALID_TITLES,
        published: bool = False,
        requires_remote_check: bool = False,
        cover: bool = True,
        article: str = ARTICLE,
    ) -> Path:
        output = self.root / name
        output.mkdir(parents=True)
        (output / "article.md").write_text(article, encoding="utf-8")
        (output / "brief.md").write_text("# Brief\n", encoding="utf-8")
        (output / "cover-prompt.md").write_text("# Cover\n", encoding="utf-8")
        if cover:
            cover_path = output / "images" / "cover.png"
            cover_path.parent.mkdir(parents=True)
            cover_path.write_bytes(b"fake-png")
        metadata = {
            "type": "output_metadata",
            "title": name,
            "slug": name,
            "outputDir": str(output),
            "profileId": "profile-ahong-running-rewrite",
            "corpusId": "corpus-ahong-running-style",
            "createdAt": "2026-06-19T00:00:00Z",
            "images": {"cover": {"status": "generated", "outputPath": "images/cover.png"}},
            "publish": {"feishu": {"status": "published" if published else "draft"}},
        }
        if quality_status is not None:
            metadata["quality"] = {"status": quality_status, "score": 90 if quality_status == "ready_for_edit" else 60}
            (output / "quality-report.md").write_text("# Quality\n", encoding="utf-8")
        if titles is not None:
            metadata["titles"] = titles
            self.write_titles_md(output, titles)
        if published:
            metadata["publish"]["feishu"]["documentUrl"] = "https://feishu.cn/docx/OLD"
        if requires_remote_check:
            metadata["publish"]["feishu"]["requiresRemoteCheck"] = True
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return output

    def run_script(self, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--root",
                str(self.root),
                "--count",
                "5",
                "--run-id",
                "test-release",
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_inspect_scans_without_writing_vault_or_building(self) -> None:
        ready = self.write_output("2026-06-19-running-social-quiet")
        before_files = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())

        result = self.run_script("--mode", "inspect")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "inspect")
        self.assertEqual([Path(item["outputDir"]).name for item in payload["candidates"]], [ready.name])
        self.assertEqual(payload["prepared"], [])
        self.assertEqual(payload["paths"]["runState"], "")
        self.assertFalse((ready / "feishu-publish.md").exists())
        after_files = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(before_files, after_files)

    def test_prepare_prepares_low_risk_outputs_and_skips_published_risky_and_blocked(self) -> None:
        ready = self.write_output("2026-06-19-running-social-quiet")
        self.write_output("2026-06-19-heart-risk-story")
        self.write_output("2026-06-19-already-published", published=True)
        self.write_output("2026-06-19-needs-remote-check", requires_remote_check=True)
        blocked = self.write_output("2026-06-19-historical-blocked")
        run_dir = self.root / "batch-runs" / "old-run"
        run_dir.mkdir(parents=True)
        (run_dir / "run_state.json").write_text(
            json.dumps(
                {
                    "articles": {
                        blocked.name: {
                            "current_stage": "blocked_remote_check",
                            "requires_remote_check": True,
                            "skipped_reason": "requires_remote_check",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.run_script("--mode", "prepare")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([Path(item["outputDir"]).name for item in payload["prepared"]], [ready.name])
        skipped = {item["slug"]: item["reason"] for item in payload["skipped"]}
        self.assertEqual(skipped["2026-06-19-heart-risk-story"], "risky_topic")
        self.assertEqual(skipped["2026-06-19-already-published"], "already_published")
        self.assertEqual(skipped["2026-06-19-needs-remote-check"], "requires_remote_check")
        self.assertEqual(skipped["2026-06-19-historical-blocked"], "historical_blocked")
        self.assertTrue((ready / "feishu-publish.md").exists())
        self.assertEqual(payload["batchDryRun"]["selectedCount"], 0)
        self.assertTrue(Path(payload["paths"]["runState"]).exists())
        self.assertTrue(Path(payload["paths"]["summary"]).exists())
        self.assertTrue(Path(payload["paths"]["backupManifest"]).exists())

    def test_missing_quality_runs_quality_check_and_skips_needs_revision(self) -> None:
        bad_article = "# 太短\n\n## 01、短\n\n不够。\n\n## 02、短\n\n不够。\n\n## 03、短\n\n不够。\n"
        output = self.write_output("2026-06-19-short-draft", quality_status=None, article=bad_article)

        result = self.run_script("--mode", "prepare")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        skipped = {item["slug"]: item["reason"] for item in payload["skipped"]}
        self.assertEqual(skipped[output.name], "quality_not_ready:needs_revision")
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["quality"]["status"], "needs_revision")

    def test_missing_titles_reports_codex_required_without_fallback_or_build(self) -> None:
        output = self.write_output("2026-06-19-social-no-titles", titles=None)

        env = os.environ.copy()
        env["OPENROUTER_API_KEY"] = "must-not-be-used"
        env["OPENROUTER_BASE_URL"] = "http://127.0.0.1:9"
        result = self.run_script("--mode", "prepare", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        skipped = {item["slug"]: item["reason"] for item in payload["skipped"]}
        self.assertEqual(skipped[output.name], "codex_title_required")
        self.assertEqual(payload["prepared"], [])
        self.assertFalse((output / "titles.md").exists())
        self.assertFalse((output / "feishu-publish.md").exists())
        self.assertFalse(any(path.name.startswith("generate_titles") for path in output.rglob("*")))
        self.assertTrue(Path(payload["paths"]["codexRequiredTasks"]).exists())

    def test_title_fallback_requires_explicit_flag_and_still_blocks_publish_ready(self) -> None:
        output = self.write_output("2026-06-19-social-no-titles", titles=None)

        result = self.run_script("--mode", "prepare", "--allow-title-fallback")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        skipped = {item["slug"]: item["reason"] for item in payload["skipped"]}
        self.assertEqual(skipped[output.name], "needs_manual_title_review")
        self.assertEqual(payload["prepared"], [])
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        self.assertNotIn("titles", metadata)
        self.assertFalse((output / "feishu-publish.md").exists())

    def test_mismatched_titles_enter_manual_review_and_do_not_build(self) -> None:
        output = self.write_output("2026-06-19-social-mismatched-title", titles=MISMATCHED_TITLES)

        result = self.run_script("--mode", "prepare")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        skipped = {item["slug"]: item["reason"] for item in payload["skipped"]}
        self.assertEqual(skipped[output.name], "needs_manual_title_review")
        self.assertFalse((output / "feishu-publish.md").exists())

    def test_guarded_mode_runs_batch_dry_run_and_outputs_next_command(self) -> None:
        ready = self.write_output("2026-06-19-running-guarded")

        result = self.run_script("--mode", "guarded", "--allow-permission-skip")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "guarded")
        self.assertEqual(payload["batchDryRun"]["selectedCount"], 1)
        self.assertIn("--output-dir", payload["guardedPublishCommand"])
        self.assertIn(str(ready), payload["guardedPublishCommand"])
        self.assertIn("--allow-permission-skip", payload["guardedPublishCommand"])
        metadata = json.loads((ready / "metadata.json").read_text(encoding="utf-8"))
        self.assertNotEqual(metadata["publish"]["feishu"].get("status"), "published")


if __name__ == "__main__":
    unittest.main()
