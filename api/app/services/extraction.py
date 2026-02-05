from __future__ import annotations

from io import BytesIO
import logging
import re

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_text(mime_type: str, content: bytes) -> str:
    if mime_type == "application/pdf":
        return _extract_pdf(content)
    if mime_type in {"text/plain", "text/markdown"}:
        return _normalize_text(content.decode("utf-8", errors="ignore"))
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx(content)
    raise ValueError("Unsupported content type")


def _extract_pdf(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    parts: list[str] = []
    empty_pages = 0
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("PDF extraction failed on page %s: %s", idx, exc)
            text = ""
        if not text.strip():
            empty_pages += 1
            continue
        parts.append(f"[PAGE {idx}]\n{text}")
    if empty_pages:
        logger.warning(
            "PDF extraction: %s/%s empty pages (possible scans or complex layout).",
            empty_pages,
            len(reader.pages),
        )
    return _normalize_text("\n\n".join(parts))


def _extract_docx(content: bytes) -> str:
    doc = Document(BytesIO(content))
    body_text = _extract_container_text(doc)
    header_footer = _extract_headers_footers(doc)
    combined = "\n\n".join([t for t in [body_text, header_footer] if t])
    return _normalize_text(combined)


def _normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _iter_block_items(parent):
    parent_elm = parent.element.body if hasattr(parent, "element") else parent._element
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _extract_container_text(container) -> str:
    parts: list[str] = []
    for block in _iter_block_items(container):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                parts.append(text)
        elif isinstance(block, Table):
            table_text = _table_to_text(block)
            if table_text:
                parts.append(table_text)
    return "\n".join(parts)


def _table_to_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cell_text = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            cell_text = cell_text.strip()
            cells.append(cell_text)
        row_text = " | ".join(c for c in cells if c)
        if row_text:
            rows.append(row_text)
    return "\n".join(rows)


def _extract_headers_footers(doc: Document) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for section in doc.sections:
        for container in (section.header, section.footer):
            text = _extract_container_text(container)
            if not text:
                continue
            norm = re.sub(r"\s+", " ", text).strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                parts.append(text)
    return "\n".join(parts)
