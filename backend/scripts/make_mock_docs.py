"""Generate the mock documents this stage is exercised against.

`chunks.sample.json` is the *contract* sample proposed to Dev 2 and is regenerated here
so its char spans stay internally consistent rather than hand-assigned. The three
documents under `fixtures/mock/` exist to prove the stage generalises: they are written
against the drafting patterns the extractor already knows, not against new rules added to
make them pass.

| Document | What it exercises |
|---|---|
| DOC-001 | the contract sample — one of each: direct, precedent, widen, alternate, escalate |
| DOC-002 | no usable precedent anywhere in the document — escalation-heavy |
| DOC-003 | the same clauses as DOC-001 off a bad scan — §7's OCR cap, loudly |
| DOC-004 | a clean amendment — every ambiguity resolved, empty review queue |

    python scripts/make_mock_docs.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

# (clause_ref, page, section_type, ocr_confidence, text)
Clause = tuple[str, int, str, float, str]


DOC_001: list[Clause] = [
    (
        "1.1",
        1,
        "definitions",
        0.99,
        '"Excluded Claims" means claims arising from data loss, third-party indemnity '
        "demands, or regulatory fines. For the avoidance of doubt, Excluded Claims are "
        "carved out of the scope of the limitation of liability in Section 2.2 and are "
        "not released, waived, or discharged by any provision of this Agreement.",
    ),
    (
        "1.2",
        1,
        "definitions",
        0.99,
        '"Agreement" means this Master Services Agreement together with all Statements '
        "of Work and exhibits attached hereto.",
    ),
    (
        # Defined, but neutrally: widening into the definitions section is a genuine
        # attempt for Section 6.1 that legitimately fails to disambiguate.
        "1.3",
        1,
        "definitions",
        0.99,
        '"Disputed Amounts" means amounts identified by Customer in good faith and in '
        "writing as incorrectly invoiced.",
    ),
    (
        "2.2",
        2,
        "clause",
        0.98,
        "Limitation of Liability. Each party's total aggregate liability arising out of "
        "or related to this Agreement shall not exceed one million dollars "
        "($1,000,000).",
    ),
    (
        "2.4",
        2,
        "clause",
        0.97,
        "Payment. Customer shall pay all undisputed invoices within thirty (30) days of "
        "receipt. Customer hereby waives and releases and discharges any right to "
        "withhold payment of undisputed amounts.",
    ),
    (
        "3.1",
        3,
        "clause",
        0.96,
        "Governing Law. This Agreement shall be governed by and construed in accordance "
        "with the laws of the State of New York, without regard to its conflict of laws "
        "principles.",
    ),
    (
        # Case A - clean OVERRIDES. Establishes the precedent used by Case B.
        "4.1",
        4,
        "clause",
        0.98,
        "Notwithstanding Section 2.2, the liability cap set forth in this Section 4.1 "
        "shall control with respect to any claim brought under the Security Addendum.",
    ),
    (
        # Case B - same "notwithstanding ... shall control" pattern as 4.1, but the
        # extractor is unsure between OVERRIDES and MODIFIES. Resolved at round 1 by
        # precedent from the confirmed 4.1 -> 2.2 edge.
        "5.1",
        5,
        "clause",
        0.94,
        "Notwithstanding Section 2.4, the payment schedule set forth in this Section 5.1 "
        "shall control for all Statements of Work executed after the Effective Date.",
    ),
    (
        # Case C - WAIVES vs MODIFIES, settled only by the clause on the other end of
        # the reference. No precedent for this drafting pattern; widening into the
        # neighbours and definitions finds "Disputed Amounts" but that definition says
        # nothing about waiver. Section 2.4's own release language decides it at round 3.
        "6.1",
        6,
        "clause",
        0.93,
        "Customer shall have no claim under Section 2.4 with respect to Disputed "
        "Amounts.",
    ),
    (
        # Case D - WAIVES vs MODIFIES. The disambiguating language sits in Section 1.1,
        # outside this chunk's window. Resolved at round 2 by widening context.
        "7.3",
        7,
        "clause",
        0.91,
        "Notwithstanding Section 2.2, Customer shall have no claim against Provider in "
        "respect of any Excluded Claims, and the limitation in Section 2.2 shall be read "
        "accordingly.",
    ),
    (
        # Case E - genuinely ambiguous drafting. No precedent, no definitional help, and
        # the alternate readings score too close to separate. Escalates after 3 rounds.
        # This is exactly the case ARCHITECTURE.md 4.2 says retrieval cannot solve:
        # the ambiguity lives in the source material.
        "8.2",
        8,
        "clause",
        0.88,
        "Section 8.2 shall apply in lieu of Section 3.1 where applicable, and to the "
        "extent of any inconsistency the parties shall act in a manner consistent with "
        "the intent of both provisions.",
    ),
]


# DOC-002 - a clean scan of a badly drafted contract. Nothing here establishes a
# precedent, the definitions are neutral, and two of the three cross-references are
# written so loosely that no strategy can settle them. This is the queue-heavy case:
# the retry loop should still pay for itself by resolving the one that is resolvable.
DOC_002: list[Clause] = [
    (
        "1.1",
        1,
        "definitions",
        0.98,
        '"Service Credits" means the credits calculated in accordance with the Service '
        "Level Exhibit and applied against future invoices.",
    ),
    (
        "1.2",
        1,
        "definitions",
        0.98,
        '"Effective Date" means the date on which the last party signs this Agreement.',
    ),
    (
        "2.1",
        2,
        "clause",
        0.97,
        "Fees. Customer shall pay all undisputed invoices within forty-five (45) days of "
        "receipt.",
    ),
    (
        "2.3",
        2,
        "clause",
        0.97,
        "Limitation of Liability. Provider's total aggregate liability under this "
        "Agreement shall not exceed five hundred thousand dollars ($500,000).",
    ),
    (
        "3.2",
        3,
        "clause",
        0.96,
        "Governing Law. This Agreement shall be governed by the laws of the State of "
        "Delaware, without regard to its conflict of laws principles.",
    ),
    (
        # Ambiguous, and no earlier "notwithstanding" clause exists to serve as
        # precedent. Only the head-to-head parse against Section 2.3 separates it.
        "4.2",
        4,
        "clause",
        0.96,
        "Notwithstanding Section 2.3, the remedies schedule set forth in this Section "
        "4.2 shall control with respect to any claim for Service Credits.",
    ),
    (
        # Same shape as DOC-001's Section 6.1, but Section 2.1 contains no release or
        # narrowing language at all, so the head-to-head parse has nothing to work with.
        "5.4",
        5,
        "clause",
        0.95,
        "Customer shall have no claim under Section 2.1 with respect to Service Credits.",
    ),
    (
        "6.3",
        6,
        "clause",
        0.95,
        "Section 6.3 shall apply in lieu of Section 3.2 where applicable, and to the "
        "extent of any inconsistency the parties shall act in a manner consistent with "
        "the intent of both provisions.",
    ),
]


# DOC-003 - DOC-001's resolvable clauses, off a poor scan. Nothing about the drafting
# changed; only the page quality did. ARCHITECTURE.md 7 calls out bad OCR silently
# degrading every downstream stage, so the cap has to make it degrade loudly instead:
# resolutions that would clear the threshold on a clean page fall below it here and
# reach the queue with a trace that says so.
DOC_003: list[Clause] = [
    (
        "1.1",
        1,
        "definitions",
        0.71,
        '"Excluded Claims" means claims arising from data loss or regulatory fines. '
        "Excluded Claims are carved out of the scope of the limitation of liability in "
        "Section 2.2 and are not released, waived, or discharged by any provision of "
        "this Agreement.",
    ),
    (
        "2.2",
        2,
        "clause",
        0.68,
        "Limitation of Liability. Each party's total aggregate liability under this "
        "Agreement shall not exceed two million five hundred thousand dollars "
        "($2,500,000).",
    ),
    (
        "2.4",
        2,
        "clause",
        0.66,
        "Payment. Customer shall pay all undisputed invoices within sixty (60) days of "
        "receipt. Customer hereby waives and releases and discharges any right to "
        "withhold payment of undisputed amounts.",
    ),
    (
        # Unambiguous even on a bad scan - but confirmed at the page's confidence, not
        # the extractor's, so it is too weak to serve as precedent for Section 5.1.
        "4.1",
        4,
        "clause",
        0.72,
        "Notwithstanding Section 2.2, the liability cap set forth in this Section 4.1 "
        "shall control with respect to any claim brought under the Security Addendum.",
    ),
    (
        # Resolves at round 3 on a clean page. Here the parse still picks the right
        # reading, but the pages it was read off cap it below the threshold.
        "5.1",
        5,
        "clause",
        0.70,
        "Notwithstanding Section 2.4, the payment schedule set forth in this Section 5.1 "
        "shall control for all Statements of Work executed after the Effective Date.",
    ),
    (
        # Resolves at round 2 on a clean page, for the same reason.
        "7.3",
        7,
        "clause",
        0.64,
        "Notwithstanding Section 2.2, Customer shall have no claim against Provider in "
        "respect of any Excluded Claims, and the limitation in Section 2.2 shall be read "
        "accordingly.",
    ),
]


# DOC-004 - a short, well-drafted amendment. Every ambiguity is answerable from the
# document itself, so the review queue comes out empty. The point of keeping this one
# around is that "no escalations" has to be a reachable outcome; a stage that always
# finds something for a human to do is the queue-everything machine 7 warns about.
DOC_004: list[Clause] = [
    (
        "1.1",
        1,
        "definitions",
        0.99,
        '"Excluded Claims" means claims arising from third-party indemnity demands. '
        "Excluded Claims are carved out of the scope of the limitation of liability in "
        "Section 2.1 and are not released, waived, or discharged by this Amendment.",
    ),
    (
        "2.1",
        1,
        "clause",
        0.98,
        "Limitation of Liability. The aggregate liability of either party under this "
        "Amendment shall not exceed seven hundred fifty thousand dollars ($750,000).",
    ),
    (
        "2.3",
        2,
        "clause",
        0.98,
        "Payment. Customer shall pay all undisputed invoices within fifteen (15) days of "
        "receipt.",
    ),
    (
        "3.1",
        2,
        "clause",
        0.97,
        "Governing Law. This Amendment shall be governed by the laws of the State of "
        "California.",
    ),
    (
        # Clean override - and, being unambiguous on a clean page, strong enough to
        # carry Section 5.1 at round 1.
        "4.1",
        3,
        "clause",
        0.98,
        "Notwithstanding Section 2.1, the liability cap set forth in this Section 4.1 "
        "shall control with respect to any claim brought under the Security Exhibit.",
    ),
    (
        "5.1",
        3,
        "clause",
        0.97,
        "Notwithstanding Section 2.3, the payment schedule set forth in this Section 5.1 "
        "shall control for all orders placed after the Effective Date.",
    ),
    (
        "6.1",
        4,
        "clause",
        0.97,
        "Customer shall have no claim against Provider in respect of any Excluded "
        "Claims, and the limitation in Section 2.1 shall be read accordingly.",
    ),
]


DOCUMENTS = [
    ("DOC-001", "acme-msa-2026.pdf", {"confidentiality": "internal", "matter": "acme-msa"},
     DOC_001, FIXTURES / "chunks.sample.json"),
    ("DOC-002", "northwind-csa-2027.pdf", {"confidentiality": "internal", "matter": "northwind-csa"},
     DOC_002, FIXTURES / "mock" / "chunks.doc-002.json"),
    ("DOC-003", "globex-msa-scanned.pdf", {"confidentiality": "restricted", "matter": "globex-msa"},
     DOC_003, FIXTURES / "mock" / "chunks.doc-003.json"),
    ("DOC-004", "initech-amendment-01.pdf", {"confidentiality": "internal", "matter": "initech-amd"},
     DOC_004, FIXTURES / "mock" / "chunks.doc-004.json"),
]


def build(document_id: str, clauses: list[Clause]) -> list[dict]:
    chunks = []
    cursor = 0
    for i, (clause_ref, page, section_type, ocr, text) in enumerate(clauses, start=1):
        chunks.append(
            {
                "chunk_id": f"{document_id}-C{i:03d}",
                "document_id": document_id,
                "text": text,
                "page": page,
                "char_start": cursor,
                "char_end": cursor + len(text),
                "ocr_confidence": ocr,
                "clause_ref": clause_ref,
                "section_type": section_type,
                "embedding": None,
            }
        )
        cursor += len(text) + 2  # paragraph break between clauses
    return chunks


def main() -> None:
    for document_id, filename, rbac_tags, clauses, out in DOCUMENTS:
        payload = {
            "document_id": document_id,
            "filename": filename,
            "rbac_tags": rbac_tags,
            "chunks": build(document_id, clauses),
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(FIXTURES.parent)} ({len(payload['chunks'])} chunks)")


if __name__ == "__main__":
    main()
