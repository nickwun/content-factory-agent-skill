import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "publish_feishu_batch.py"


class PublishFeishuBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "04-Outputs"
        self.root.mkdir(parents=True)
        self.publisher = Path(self.tmp.name) / "fake-publisher.py"
        self.publisher.write_text(
            """#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path

out = Path(sys.argv[1])
if "timeout" in out.name:
    time.sleep(10)
if "fail" in out.name:
    meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    meta.setdefault("publish", {}).setdefault("feishu", {})["status"] = "failed"
    meta["publish"]["feishu"]["error"] = "fake failed"
    (out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print("fake failed", file=sys.stderr)
    raise SystemExit(4)
meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
doc = "DOC_" + out.name.replace("-", "_")
owner_id = ""
if "--owner-user-id" in sys.argv:
    owner_id = sys.argv[sys.argv.index("--owner-user-id") + 1]
permission = {"status": "skipped", "grantedTo": "", "perm": "edit"}
if owner_id:
    permission = {"status": "granted", "grantedTo": "user_id:" + owner_id, "perm": "edit"}
meta.setdefault("publish", {}).setdefault("feishu", {}).update({
    "status": "published",
    "documentId": doc,
    "documentUrl": "https://feishu.cn/docx/" + doc,
    "backend": "feishu-cli",
    "publishedAt": "2026-05-20T00:00:00Z",
    "permission": permission,
})
(out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
(out / "publish-report.md").write_text("# Report\\n\\n- image upload result：`1/1`\\n", encoding="utf-8")
print(json.dumps({"status":"published","documentUrl":meta["publish"]["feishu"]["documentUrl"]}, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        self.publisher.chmod(0o755)
        self.builder = Path(self.tmp.name) / "fake-builder.py"
        self.builder.write_text(
            """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
(out / "feishu-publish.md").write_text("# 飞书发布稿\\n\\n![封面图](images/cover.png)\\n", encoding="utf-8")
meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
meta.setdefault("publish", {}).setdefault("feishu", {}).update({
    "status": "prepared",
    "markdownFile": "feishu-publish.md",
    "preparedAt": "2026-05-20T00:00:00Z",
})
(out / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"status":"prepared"}, ensure_ascii=False))
""",
            encoding="utf-8",
        )
        self.builder.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_output(
        self,
        name: str,
        *,
        published: bool = False,
        fail: bool = False,
        review_status: str = "",
        feishu_publish: bool = True,
        cover: bool = True,
        quality_status: str = "ready_for_edit",
    ) -> Path:
        output = self.root / name
        output.mkdir(parents=True)
        (output / "article.md").write_text("# Article\n\n正文", encoding="utf-8")
        (output / "titles.md").write_text("# 标题候选\n", encoding="utf-8")
        if cover:
            image = output / "images" / "cover.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"fake-png")
        if feishu_publish:
            (output / "feishu-publish.md").write_text("# 飞书发布稿\n\n![封面图](images/cover.png)\n", encoding="utf-8")
        metadata = {
            "title": name,
            "quality": {"status": quality_status, "score": 100},
            "images": {"cover": {"status": "generated", "outputPath": "images/cover.png"}},
            "publish": {"feishu": {"status": "published" if published else "prepared"}},
        }
        if published:
            metadata["publish"]["feishu"]["documentUrl"] = "https://feishu.cn/docx/OLD"
        if review_status:
            metadata["publish"]["feishu"]["review"] = {"status": review_status}
        if fail:
            output = output.rename(self.root / f"{name}-fail")
        (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return output

    def run_script(self, *extra: str, env_owner_user_id: str | None = "test") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("FEISHU_OWNER_USER_ID", None)
        env.pop("FEISHU_OWNER_EMAIL", None)
        env.pop("FEISHU_OWNER_OPEN_ID", None)
        env.pop("FEISHU_OWNER_UNION_ID", None)
        if env_owner_user_id is not None:
            env["FEISHU_OWNER_USER_ID"] = env_owner_user_id
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--root",
                str(self.root),
                "--publisher",
                str(self.publisher),
                "--builder",
                str(self.builder),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_batch_publishes_unpublished_ready_outputs_and_skips_published(self) -> None:
        first = self.write_output("2026-05-20-a")
        second = self.write_output("2026-05-20-b")
        self.write_output("2026-05-20-old", published=True)

        result = self.run_script("--limit", "3")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["results"]), 2)
        self.assertTrue(Path(payload["summaryPath"]).exists())
        for output in [first, second]:
            metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["publish"]["feishu"]["status"], "published")
            self.assertEqual(metadata["publish"]["feishu"]["permission"]["status"], "granted")
            self.assertEqual(metadata["publish"]["feishu"]["review"]["status"], "pending_review")
            self.assertTrue((output / "feishu-check.md").exists())
        summary = Path(payload["summaryPath"]).read_text(encoding="utf-8")
        self.assertIn("feishu_publish_batch_summary", summary)

    def test_limit_allows_up_to_five_outputs(self) -> None:
        outputs = [self.write_output(f"2026-05-20-{index}") for index in range(6)]

        result = self.run_script("--limit", "5")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["results"]), 5)
        published = [
            json.loads((output / "metadata.json").read_text(encoding="utf-8"))["publish"]["feishu"]["status"]
            for output in outputs
        ]
        self.assertEqual(published.count("published"), 5)
        self.assertEqual(published.count("prepared"), 1)

    def test_skips_outputs_already_in_wechat_review_end_states(self) -> None:
        skipped = self.write_output("2026-05-20-skipped", review_status="ready_for_wechat")
        publishable = self.write_output("2026-05-20-publishable")

        result = self.run_script("--limit", "5")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([Path(item["outputDir"]).name for item in payload["results"]], [publishable.name])
        skipped_meta = json.loads((skipped / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(skipped_meta["publish"]["feishu"]["status"], "prepared")

    def test_single_failure_does_not_interrupt_next_item(self) -> None:
        failed = self.write_output("2026-05-20-a", fail=True)
        success = self.write_output("2026-05-20-b")

        result = self.run_script("--limit", "3")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        statuses = [item["status"] for item in payload["results"]]
        self.assertIn("failed", statuses)
        self.assertIn("published", statuses)
        failed_meta = json.loads((failed / "metadata.json").read_text(encoding="utf-8"))
        success_meta = json.loads((success / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(failed_meta["publish"]["feishu"]["status"], "failed")
        self.assertEqual(success_meta["publish"]["feishu"]["status"], "published")

    def test_missing_owner_fails_batch_by_default(self) -> None:
        output = self.write_output("2026-05-20-needs-owner")

        result = self.run_script("--limit", "1", env_owner_user_id=None)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FEISHU_OWNER", result.stderr)
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["publish"]["feishu"]["status"], "prepared")

    def test_allow_permission_skip_continues_without_owner(self) -> None:
        output = self.write_output("2026-05-20-skip-owner")

        result = self.run_script("--limit", "1", "--allow-permission-skip", env_owner_user_id=None)

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["publish"]["feishu"]["status"], "published")
        self.assertEqual(metadata["publish"]["feishu"]["permission"]["status"], "skipped")

    def test_multiple_output_dirs_dry_run_preserves_order_and_does_not_scan(self) -> None:
        other = self.write_output("2026-05-20-aaa-other")
        second = self.write_output("2026-05-20-second")
        first = self.write_output("2026-05-20-first")

        result = self.run_script(
            "--output-dir",
            str(second),
            "--output-dir",
            str(first),
            "--limit",
            "2",
            "--dry-run",
            "--run-id",
            "multi-dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selectedCount"], 2)
        self.assertEqual([item["outputDir"] for item in payload["results"]], [str(second.resolve()), str(first.resolve())])
        state = json.loads(Path(payload["statePath"]).read_text(encoding="utf-8"))
        self.assertEqual(state["selectionMode"], "output_dirs")
        self.assertEqual(state["selected"], [str(second.resolve()), str(first.resolve())])
        self.assertIn(second.name, state["articles"])
        self.assertIn(first.name, state["articles"])
        self.assertNotIn(other.name, state["articles"])
        self.assertEqual(state["articles"][second.name]["current_stage"], "dry_run")
        self.assertEqual(state["articles"][first.name]["current_stage"], "dry_run")

    def test_multiple_output_dirs_dry_run_skips_published_and_keeps_unpublished(self) -> None:
        published = self.write_output("2026-05-20-published", published=True)
        draft = self.write_output("2026-05-20-draft")

        result = self.run_script(
            "--output-dir",
            str(published),
            "--output-dir",
            str(draft),
            "--limit",
            "2",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([item["status"] for item in payload["results"]], ["skipped", "dry_run"])
        self.assertEqual(payload["results"][0]["skippedReason"], "already_published")

    def test_multiple_output_dirs_missing_feishu_publish_is_skipped_without_publisher(self) -> None:
        missing = self.write_output("2026-05-20-missing-feishu", feishu_publish=False)

        result = self.run_script("--output-dir", str(missing), "--limit", "1")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["results"][0]["status"], "skipped")
        self.assertEqual(payload["results"][0]["skippedReason"], "missing_feishu_publish")
        self.assertFalse((missing / "publish-report.md").exists())
        metadata = json.loads((missing / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["publish"]["feishu"]["status"], "prepared")

    def test_duplicate_output_dir_errors_before_lock_or_state(self) -> None:
        output = self.write_output("2026-05-20-duplicate")

        result = self.run_script(
            "--output-dir",
            str(output),
            "--output-dir",
            str(output),
            "--limit",
            "2",
            "--run-id",
            "duplicate-output",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate --output-dir", result.stderr)
        self.assertFalse((self.root / ".codex_locks" / "feishu_publish.lock").exists())
        self.assertFalse((self.root / "batch-runs" / "duplicate-output" / "run_state.json").exists())

    def test_output_dir_outside_root_errors_before_lock_or_state(self) -> None:
        inside = self.write_output("2026-05-20-inside")
        outside_root = Path(self.tmp.name) / "outside"
        outside_root.mkdir()
        outside = outside_root / "outside-article"
        outside.mkdir()
        (outside / "metadata.json").write_text(json.dumps({"publish": {"feishu": {"status": "prepared"}}}), encoding="utf-8")

        result = self.run_script(
            "--output-dir",
            str(inside),
            "--output-dir",
            str(outside),
            "--limit",
            "2",
            "--run-id",
            "outside-root",
            "--dry-run",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be inside --root", result.stderr)
        self.assertFalse((self.root / ".codex_locks" / "feishu_publish.lock").exists())
        self.assertFalse((self.root / "batch-runs" / "outside-root" / "run_state.json").exists())

    def test_real_timeout_stops_later_explicit_outputs(self) -> None:
        timeout = self.write_output("2026-05-20-timeout")
        later = self.write_output("2026-05-20-later")

        result = self.run_script(
            "--output-dir",
            str(timeout),
            "--output-dir",
            str(later),
            "--limit",
            "2",
            "--single-timeout",
            "0.1",
            "--run-id",
            "timeout-stops",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([item["status"] for item in payload["results"]], ["failed", "not_started_after_failure"])
        timeout_meta = json.loads((timeout / "metadata.json").read_text(encoding="utf-8"))
        later_meta = json.loads((later / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(timeout_meta["publish"]["feishu"]["requiresRemoteCheck"])
        self.assertEqual(later_meta["publish"]["feishu"]["status"], "prepared")
        state = json.loads(Path(payload["statePath"]).read_text(encoding="utf-8"))
        self.assertTrue(state["articles"][timeout.name]["requires_remote_check"])
        self.assertEqual(state["articles"][later.name]["current_stage"], "not_started_after_failure")
        self.assertIn("previous article", state["articles"][later.name]["last_error"])


if __name__ == "__main__":
    unittest.main()
