"""Which clause does this chunk belong to?

Dev 3 keys everything off `clause_ref` — the Risk Engine flags a clause, and
`redline.grounding` resolves the flagged clause by ref before it can ground anything.
A chunk that arrives without one is not degraded, it is invisible.

Deterministic regex, no LLM: the same document has to produce the same labels on every
run, or a redline's `grounding_chunk_ids` stop meaning anything across runs.

Deliberately *not* `app.redline.retrieval.SECTION_REF`. That pattern matches "Section
4.1" anywhere in a sentence, which is right for finding cross-references and wrong here —
a clause that merely mentions Section 2.2 would be labelled as Section 2.2.
"""
from __future__ import annotations

import re
from typing import List

from ingestion.models import Chunk

# A clause heading on a line of its own: "2.2 Limitation of Liability".
CLAUSE_HEADER = re.compile(r'(?m)^\s*(\d+(?:\.\d+)*)\.?\s+([A-Z][A-Za-z ,/&\-]{2,60})\.?\s*$')
# A term the definitions section defines: `"Excluded Claims" means ...`. Same pattern the
# retrieval loop builds its glossary from, so a chunk this marks as `definitions` is
# exactly a chunk that glossary can read.
DEFINED_TERM = re.compile(r'"([^"]+)"\s+means')


def label_chunks(chunks: List[Chunk]) -> List[Chunk]:
    """Label each chunk in place, carrying the current clause forward.

    Carry-forward is document-scoped and follows chunk order: a chunk that starts
    mid-clause — which most do, at 800 characters with a 150-character overlap —
    inherits the last heading seen. Chunks before the first heading (cover page,
    preamble) keep an empty ref rather than being guessed at.
    """
    current = ""
    for chunk in chunks:
        header = CLAUSE_HEADER.search(chunk.text)
        if header is not None:
            current = header.group(1)
        chunk.clause_ref = current
        chunk.section_type = "definitions" if DEFINED_TERM.search(chunk.text) else "clause"
    return chunks
