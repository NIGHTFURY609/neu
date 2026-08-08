"""Word-level diff of one aligned clause pair."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel

# Trailing whitespace stays attached to its token so "".join(tokens) == text exactly.
# Without that, reassembling a diff for display silently loses the spacing.
_TOKEN = re.compile(r"\S+\s*")


class DiffSpan(BaseModel):
    op: Literal["equal", "insert", "delete", "replace"]
    left: str
    right: str


def word_diff(left: str, right: str) -> list[DiffSpan]:
    """Diff two clauses by word.

    Word-level, not line-level: a contract clause is one long line, so `unified_diff` or
    `ndiff` would report the entire clause as changed for a one-word edit — technically
    true and useless.

    `autojunk=False` matters more than it looks. SequenceMatcher's autojunk heuristic
    treats any element appearing in more than 1% of a sequence of 200+ elements as noise
    to be ignored — and in contract prose that is exactly `"the "`, `"of "`, `"shall "`,
    `"party "`. Those are the tokens that anchor the match; discarding them produces
    diffs that wander.
    """
    left_tokens = _TOKEN.findall(left)
    right_tokens = _TOKEN.findall(right)
    matcher = SequenceMatcher(None, left_tokens, right_tokens, autojunk=False)
    return [
        DiffSpan(
            op=tag,
            left="".join(left_tokens[i1:i2]),
            right="".join(right_tokens[j1:j2]),
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]
