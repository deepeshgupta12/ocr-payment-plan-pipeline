"""
Stage 1 — Ingest.

Accepts a file path (image or PDF) and produces a list of base64-encoded
images ready for the OpenAI vision model.

Key decisions:
  - PDFs are rasterised page-by-page via PyMuPDF (no poppler system
    dependency), but ALL pages are sent in a SINGLE extraction request so a
    milestone table split across pages is seen as one plan, never two
    partial plans.
  - Born-digital PDFs additionally yield a native text layer, passed to the
    model as a cross-check aid (it improves digit accuracy on dense tables).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from . import config


@dataclass
class IngestResult:
    source_path: str
    source_type: str                      # "image" | "pdf"
    page_count: int
    images_b64: list[str] = field(default_factory=list)   # one per page
    image_media_type: str = "image/png"
    native_text: str | None = None        # text layer for born-digital PDFs


class IngestError(Exception):
    pass


def _encode_file_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _media_type_for(ext: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }[ext]


def ingest(path: str) -> IngestResult:
    """Entry point for Stage 1. Raises IngestError on unsupported input."""
    if not os.path.isfile(path):
        raise IngestError(f"File not found: {path}")

    ext = os.path.splitext(path)[1].lower()

    if ext in config.SUPPORTED_IMAGE_EXTENSIONS:
        return IngestResult(
            source_path=path,
            source_type="image",
            page_count=1,
            images_b64=[_encode_file_b64(path)],
            image_media_type=_media_type_for(ext),
        )

    if ext in config.SUPPORTED_PDF_EXTENSIONS:
        return _ingest_pdf(path)

    raise IngestError(
        f"Unsupported file type '{ext}'. Supported: "
        f"{sorted(config.SUPPORTED_IMAGE_EXTENSIONS | config.SUPPORTED_PDF_EXTENSIONS)}"
    )


def _ingest_pdf(path: str) -> IngestResult:
    doc = fitz.open(path)
    try:
        page_count = doc.page_count
        if page_count == 0:
            raise IngestError("PDF has no pages.")
        if page_count > config.MAX_PDF_PAGES:
            raise IngestError(
                f"PDF has {page_count} pages; max supported is "
                f"{config.MAX_PDF_PAGES}. Route to manual triage."
            )

        zoom = config.PDF_RENDER_DPI / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        images: list[str] = []
        text_parts: list[str] = []
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            images.append(base64.b64encode(pix.tobytes("png")).decode("utf-8"))
            text_parts.append(page.get_text("text").strip())

        native_text = "\n\n--- PAGE BREAK ---\n\n".join(t for t in text_parts if t)
        return IngestResult(
            source_path=path,
            source_type="pdf",
            page_count=page_count,
            images_b64=images,
            image_media_type="image/png",
            native_text=native_text or None,   # None => scanned PDF
        )
    finally:
        doc.close()
