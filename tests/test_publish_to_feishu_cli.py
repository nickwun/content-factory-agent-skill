import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "publish_to_feishu_cli.py"
TEST_OWNER_USER_ID = "test_owner_user"


class PublishToFeishuCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "2026-05-20-safe-slug"
        self.output.mkdir(parents=True)
        self.fake_cli = Path(self.tmp.name) / "fake-feishu-cli.py"
        self.capture = Path(self.tmp.name) / "cli-args.json"
        self.fake_cli.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys

capture = os.environ.get("FAKE_FEISHU_CAPTURE")
if capture:
    with open(capture, "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv, ensure_ascii=False) + "\\n")
if "perm" in sys.argv and "add" in sys.argv:
    if os.environ.get("FAKE_FEISHU_PERM_FAIL") == "1":
        print("fake permission failed", file=sys.stderr)
        raise SystemExit(8)
    print("权限添加成功！\\n  权限: edit")
    raise SystemExit(0)
if os.environ.get("FAKE_FEISHU_FAIL") == "1":
    print("fake import failed", file=sys.stderr)
    raise SystemExit(9)
print(json.dumps({
    "document_id": "DOC123abc",
    "blocks": 12,
    "image_success": 1,
    "image_total": 1,
    "duration_seconds": 1.23
}, ensure_ascii=False))
print("链接: https://feishu.cn/docx/DOC123abc", file=sys.stderr)
""",
            encoding="utf-8",
        )
        self.fake_cli.chmod(0o755)
        self.write_output()
        self.registry = Path(self.tmp.name) / "source-registry.json"
        self.registry.write_text('{"keep":"same"}', encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_output(self, *, status: str = "prepared", quality_status: str = "ready_for_edit", cover: bool = True) -> None:
        (self.output / "article.md").write_text("# Article\n\n正文", encoding="utf-8")
        (self.output / "titles.md").write_text("# 标题候选\n", encoding="utf-8")
        (self.output / "feishu-publish.md").write_text("# 飞书发布稿\n\n![封面图](images/cover.png)\n", encoding="utf-8")
        if cover:
            image = self.output / "images" / "cover.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"fake-png")
        metadata = {
            "title": "原始标题",
            "titles": {
                "recommended": {
                    "primary": "首选标题",
                    "secondary": "备选标题",
                    "reason": "测试。",
                }
            },
            "quality": {"status": quality_status, "score": 100},
            "images": {"cover": {"status": "generated", "outputPath": "images/cover.png"}},
            "publish": {"feishu": {"status": status, "markdownFile": "feishu-publish.md"}},
        }
        if status == "published":
            metadata["publish"]["feishu"].update(
                {
                    "documentId": "OLD_DOC",
                    "documentUrl": "https://feishu.cn/docx/OLD_DOC",
                }
            )
        (self.output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_script(
        self,
        *extra: str,
        fail_cli: bool = False,
        owner_user_id: str | None = TEST_OWNER_USER_ID,
        env_owner_user_id: str | None = TEST_OWNER_USER_ID,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FEISHU_APP_ID"] = "cli_fake"
        env["FEISHU_APP_SECRET"] = "secret_fake"
        env.pop("FEISHU_OWNER_USER_ID", None)
        env.pop("FEISHU_OWNER_EMAIL", None)
        env.pop("FEISHU_OWNER_OPEN_ID", None)
        env.pop("FEISHU_OWNER_UNION_ID", None)
        if env_owner_user_id is not None:
            env["FEISHU_OWNER_USER_ID"] = env_owner_user_id
        env["FAKE_FEISHU_CAPTURE"] = str(self.capture)
        if fail_cli:
            env["FAKE_FEISHU_FAIL"] = "1"
        if "--fail-permission" in extra:
            env["FAKE_FEISHU_PERM_FAIL"] = "1"
            extra = tuple(item for item in extra if item != "--fail-permission")
        if owner_user_id is not None and "--owner-user-id" not in extra:
            extra = (*extra, "--owner-user-id", owner_user_id)
        return subprocess.run(
            ["python3", str(SCRIPT), str(self.output), "--cli", str(self.fake_cli), *extra],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_missing_feishu_publish_markdown_fails(self) -> None:
        (self.output / "feishu-publish.md").unlink()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("feishu-publish.md", result.stderr)

    def test_non_prepared_status_fails(self) -> None:
        self.write_output(status="draft")

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publish.feishu.status", result.stderr)

    def test_published_without_force_refuses_duplicate_publish(self) -> None:
        self.write_output(status="published")

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already published", result.stderr)

    def test_successful_cli_output_updates_metadata_and_report(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["status"], "published")
        self.assertEqual(feishu["documentId"], "DOC123abc")
        self.assertEqual(feishu["documentUrl"], "https://feishu.cn/docx/DOC123abc")
        self.assertEqual(feishu["backend"], "feishu-cli")
        self.assertEqual(feishu["permission"]["status"], "granted")
        self.assertEqual(feishu["permission"]["grantedTo"], f"user_id:{TEST_OWNER_USER_ID}")
        self.assertTrue(feishu["publishedAt"])
        report = (self.output / "publish-report.md").read_text(encoding="utf-8")
        self.assertIn("DOC123abc", report)
        self.assertIn("permission status", report)
        self.assertIn("image_success", report)
        calls = [json.loads(line) for line in self.capture.read_text(encoding="utf-8").splitlines() if line.strip()]
        import_args = calls[0]
        perm_args = calls[1]
        self.assertIn("doc", import_args)
        self.assertIn("import", import_args)
        self.assertIn("feishu-publish.md", import_args)
        self.assertIn("首选标题", import_args)
        self.assertIn("perm", perm_args)
        self.assertIn("add", perm_args)
        self.assertIn(TEST_OWNER_USER_ID, perm_args)

    def test_env_owner_user_id_grants_edit_permission(self) -> None:
        result = self.run_script(owner_user_id=None, env_owner_user_id="env_owner")

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["permission"]["status"], "granted")
        self.assertEqual(feishu["permission"]["grantedTo"], "user_id:env_owner")
        calls = [json.loads(line) for line in self.capture.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertIn("env_owner", calls[1])

    def test_cli_owner_user_id_overrides_environment_owner(self) -> None:
        result = self.run_script("--owner-user-id", "cli_owner", owner_user_id=None, env_owner_user_id="env_owner")

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["permission"]["status"], "granted")
        self.assertEqual(feishu["permission"]["grantedTo"], "user_id:cli_owner")
        calls = [json.loads(line) for line in self.capture.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertIn("cli_owner", calls[1])
        self.assertNotIn("env_owner", calls[1])

    def test_missing_owner_skips_permission_grant_for_single_publish(self) -> None:
        result = self.run_script(owner_user_id=None, env_owner_user_id=None)

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["status"], "published")
        self.assertEqual(feishu["permission"]["status"], "skipped")
        calls = [json.loads(line) for line in self.capture.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(calls), 1)

    def test_permission_failure_keeps_published_and_records_permission_error(self) -> None:
        result = self.run_script("--fail-permission")

        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["status"], "published")
        self.assertEqual(feishu["documentId"], "DOC123abc")
        self.assertEqual(feishu["permission"]["status"], "failed")
        self.assertIn("fake permission failed", feishu["permission"]["error"])

    def test_cli_failure_writes_failed_error_without_document_url(self) -> None:
        result = self.run_script(fail_cli=True)

        self.assertNotEqual(result.returncode, 0)
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        feishu = metadata["publish"]["feishu"]
        self.assertEqual(feishu["status"], "failed")
        self.assertIn("fake import failed", feishu["error"])
        self.assertNotIn("documentUrl", feishu)
        self.assertTrue((self.output / "publish-report.md").exists())

    def test_does_not_modify_article_titles_cover_or_source_registry(self) -> None:
        before = {
            "article": (self.output / "article.md").read_text(encoding="utf-8"),
            "titles": (self.output / "titles.md").read_text(encoding="utf-8"),
            "cover": (self.output / "images" / "cover.png").read_bytes(),
            "registry": self.registry.read_text(encoding="utf-8"),
        }

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        after = {
            "article": (self.output / "article.md").read_text(encoding="utf-8"),
            "titles": (self.output / "titles.md").read_text(encoding="utf-8"),
            "cover": (self.output / "images" / "cover.png").read_bytes(),
            "registry": self.registry.read_text(encoding="utf-8"),
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
