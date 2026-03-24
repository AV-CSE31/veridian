"""
veridian.verify.builtin.quote
─────────────────────────────
QuoteMatchVerifier — verify that a quoted excerpt from result.structured
actually appears verbatim (modulo whitespace normalisation) in a source document.

Supported formats: .txt, .md, .pdf (pypdf), .docx (python-docx).

Usage:
    verifier_id="quote_match"
    verifier_config={
        "source_file": "contracts/001.pdf",
        "quote_field": "quote",    # field in result.structured
        "min_quote_length": 10,
    }
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult


def _normalise(text: str) -> str:
    """Collapse all whitespace runs to single space and strip edges."""
    return re.sub(r"\s+", " ", text).strip()


def _read_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as err:
        raise VeridianConfigError(
            "pypdf is required for PDF quote matching. Run: pip install pypdf"
        ) from err
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _read_docx(path: Path) -> str:
    try:
        import docx  # noqa: PLC0415
    except ImportError as err:
        raise VeridianConfigError(
            "python-docx is required for .docx quote matching. Run: pip install python-docx"
        ) from err
    doc = docx.Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _read_source(path: Path) -> str:
    """Read source file and return its full text content."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    # .txt, .md, and anything else treated as plain text
    return _read_txt(path)


class QuoteMatchVerifier(BaseVerifier):
    """
    Verify that result.structured[quote_field] appears verbatim in source_file.

    Whitespace is normalised for matching (multiple spaces → single space).
    Supports PDF, DOCX, TXT, MD.
    """

    id: ClassVar[str] = "quote_match"
    description: ClassVar[str] = (
        "Verify that a quoted excerpt from structured output appears verbatim "
        "in the source document (PDF, DOCX, TXT, MD). Whitespace-normalised match."
    )

    def __init__(
        self,
        source_file: str,
        quote_field: str = "quote",
        min_quote_length: int = 10,
    ) -> None:
        """
        Args:
            source_file: Path to the source document. Required.
            quote_field: Key in result.structured containing the quote. Default "quote".
            min_quote_length: Minimum accepted quote length in characters. Must be ≥ 1.
        """
        if not source_file or not source_file.strip():
            raise VeridianConfigError(
                "QuoteMatchVerifier: 'source_file' must not be empty."
            )
        if min_quote_length < 1:
            raise VeridianConfigError(
                f"QuoteMatchVerifier: 'min_quote_length' must be ≥ 1, got {min_quote_length}."
            )
        self.source_file = source_file
        self.quote_field = quote_field
        self.min_quote_length = min_quote_length

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Check that quote appears in source file."""
        quote: str | None = result.structured.get(self.quote_field)

        if not quote or not isinstance(quote, str):
            return VerificationResult(
                passed=False,
                error=(
                    f"Field '{self.quote_field}' missing or empty in structured output. "
                    f"Add the verbatim quote to '{self.quote_field}'."
                )[:300],
            )

        if len(quote.strip()) < self.min_quote_length:
            return VerificationResult(
                passed=False,
                error=(
                    f"Quote is too short ({len(quote.strip())} chars, "
                    f"min {self.min_quote_length}). Provide a longer verbatim excerpt."
                )[:300],
            )

        path = Path(self.source_file)
        if not path.exists():
            return VerificationResult(
                passed=False,
                error=(
                    f"Source file not found: {self.source_file}. "
                    f"Check the file path is correct."
                )[:300],
            )

        try:
            source_text = _read_source(path)
        except Exception as exc:
            return VerificationResult(
                passed=False,
                error=f"Could not read source file '{path.name}': {str(exc)[:100]}"[:300],
            )

        norm_source = _normalise(source_text)
        norm_quote = _normalise(quote)

        if norm_quote in norm_source:
            return VerificationResult(
                passed=True,
                evidence={
                    "quote_field": self.quote_field,
                    "source_file": self.source_file,
                    "quote_length": len(norm_quote),
                },
            )

        return VerificationResult(
            passed=False,
            error=(
                f"Quote not found verbatim in {Path(self.source_file).name}. "
                f"First 80 chars: '{quote[:80]}'. "
                f"Ensure the quote is copied exactly from the source."
            )[:300],
        )
