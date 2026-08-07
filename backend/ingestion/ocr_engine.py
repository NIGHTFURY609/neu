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

from backend.ingestion.models import PageOCRResult


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
    Magic bullet engine using Microsoft's MarkItDown.
    Automatically handles PDF, DOCX, PPTX, XLSX, HTML, and more.
    """
    def run(self, document_id: str, file_bytes: bytes) -> List[PageOCRResult]:
        from markitdown import MarkItDown
        import tempfile
        import os
        
        # 1. Temporarily save the bytes to a file so MarkItDown can read it
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(file_bytes)
            temp_path = temp_file.name
            
        try:
            # 2. Instantiate the converter
            md = MarkItDown()
            
            # 3. Automatically detect file type and extract text
            result = md.convert(temp_path)
            extracted_text = result.text_content
            
            # 4. Return the text to be passed to your chunker
            return [
                PageOCRResult(
                    document_id=document_id, 
                    page_number=1, 
                    text=extracted_text.strip(), 
                    confidence=0.99, 
                    engine="markitdown-universal"
                )
            ]
        finally:
            # 5. Clean up the temp file
            os.remove(temp_path)