"""UniversalOCREngine's local-extraction branches (PDF via PyMuPDF, plain text).

The image/scanned-page branch calls the Anthropic API and isn't covered here — it needs
network + a live key, same reason existing tests never exercised it either.
"""
from ingestion.ocr_engine import UniversalOCREngine


def _one_page_pdf(text: str) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_pdf_with_text_layer_extracts_natively_via_pymupdf4llm():
    pdf_bytes = _one_page_pdf("Section 2.2 Limitation of Liability")

    results = UniversalOCREngine().run("DOC-PDF", pdf_bytes)

    assert len(results) == 1
    assert results[0].engine == "pymupdf4llm"
    assert results[0].confidence == 0.99
    assert "Limitation of Liability" in results[0].text


def test_plain_text_bytes_are_decoded_directly_not_routed_through_markitdown():
    text = "1.1 Definitions\n\nExcluded Claims means..."

    results = UniversalOCREngine().run("DOC-TXT", text.encode("utf-8"))

    assert len(results) == 1
    assert results[0].engine == "plain-text"
    assert results[0].confidence == 0.99
    assert results[0].text == text


def test_html_bytes_still_route_to_markitdown_not_the_plain_text_fast_path():
    html = b"<!DOCTYPE html><html><body><p>Hello world</p></body></html>"

    results = UniversalOCREngine().run("DOC-HTML", html)

    assert len(results) == 1
    assert results[0].engine == "markitdown-universal"
    assert "Hello world" in results[0].text
