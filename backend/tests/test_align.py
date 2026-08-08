"""Coverage for `app.compare.align`, which had none before this.

`AssembledClause` objects are built directly in-test rather than via new JSON fixtures,
matching this repo's existing style (e.g. `test_risk_api.py` constructs dataclasses inline).
"""

from app.compare.align import align_clauses
from app.compare.assemble import AssembledClause
from app.compare.service import _clause_status


def _clause(ref: str, heading: str, text: str, section_type: str = "clause") -> AssembledClause:
    return AssembledClause(
        clause_ref=ref,
        heading=heading,
        text=text,
        section_type=section_type,
        pages=(1, 1),
        chunk_ids=(f"chunk-{ref}",),
        min_ocr_confidence=1.0,
    )


def test_renumbered_clause_is_matched_by_text_similarity():
    """Near-identical body under a different ref/number is still matched (Pass 2), and
    reported as 'renumbered' rather than 'modified' — the clause-movement case."""
    left = _clause(
        "4.2", "Liability", "The Vendor shall indemnify the Customer for damages up to $1,000,000."
    )
    right = _clause(
        "4.3", "Liability", "The Vendor shall indemnify the Customer for damages up to $750,000."
    )

    pairings = align_clauses({"4.2": left}, {"4.3": right})

    assert len(pairings) == 1
    pairing = pairings[0]
    assert (pairing.left_ref, pairing.right_ref) == ("4.2", "4.3")
    # Money is masked by `normalize`, so the two amounts don't count against similarity.
    assert pairing.similarity >= 0.999
    assert _clause_status(pairing) == "renumbered"


def test_section_type_mismatch_blocks_a_pass_2_match():
    """Identical body text does not get paired across a `section_type` change — Pass 2's
    block, not a similarity failure."""
    text = (
        "Each party shall keep information confidential using reasonable care and shall "
        "not disclose it to any third party without prior written consent."
    )
    left = _clause("5.1", "Confidentiality", text, section_type="clause")
    right = _clause("5.2", "Confidentiality", text, section_type="definitions")

    pairings = align_clauses({"5.1": left}, {"5.2": right})

    matched = [p for p in pairings if p.left_ref and p.right_ref]
    assert matched == []
    statuses = {(p.left_ref, p.right_ref): _clause_status(p) for p in pairings}
    assert statuses[("5.1", None)] == "removed"
    assert statuses[(None, "5.2")] == "added"


def test_matching_ref_alone_is_accepted_even_with_unrelated_content():
    """Pass 1 accepts any identical-ref pair under the default config: the ref term's
    weight (0.35) equals `align_ref_trust_floor` (0.35), so a same-ref pair can never
    score below the floor — it is paired regardless of content agreement. What actually
    flags a coincidental renumbering to a reviewer is the low `similarity` this produces,
    which `app.compare.service`'s `LOW_CONFIDENCE_ALIGNMENT` (0.70) caveat catches; the
    floor does not reject it outright."""
    left = _clause("6.1", "Termination", "This agreement terminates upon written notice.")
    right = _clause("6.1", "Confidentiality", "Each party shall keep information confidential.")

    pairings = align_clauses({"6.1": left}, {"6.1": right})

    assert len(pairings) == 1
    pairing = pairings[0]
    assert (pairing.left_ref, pairing.right_ref) == ("6.1", "6.1")
    assert pairing.similarity < 0.70
