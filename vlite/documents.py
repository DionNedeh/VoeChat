from __future__ import annotations

import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


class DocumentService:
    def process(self, path: str) -> dict:
        file_path = Path(path)
        ext = file_path.suffix.lower()
        if ext in {".txt", ".md", ".markdown", ".csv", ".json", ".py", ".js", ".html", ".css"}:
            text = self._read_text(file_path)
        elif ext == ".pdf":
            text = self._read_pdf(file_path)
        elif ext == ".docx":
            text = self._read_docx(file_path)
        else:
            raise ValueError(f"Unsupported upload type: {ext or 'unknown'}")
        text = (text or "").strip()
        if not text:
            raise ValueError("No extractable text was found.")
        return {
            "name": file_path.name,
            "path": str(file_path),
            "text": text,
            "tokens": max(1, int(len(text) / 4)),
            "kind": "document",
            "bytes": os.path.getsize(file_path),
        }

    @staticmethod
    def _read_text(path: Path) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(errors="replace")

    @staticmethod
    def _read_pdf(path: Path) -> str:
        errors: list[str] = []
        try:
            import fitz  # PyMuPDF

            parts = []
            with fitz.open(path) as doc:
                for page in doc:
                    parts.append(page.get_text("text"))
            text = "\n".join(parts).strip()
            if text:
                return text
        except Exception as exc:
            errors.append(str(exc))
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            if text:
                return text
        except Exception as exc:
            errors.append(str(exc))
        if errors:
            raise ValueError("No extractable text was found.")
        return ""

    @staticmethod
    def _read_docx(path: Path) -> str:
        try:
            import docx

            doc = docx.Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs).strip()
            if text:
                return text
        except Exception:
            pass

        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", ns):
            pieces = [node.text or "" for node in paragraph.findall(".//w:t", ns)]
            if pieces:
                paragraphs.append("".join(pieces))
        return "\n".join(paragraphs)
