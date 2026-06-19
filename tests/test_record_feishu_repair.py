import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "record_feishu_repair.py"


class RecordFeishuRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.output = Path(self.tmp.name) / "2026-06-19-output"
        self.output.mkdir(parents=True)
        self.write_metadata()
        (self.output / "repairs").mkdir()
        (self.output / "repairs" / "cover.md").write_text("# repair", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_metadata(self, repairs=None) -> None:
        feishu = {
            "status": "published",
            "documentId": "DOC123",
            "documentUrl": "https://feishu.cn/docx/DOC123",
            "requiresRemoteCheck": False,
        }
        if repairs is not None:
            feishu["repairs"] = repairs
        metadata = {"title": "测试", "publish": {"feishu": feishu}}
        (self.output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_script(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                str(self.output),
                "--type",
                "cover_repair",
                "--status",
                "closed",
                "--document-id",
                "DOC123",
                "--document-url",
                "https://feishu.cn/docx/DOC123",
                "--notes-path",
                "repairs/cover.md",
                "--image-upload-result",
                "0/1",
                "--cover-uploaded",
                "false",
                "--blocks-count",
                "64",
                "--image-blocks-count",
                "2",
                "--remote-cover-visible",
                "true",
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def read_feishu(self):
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        return metadata["publish"]["feishu"]

    def test_appends_first_repair_without_changing_publish_state(self) -> None:
        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        feishu = self.read_feishu()
        self.assertEqual(feishu["status"], "published")
        self.assertEqual(feishu["documentId"], "DOC123")
        self.assertEqual(feishu["documentUrl"], "https://feishu.cn/docx/DOC123")
        self.assertFalse(feishu["requiresRemoteCheck"])
        self.assertEqual(len(feishu["repairs"]), 1)
        repair = feishu["repairs"][0]
        self.assertEqual(repair["type"], "cover_repair")
        self.assertEqual(repair["status"], "closed")
        self.assertTrue(repair["createdAt"])
        self.assertEqual(repair["source"], "manual_reconciliation")
        self.assertEqual(repair["documentId"], "DOC123")
        self.assertEqual(repair["documentUrl"], "https://feishu.cn/docx/DOC123")
        self.assertEqual(repair["originalIssue"], {"imageUploadResult": "0/1", "coverUploaded": False})
        self.assertEqual(
            repair["repairAction"],
            {"method": "feishu-cli doc add --index 0 --upload-images", "target": "cover"},
        )
        self.assertEqual(
            repair["verification"],
            {
                "method": "feishu-cli doc blocks read-only",
                "blocksCount": 64,
                "imageBlocksCount": 2,
                "remoteCoverVisible": True,
            },
        )
        self.assertEqual(repair["notesPath"], "repairs/cover.md")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["repairCount"], 1)
        self.assertEqual(payload["action"], "appended")

    def test_appends_second_repair_without_overwriting_existing_repairs(self) -> None:
        existing = [{"type": "permission_repair", "notesPath": "repairs/permission.md", "status": "closed"}]
        self.write_metadata(repairs=existing)

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        repairs = self.read_feishu()["repairs"]
        self.assertEqual(len(repairs), 2)
        self.assertEqual(repairs[0], existing[0])
        self.assertEqual(repairs[1]["type"], "cover_repair")

    def test_duplicate_type_and_notes_path_fails_by_default(self) -> None:
        self.write_metadata(repairs=[{"type": "cover_repair", "notesPath": "repairs/cover.md", "status": "open"}])

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        repairs = self.read_feishu()["repairs"]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["status"], "open")

    def test_replace_updates_matching_repair(self) -> None:
        self.write_metadata(repairs=[{"type": "cover_repair", "notesPath": "repairs/cover.md", "status": "open"}])

        result = self.run_script("--replace")

        self.assertEqual(result.returncode, 0, result.stderr)
        repairs = self.read_feishu()["repairs"]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["status"], "closed")
        self.assertEqual(repairs[0]["verification"]["imageBlocksCount"], 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["action"], "replaced")

    def test_missing_metadata_fails(self) -> None:
        (self.output / "metadata.json").unlink()

        result = self.run_script()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata.json not found", result.stderr)

    def test_absolute_notes_path_is_stored_relative_to_output_dir(self) -> None:
        absolute_notes = self.output / "repairs" / "cover.md"

        result = self.run_script("--notes-path", str(absolute_notes))

        self.assertEqual(result.returncode, 0, result.stderr)
        repair = self.read_feishu()["repairs"][0]
        self.assertEqual(repair["notesPath"], "repairs/cover.md")
        self.assertFalse(Path(repair["notesPath"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
