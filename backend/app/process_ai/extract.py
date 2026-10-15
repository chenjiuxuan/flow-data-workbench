"""把粘贴文字或上传文件收成一段纯文本。不落库。"""
import io
import os
import re
from typing import Protocol

from charset_normalizer import from_bytes
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from .errors import (
    DraftError,
    EMPTY_TEXT,
    FILE_TOO_LARGE,
    PARSE_FAILED,
    SCANNED_PDF,
    TEXT_TOO_LONG,
    UNSUPPORTED_TYPE,
)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
# 有效字符 / 页数低于该值视为扫描件。
MIN_CHARS_PER_PAGE = 20
MAX_PDF_PAGES = 2000
ALLOWED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}


class ReadableUpload(Protocol):
    filename: str | None
    size: int | None

    async def read(self, size: int = -1) -> bytes: ...


def normalize_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    value = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return value.strip()


def prepare_text(value: str) -> str:
    text = normalize_text(value)
    if not text:
        raise DraftError(422, "empty_text", EMPTY_TEXT)
    if len(text) > MAX_TEXT_CHARS:
        raise DraftError(422, "text_too_long", TEXT_TOO_LONG)
    return text


def extension_of(filename: str | None) -> str:
    return os.path.splitext(filename or "")[1].lower()


async def read_limited(upload: ReadableUpload, limit: int = MAX_FILE_BYTES) -> bytes:
    declared = getattr(upload, "size", None)
    if isinstance(declared, int) and declared > limit:
        raise DraftError(413, "file_too_large", FILE_TOO_LARGE)
    buffer = bytearray()
    while True:
        chunk = await upload.read(min(1024 * 1024, limit + 1))
        if not chunk:
            break
        if len(buffer) + len(chunk) > limit:
            raise DraftError(413, "file_too_large", FILE_TOO_LARGE)
        buffer.extend(chunk)
    return bytes(buffer)


def extract_bytes(filename: str | None, data: bytes) -> str:
    extension = extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise DraftError(415, "unsupported_type", UNSUPPORTED_TYPE)
    if extension in {".txt", ".md"}:
        return _decode_text(data)
    if extension == ".docx":
        return _extract_docx(data)
    return _extract_pdf(data)


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        guessed = from_bytes(data).best()
        if guessed is None:
            raise DraftError(422, "parse_failed", PARSE_FAILED)
        return str(guessed)


def _extract_docx(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
        parts: list[str] = []
        for block in _iter_blocks(document):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if text:
                    parts.append(text)
            else:
                parts.extend(_table_lines(block))
    except DraftError:
        raise
    except Exception as exc:
        raise DraftError(422, "parse_failed", PARSE_FAILED) from exc
    return "\n".join(parts)


def _iter_blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _table_lines(table: Table) -> list[str]:
    lines = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        if any(cells):
            lines.append(" | ".join(cells))
    return lines


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            decrypted = reader.decrypt("")
            if decrypted == 0:
                raise DraftError(422, "parse_failed", PARSE_FAILED)
        page_count = len(reader.pages)
        if page_count <= 0:
            raise DraftError(422, "parse_failed", PARSE_FAILED)
        sample = reader.pages[:MAX_PDF_PAGES]
        pieces = [(page.extract_text() or "") for page in sample]
    except DraftError:
        raise
    except Exception as exc:
        raise DraftError(422, "parse_failed", PARSE_FAILED) from exc
    combined = "\n".join(pieces)
    effective = sum(1 for char in combined if not char.isspace())
    if effective / len(sample) < MIN_CHARS_PER_PAGE:
        raise DraftError(422, "scanned_pdf", SCANNED_PDF)
    return combined
