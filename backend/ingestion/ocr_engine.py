"""
OCR stage: raw file bytes -> per-page text + confidence.
Interface is swappable so Dev 2 can start immediately without a hard
external dependency, and §7's "per-page confidence" contract is enforced
at the type level (PageOCRResult always carries a confidence).
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import List

from ingestion.models import PageOCRResult


class OCREngine(ABC):
    @abstractmethod
    def run(self, document_id: str, file_bytes: bytes) -> List[PageOCRResult]:
        ...


class StubOCREngine(OCREngine):
    """
    Deterministic stand-in for local dev and fixture generation. Splits on
    form-feed page-break markers if the source is already text, otherwise
    treats the whole blob as one page. Confidence is derived deterministically
    from content so re-runs are reproducible in tests/fixtures.
    """

    def run(self, document_id: str, file_bytes: bytes) -> List[PageOCRResult]:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1", errors="replace")

        pages = text.split("\x0c") if "\x0c" in text else [text]
        results = []
        for i, page_text in enumerate(pages, start=1):
            confidence = self._pseudo_confidence(page_text)
            results.append(
                PageOCRResult(
                    document_id=document_id, page_number=i,
                    text=page_text.strip(), confidence=confidence, engine="stub-ocr",
                )
            )
        return results

    @staticmethod
    def _pseudo_confidence(page_text: str) -> float:
        if not page_text.strip():
            return 0.0
        h = int(hashlib.sha256(page_text.encode("utf-8")).hexdigest(), 16)
        return round(0.55 + (h % 4501) / 10000, 4)  # deterministic, in [0.55, 1.0]


class TesseractOCREngine(OCREngine):
    """
    Real engine — wraps pytesseract + pdf2image. Swapping StubOCREngine for
    this in production is a one-line change in ingestion_pipeline.py.
    """

    def run(self, document_id: str, file_bytes: bytes) -> List[PageOCRResult]:
        import pytesseract
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(file_bytes)
        results = []
        for i, image in enumerate(images, start=1):
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data["conf"] if c not in ("-1", -1)]
            avg_conf = (sum(confidences) / len(confidences) / 100) if confidences else 0.0
            text = pytesseract.image_to_string(image)
            results.append(
                PageOCRResult(
                    document_id=document_id, page_number=i, text=text.strip(),
                    confidence=round(avg_conf, 4), engine="tesseract",
                )
            )
        return results


class UniversalOCREngine(OCREngine):
    """
    Format-sniffed local extraction — no filename is available (`run` gets bytes only),
    so every branch below is chosen by content, not extension:

    - Images (JPEG/PNG — camera captures, scanned pages saved as an image rather than a
      PDF) and scanned PDF pages with no text layer go through Codex vision
      (`_transcribe_image`). MarkItDown's own image path
      (`markitdown.converters.ImageConverter`) never transcribes the picture — it only
      emits EXIF metadata and, if given an LLM client, a one-paragraph *caption*, not the
      document's actual text. Local OCR (Tesseract) was tried here and dropped — not
      suitable, so this stays on the vision API.
    - PDF is extracted natively with PyMuPDF4LLM (built on `fitz`/PyMuPDF), straight from
      bytes — far faster than MarkItDown's pdfminer-based conversion, no temp file
      needed, and markdown-formatted output (headers/tables) instead of a flat text dump.
      Only pages with no embedded text layer (scanned pages) fall through to vision
      transcription.
    - Plain text/Markdown is decoded directly — no conversion library needed at all.
    - Everything else (DOCX/PPTX/XLSX/HTML) still goes through MarkItDown.
    """

    _IMAGE_MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}

    def run(self, document_id: str, file_bytes: bytes) -> List[PageOCRResult]:
        image_media_type = self._sniff_image_media_type(file_bytes)
        if image_media_type:
            text = self._transcribe_image(file_bytes, image_media_type)
            return [
                PageOCRResult(
                    document_id=document_id,
                    page_number=1,
                    text=text.strip(),
                    # Codex vision has no per-word confidence to report, unlike Tesseract.
                    # 0.85 clears the low_confidence threshold without claiming the near-
                    # certainty MarkItDown's hardcoded 0.99 implied for machine-readable text.
                    confidence=0.85,
                    engine="codex-vision",
                )
            ]

        if file_bytes.startswith(b"%PDF"):
            return self._extract_pdf(document_id, file_bytes)

        if not file_bytes.startswith(b"PK\x03\x04") and not self._looks_like_html(file_bytes):
            text = self._decode_plain_text(file_bytes)
            if text is not None:
                return [
                    PageOCRResult(
                        document_id=document_id, page_number=1, text=text.strip(),
                        confidence=0.99, engine="plain-text",
                    )
                ]

        from markitdown import MarkItDown
        import tempfile
        import os

        # Temporarily save the bytes to a file so MarkItDown can read it
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_path = temp_file.name

        try:
            md = MarkItDown()
            result = md.convert(temp_path)
            extracted_text = result.text_content
            return [
                PageOCRResult(
                    document_id=document_id,
                    page_number=1,
                    text=extracted_text.strip(),
                    confidence=0.99,
                    engine="markitdown-universal",
                )
            ]
        finally:
            os.remove(temp_path)

    @staticmethod
    def _extract_pdf(document_id: str, file_bytes: bytes) -> List[PageOCRResult]:
        import fitz
        import pymupdf4llm

        results = []
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            # page_chunks=True keeps per-page boundaries (chunking downstream needs a
            # page number per result) instead of one markdown blob for the whole file.
            pages_md = pymupdf4llm.to_markdown(doc, page_chunks=True)
            for i, page_data in enumerate(pages_md, start=1):
                text = (page_data.get("text") or "").strip()
                if text:
                    results.append(
                        PageOCRResult(
                            document_id=document_id, page_number=i, text=text,
                            confidence=0.99, engine="pymupdf4llm",
                        )
                    )
                    continue

                # No embedded text layer (scanned page) — rasterize and send to Codex
                # vision, same as a standalone image upload.
                pixmap = doc[i - 1].get_pixmap(matrix=fitz.Matrix(2, 2))
                png_bytes = pixmap.tobytes("png")
                ocr_text = UniversalOCREngine._transcribe_image(png_bytes, "image/png")
                results.append(
                    PageOCRResult(
                        document_id=document_id, page_number=i, text=ocr_text.strip(),
                        confidence=0.85, engine="codex-vision",
                    )
                )
            return results
        finally:
            doc.close()

    @staticmethod
    def _sniff_image_media_type(file_bytes: bytes) -> str | None:
        """Content-sniffed, not extension-based — `run` never receives a filename."""
        from PIL import Image
        import io

        try:
            with Image.open(io.BytesIO(file_bytes)) as img:
                return UniversalOCREngine._IMAGE_MEDIA_TYPES.get(img.format)
        except Exception:
            return None

    @staticmethod
    def _looks_like_html(file_bytes: bytes) -> bool:
        head = file_bytes[:512].lstrip().lower()
        return head.startswith(b"<!doctype") or head.startswith(b"<html")

    @staticmethod
    def _decode_plain_text(file_bytes: bytes) -> str | None:
        """Only takes the fast path on unambiguous UTF-8 text — anything else (a
        misidentified binary blob) falls through to MarkItDown instead of being
        silently mangled."""
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _transcribe_image(file_bytes: bytes, media_type: str) -> str:
        import base64

        from anthropic import Anthropic

        from app.config import settings

        client = Anthropic(
            api_key=settings.anthropic_api_key,
            **({"base_url": settings.anthropic_base_url} if settings.anthropic_base_url else {}),
        )
        message = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(file_bytes).decode("ascii"),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Transcribe every word of visible text in this image "
                                "exactly as written, preserving line breaks and structure. "
                                "Output only the transcribed text, nothing else. If there is "
                                "no legible text, output nothing."
                            ),
                        },
                    ],
                }
            ],
        )
        return message.content[0].text if message.content else ""