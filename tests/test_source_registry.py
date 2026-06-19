import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCAN = SKILL_DIR / "scripts" / "scan_docx_materials.py"
NORMALIZE = SKILL_DIR / "scripts" / "normalize_docx_material.py"
MARK = SKILL_DIR / "scripts" / "mark_source_usage.py"


def write_docx(path: Path, title: str, body: str) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>{title}</w:t></w:r></w:p>
    <w:p><w:r><w:t>{body}</w:t></w:r></w:p>
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


class SourceRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "ContentFactoryVault"
        (self.vault / "01-Materials" / "docx-raw").mkdir(parents=True)
        (self.vault / "01-Materials" / "rewrite-sources").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(script), "--vault", str(self.vault), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def read_registry(self) -> list[dict]:
        return json.loads((self.vault / "01-Materials" / "source-registry.json").read_text())

    def test_scan_initializes_registry_and_flags_duplicate_content_hashes(self) -> None:
        raw = self.vault / "01-Materials" / "docx-raw"
        write_docx(raw / "a.docx", "同一标题", "同一正文")
        write_docx(raw / "b.docx", "同一标题", "同一正文")

        result = self.run_script(SCAN)

        self.assertEqual(result.returncode, 0, result.stderr)
        registry = self.read_registry()
        self.assertEqual(len(registry), 2)
        self.assertEqual({item["status"] for item in registry}, {"unused"})
        self.assertEqual(registry[0]["contentHash"], registry[1]["contentHash"])
        self.assertTrue(registry[0]["notes"])
        self.assertIn("possible_duplicate", registry[0]["notes"][0])
        self.assertTrue(registry[0]["sourceId"].startswith("source-"))

    def test_normalize_updates_registry_and_markdown_frontmatter(self) -> None:
        source = self.vault / "01-Materials" / "docx-raw" / "source.docx"
        write_docx(source, "素材标题", "素材正文")
        self.assertEqual(self.run_script(SCAN).returncode, 0)

        result = self.run_script(NORMALIZE, "--file", str(source))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        markdown = Path(payload["markdown_path"]).read_text()
        registry = self.read_registry()
        item = registry[0]
        self.assertEqual(item["status"], "normalized")
        self.assertEqual(item["normalizedPath"], payload["markdown_path"])
        self.assertEqual(item["title"], "素材标题")
        self.assertIn(f"sourceId: {item['sourceId']}", markdown)
        self.assertIn("sourceStatus: normalized", markdown)
        self.assertIn("contentHash: sha256:", markdown)
        self.assertIn("usedAt:", markdown)
        self.assertIn("usedByOutput:", markdown)

    def test_mark_source_usage_updates_registry_and_frontmatter(self) -> None:
        source = self.vault / "01-Materials" / "docx-raw" / "source.docx"
        write_docx(source, "素材标题", "素材正文")
        self.assertEqual(self.run_script(SCAN).returncode, 0)
        self.assertEqual(self.run_script(NORMALIZE, "--file", str(source)).returncode, 0)
        item = self.read_registry()[0]

        result = self.run_script(
            MARK,
            "--source-id",
            item["sourceId"],
            "--status",
            "used",
            "--output",
            "04-Outputs/2026-05-19-test",
            "--profile",
            "profile-ahong-running-rewrite",
            "--corpus",
            "corpus-ahong-running-style",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = self.read_registry()[0]
        self.assertEqual(updated["status"], "used")
        self.assertEqual(updated["usedByOutput"], "04-Outputs/2026-05-19-test")
        self.assertEqual(updated["profile"], "profile-ahong-running-rewrite")
        self.assertEqual(updated["corpus"], "corpus-ahong-running-style")
        markdown = Path(updated["normalizedPath"]).read_text()
        self.assertIn("sourceStatus: used", markdown)
        self.assertIn("usedByOutput: 04-Outputs/2026-05-19-test", markdown)

    def test_normalizing_used_source_does_not_downgrade_registry_status(self) -> None:
        source = self.vault / "01-Materials" / "docx-raw" / "source.docx"
        write_docx(source, "素材标题", "素材正文")
        self.assertEqual(self.run_script(SCAN).returncode, 0)
        self.assertEqual(self.run_script(NORMALIZE, "--file", str(source)).returncode, 0)
        item = self.read_registry()[0]
        self.assertEqual(
            self.run_script(
                MARK,
                "--source-id",
                item["sourceId"],
                "--status",
                "used",
                "--output",
                "04-Outputs/used-once",
            ).returncode,
            0,
        )

        result = self.run_script(NORMALIZE, "--file", str(source))

        self.assertEqual(result.returncode, 0, result.stderr)
        updated = self.read_registry()[0]
        self.assertEqual(updated["status"], "used")
        self.assertEqual(updated["usedByOutput"], "04-Outputs/used-once")


if __name__ == "__main__":
    unittest.main()
