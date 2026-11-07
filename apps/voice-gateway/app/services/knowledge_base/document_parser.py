"""Document parser for knowledge-base ingestion (Day 6).

Supports .md, .txt, and .pdf files. Extracts plain text with section and page
metadata. Rejects unsupported formats and empty/unreadable documents clearly.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedDocument:
    """One parsed document with extracted text and metadata."""

    source_filename: str
    source_type: str  # "markdown" | "text" | "pdf"
    title: str  # extracted from first heading or filename
    content: str  # full extracted text
    sections: list[tuple[str, int | None, int, int]]  # (section_title, page, start_char, end_char)
    content_sha256: str
    parse_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing one document."""

    document: ParsedDocument | None
    error: str | None = None


class DocumentParser:
    """Parses .md, .txt, and .pdf documents into structured text."""

    SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}

    def parse(self, filename: str, data: bytes) -> ParseResult:
        """Parse one document file.

        Returns a ParsedDocument on success, or a ParseResult with an error
        message on failure. Never raises — callers check `document is None`.
        """
        ext = self._extension(filename)
        if ext not in self.SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(self.SUPPORTED_EXTENSIONS))
            return ParseResult(
                document=None,
                error=f"unsupported file format: {ext} (supported: {supported})",
            )

        try:
            if ext == ".md":
                return self._parse_markdown(filename, data)
            elif ext == ".txt":
                return self._parse_text(filename, data)
            elif ext == ".pdf":
                return self._parse_pdf(filename, data)
        except Exception as exc:  # noqa: BLE001 — parse errors are reported, not raised
            return ParseResult(document=None, error=f"parse failed: {exc}")

        return ParseResult(document=None, error=f"unknown file format: {ext}")

    # --- internal parsers ---------------------------------------------------

    def _parse_markdown(self, filename: str, data: bytes) -> ParseResult:
        text = data.decode("utf-8", errors="replace")
        title = self._extract_title_from_markdown(text)
        sections = self._extract_markdown_sections(text)
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ParseResult(
            document=ParsedDocument(
                source_filename=filename,
                source_type="markdown",
                title=title,
                content=text,
                sections=sections,
                content_sha256=content_sha256,
            )
        )

    def _parse_text(self, filename: str, data: bytes) -> ParseResult:
        text = data.decode("utf-8", errors="replace")
        title = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
        sections = [(title, None, 0, len(text))] if text.strip() else []
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ParseResult(
            document=ParsedDocument(
                source_filename=filename,
                source_type="text",
                title=title,
                content=text,
                sections=sections,
                content_sha256=content_sha256,
            )
        )

    def _parse_pdf(self, filename: str, data: bytes) -> ParseResult:
        """Parse PDF using pypdf if available; otherwise report unsupported."""
        try:
            from pypdf import PdfReader
        except ImportError:
            return ParseResult(
                document=None,
                error="PDF parsing requires pypdf (not installed)",
            )

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return ParseResult(
                document=None,
                error="encrypted PDFs are not supported",
            )

        pages_text: list[str] = []
        sections: list[tuple[str, int | None, int, int]] = []
        char_offset = 0

        for page_num, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text.strip():
                pages_text.append(page_text)
                # Try to find a heading on the page
                section_title = self._guess_section_title(page_text)
                sections.append(
                    (section_title, page_num, char_offset, char_offset + len(page_text))
                )
            char_offset += len(page_text)

        full_text = "\n\n".join(pages_text)
        if not full_text.strip():
            return ParseResult(
                document=None,
                error="PDF contains no extractable text (may be image-based)",
            )

        title = sections[0][0] if sections else filename.rsplit(".", 1)[0].replace("_", " ").title()
        content_sha256 = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        return ParseResult(
            document=ParsedDocument(
                source_filename=filename,
                source_type="pdf",
                title=title,
                content=full_text,
                sections=sections,
                content_sha256=content_sha256,
            )
        )

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _extension(filename: str) -> str:
        return "." + filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    @staticmethod
    def _extract_title_from_markdown(text: str) -> str:
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
        # Fall back to first non-empty line
        for line in text.split("\n"):
            line = line.strip()
            if line:
                return line[:80]
        return "Untitled Document"

    @staticmethod
    def _guess_section_title(text: str) -> str | None:
        """Guess a section title from the first non-empty line of a page."""
        for line in text.split("\n"):
            line = line.strip()
            if line and len(line) < 100:
                return line
        return None

    @staticmethod
    def _extract_markdown_sections(text: str) -> list[tuple[str, int | None, int, int]]:
        """Split markdown text into sections based on heading lines.

        Returns a list of (section_title, page_number, start_char, end_char)
        tuples. Page number is always None for markdown (no pagination).
        """
        sections: list[tuple[str, int | None, int, int]] = []
        lines = text.split("\n")
        current_start = 0
        current_title = None

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# "):
                # Save previous section
                if current_title is not None:
                    sections.append((current_title, None, current_start, i))
                current_title = stripped[2:].strip()
                current_start = i
            elif stripped.startswith("## "):
                # Sub-heading: treat as part of current section
                pass

        # Add final section
        if current_title is not None:
            sections.append((current_title, None, current_start, len(text)))
        elif lines:
            # No headings found; treat entire document as one section
            first_line = lines[0].strip() if lines else ""
            first_title = first_line[:80] if first_line else "Untitled Document"
            sections.append((first_title, None, 0, len(text)))

        return sections


__all__ = [
    "DocumentParser",
    "ParsedDocument",
    "ParseResult",
]
