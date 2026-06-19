import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "normalize_docx_material.py"


def write_docx(path: Path, document_xml: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""")
        archive.writestr("word/document.xml", document_xml)


def w_p(text: str, style: str | None = None, extra: str = "") -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{style_xml}{extra}<w:r><w:t>{text}</w:t></w:r></w:p>"


class NormalizeDocxMaterialTest(unittest.TestCase):
    def test_converts_docx_to_clean_markdown_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "ContentFactoryVault"
            raw_dir = vault / "01-Materials" / "docx-raw"
            out_dir = vault / "01-Materials" / "rewrite-sources"
            raw_dir.mkdir(parents=True)
            out_dir.mkdir(parents=True)
            source = raw_dir / "跑步素材.docx"
            document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <w:body>
    {w_p("页眉：不要进入正文")}
    {w_p("跑步素材标题", "Title")}
    {w_p("第一部分", "Heading1")}
    {w_p("这是第一段正文。")}
    {w_p("这是第二段正文。")}
    {w_p("列表项", extra="<w:pPr><w:numPr><w:ilvl w:val=\"0\"/><w:numId w:val=\"1\"/></w:numPr></w:pPr>")}
    {w_p("引用内容", "Quote")}
    <w:p><w:r><w:drawing><a:blip/></w:drawing></w:r></w:p>
    <w:tbl><w:tr><w:tc>{w_p("表格内容")}</w:tc></w:tr></w:tbl>
    {w_p("第 1 页")}
  </w:body>
</w:document>
"""
            write_docx(source, document_xml)

            result = subprocess.run(
                ["python3", str(SCRIPT), "--vault", str(vault), "--file", str(source)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            markdown_path = Path(payload["markdown_path"])
            note_path = Path(payload["cleaning_note_path"])
            markdown = markdown_path.read_text(encoding="utf-8")
            note = note_path.read_text(encoding="utf-8")

            self.assertIn("# 跑步素材标题", markdown)
            self.assertIn("## 第一部分", markdown)
            self.assertIn("这是第一段正文。", markdown)
            self.assertIn("- 列表项", markdown)
            self.assertIn("> 引用内容", markdown)
            self.assertIn("[图片占位：原文此处有图片]", markdown)
            self.assertNotIn("页眉", markdown)
            self.assertNotIn("第 1 页", markdown)
            self.assertIn("是否发现图片：是", note)
            self.assertIn("是否发现表格：是", note)
            self.assertIn("是否可以进入仿写流程：是", note)

    def test_removes_wechat_article_chrome_from_normalized_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "ContentFactoryVault"
            raw_dir = vault / "01-Materials" / "docx-raw"
            raw_dir.mkdir(parents=True)
            source = raw_dir / "公众号素材.docx"
            document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {w_p("为什么跑步老手偏爱10公里", "Title")}
    {w_p("原创 跑步老王 跑步指南")}
    {w_p("2025年03月24日 11:02 浙江")}
    {w_p("点击👇下方小卡片关注")}
    {w_p("真正正文第一段。")}
    {w_p("真正正文第二段。")}
    {w_p("●低心率跑步，8 成跑者都错过的超值跑法")}
    {w_p("阅读")}
    {w_p("微信扫一扫关注该公众号")}
    {w_p("取消 允许")}
  </w:body>
</w:document>
"""
            write_docx(source, document_xml)

            result = subprocess.run(
                ["python3", str(SCRIPT), "--vault", str(vault), "--file", str(source)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            markdown = Path(payload["markdown_path"]).read_text(encoding="utf-8")
            self.assertIn("真正正文第一段。", markdown)
            self.assertNotIn("原创 跑步老王", markdown)
            self.assertNotIn("2025年03月24日", markdown)
            self.assertNotIn("点击👇", markdown)
            self.assertNotIn("低心率跑步", markdown)
            self.assertNotIn("微信扫一扫", markdown)
            self.assertNotIn("取消 允许", markdown)

    def test_removes_author_lines_and_leading_cover_image_but_keeps_body_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "ContentFactoryVault"
            raw_dir = vault / "01-Materials" / "docx-raw"
            raw_dir.mkdir(parents=True)
            source = raw_dir / "首图素材.docx"
            document_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <w:body>
    {w_p("跑步距离怎么选", "Title")}
    {w_p("跑步老王")}
    <w:p><w:r><w:drawing><a:blip/></w:drawing></w:r></w:p>
    {w_p("这是正文第一段。")}
    {w_p("这是正文第二段。")}
    <w:p><w:r><w:drawing><a:blip/></w:drawing></w:r></w:p>
    {w_p("这是图片后的正文。")}
  </w:body>
</w:document>
"""
            write_docx(source, document_xml)

            result = subprocess.run(
                ["python3", str(SCRIPT), "--vault", str(vault), "--file", str(source)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            markdown = Path(payload["markdown_path"]).read_text(encoding="utf-8")
            note = Path(payload["cleaning_note_path"]).read_text(encoding="utf-8")
            self.assertNotIn("跑步老王", markdown)
            self.assertEqual(markdown.count("[图片占位：原文此处有图片]"), 1)
            self.assertIn("这是正文第一段。", markdown)
            self.assertIn("这是图片后的正文。", markdown)
            self.assertIn("是否清理作者行：是", note)
            self.assertIn("是否清理首图占位：是", note)
            self.assertIn("是否清理公众号尾部污染：否", note)


if __name__ == "__main__":
    unittest.main()
