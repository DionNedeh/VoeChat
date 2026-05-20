from __future__ import annotations

import os
import zipfile
from pathlib import Path

try:
    import defusedxml.ElementTree as SafeET
except Exception:
    import xml.etree.ElementTree as SafeET


class DocumentService:
    MAX_UPLOAD_BYTES = 25 * 1024 * 1024
    MAX_PDF_PAGES = 100
    MAX_EXTRACTED_CHARS = 300_000

    def process(self, path: str) -> dict:
        file_path = Path(path)
        self._validate_upload_file(file_path)
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
    def _validate_upload_file(path: Path) -> None:
        if not path.is_file():
            raise ValueError("Upload path is not a file.")
        if path.stat().st_size > DocumentService.MAX_UPLOAD_BYTES:
            raise ValueError("Upload file is too large.")

    @staticmethod
    def _read_text(path: Path) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return path.read_text(encoding=encoding)[:DocumentService.MAX_EXTRACTED_CHARS]
            except UnicodeDecodeError:
                continue
        return path.read_text(errors="replace")[:DocumentService.MAX_EXTRACTED_CHARS]

    @staticmethod
    def _read_pdf(path: Path) -> str:
        errors: list[str] = []
        try:
            import fitz  # PyMuPDF

            parts = []
            with fitz.open(path) as doc:
                if doc.page_count > DocumentService.MAX_PDF_PAGES:
                    raise ValueError("PDF has too many pages.")
                for page in doc:
                    parts.append(page.get_text("text"))
                    if sum(len(part) for part in parts) >= DocumentService.MAX_EXTRACTED_CHARS:
                        break
            text = "\n".join(parts).strip()
            if text:
                return text[:DocumentService.MAX_EXTRACTED_CHARS]
        except Exception as exc:
            errors.append(str(exc))
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path), strict=False)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported.")
            if len(reader.pages) > DocumentService.MAX_PDF_PAGES:
                raise ValueError("PDF has too many pages.")
            parts = []
            for page in reader.pages[:DocumentService.MAX_PDF_PAGES]:
                parts.append(page.extract_text() or "")
                if sum(len(part) for part in parts) >= DocumentService.MAX_EXTRACTED_CHARS:
                    break
            text = "\n".join(parts).strip()
            if text:
                return text[:DocumentService.MAX_EXTRACTED_CHARS]
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
        if len(xml) > DocumentService.MAX_EXTRACTED_CHARS:
            raise ValueError("DOCX document XML is too large.")
        root = SafeET.fromstring(xml)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", ns):
            pieces = [node.text or "" for node in paragraph.findall(".//w:t", ns)]
            if pieces:
                paragraphs.append("".join(pieces))
        return "\n".join(paragraphs)
