from zipfile import ZipFile

from context_memory import summarize_document_context


def test_extracts_text_from_txt_file(tmp_path):
    file_path = tmp_path / "project_notes.txt"
    file_path.write_text("This project plan covers baking bread and meal prep for the week.\n", encoding="utf-8")

    result = summarize_document_context(file_path)

    assert "project plan" in result["summary"].lower()
    assert result["image_summary"] == []


def test_extracts_docx_text_and_image_names(tmp_path):
    file_path = tmp_path / "brief.docx"

    with ZipFile(file_path, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
  <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
  <Default Extension=\"xml\" ContentType=\"application/xml\"/>
  <Default Extension=\"png\" ContentType=\"image/png\"/>
  <Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>
</Types>
""")
        archive.writestr("_rels/.rels", """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/>
</Relationships>
""")
        archive.writestr("word/document.xml", """<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>
<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
  <w:body>
    <w:p><w:r><w:t>Budget meeting notes</w:t></w:r></w:p>
    <w:p><w:r><w:t>Team will review Q3 revenue and product launch plan.</w:t></w:r></w:p>
  </w:body>
</w:document>
""")
        archive.writestr("word/media/image1.png", b"fakepngcontent")

    result = summarize_document_context(file_path)

    assert "budget meeting" in result["summary"].lower()
    assert "image1.png" in result["image_summary"][0].lower()
