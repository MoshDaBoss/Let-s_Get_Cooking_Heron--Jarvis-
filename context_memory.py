from __future__ import annotations

import re
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET


def _clean_text(raw_text: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw_text or "").strip()
    return cleaned


def _summarize_text(text: str, max_chars: int = 1200) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return "No readable text found in this document."
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _extract_docx_details(file_path: Path) -> dict:
    image_names: list[str] = []
    text_parts: list[str] = []

    with zipfile.ZipFile(file_path, "r") as archive:
        names = archive.namelist()

        for name in names:
            lower = name.lower()
            if lower.startswith("word/media/") and lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".wmf", ".emf")):
                image_names.append(Path(name).name)

        if "word/document.xml" in names:
            xml_bytes = archive.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for text_node in root.findall(".//w:t", ns):
                if text_node.text:
                    text_parts.append(text_node.text)

        if not image_names and "word/_rels/document.xml.rels" in names:
            rels = archive.read("word/_rels/document.xml.rels").decode("utf-8", errors="ignore")
            image_names = [Path(match).name for match in re.findall(r'Target="([^"]+)"', rels) if match.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".wmf", ".emf"))]

    text = " ".join(text_parts)
    summary = _summarize_text(text)
    return {"summary": summary, "image_summary": image_names or ["embedded document image(s) detected"]}


def _extract_pdf_details(file_path: Path) -> dict:
    try:
        from pypdf import PdfReader
    except Exception:
        PdfReader = None

    image_names: list[str] = []
    text_parts: list[str] = []

    if PdfReader is not None:
        try:
            reader = PdfReader(str(file_path))
            for page in reader.pages:
                extracted = page.extract_text() or ""
                if extracted:
                    text_parts.append(extracted)
        except Exception:
            text_parts = []

    raw_bytes = file_path.read_bytes(errors="ignore") if hasattr(file_path.read_bytes, "__call__") else b""
    if b"/XObject" in raw_bytes or b"/Image" in raw_bytes:
        image_names.append("embedded image(s) detected")

    matches = re.findall(rb"/(?:[A-Za-z0-9_]+)\s+\d+\s+\d+\s+R", raw_bytes)
    if matches:
        image_names = [match.decode("utf-8", errors="ignore").strip("/") for match in matches[:10]]

    text = "\n".join(text_parts)
    summary = _summarize_text(text)
    if not summary or summary == "No readable text found in this document.":
        summary = "The PDF appears to contain visual content or text that could not be parsed automatically."

    return {"summary": summary, "image_summary": image_names or ["embedded PDF image(s) detected"]}


def summarize_document_context(file_path: str | Path) -> dict:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="ignore")
        return {"summary": _summarize_text(text), "image_summary": []}

    if suffix in {".doc", ".docx"}:
        if suffix == ".doc":
            return {"summary": "Legacy .doc files are not parsed directly in this app; please convert to .docx or paste the text manually.", "image_summary": []}
        return _extract_docx_details(path)

    if suffix == ".pdf":
        return _extract_pdf_details(path)

    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    return {"summary": _summarize_text(text), "image_summary": []}
