"""Stage 2 extraction should overlap its LLM calls instead of running them one at a time.

`clause_ner.extract` takes an `LLMProvider` as a plain argument, so these tests use a
hand-rolled fake instead of the real `CodexProvider` — no network, no API key, and no
dependency on the (currently missing) `MockProvider` some other tests reference.
"""

from __future__ import annotations

import threading
import time

from app.extraction import clause_ner
from app.schemas import FactType


class _TimingProvider:
    """Simulates per-call network latency and records how many calls overlap."""

    def __init__(self, order: list[str], delay: float = 0.05, reverse_delay: bool = False):
        self._order = order
        self._delay = delay
        self._reverse_delay = reverse_delay
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0
        self.call_count = 0

    def _wait(self, chunk) -> None:
        position = self._order.index(chunk.chunk_id)
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            self.call_count += 1
        # Later chunks finish first when reversed, so ordering assertions can't pass by
        # accident just because completion order happens to match submission order.
        rank = position if not self._reverse_delay else (len(self._order) - position)
        time.sleep(max(self._delay * rank, 0.001))
        with self._lock:
            self._in_flight -= 1

    def extract_facts(self, chunk):
        self._wait(chunk)
        return [
            {
                "fact_type": FactType.MONETARY_VALUE.value,
                "value": {"amount": "$1,000"},
                "confidence": 0.9,
            }
        ]

    def extract_edges(self, chunk):
        self._wait(chunk)
        return []


def test_extract_preserves_chunk_order_despite_out_of_order_completion(sample_chunks):
    order = [c.chunk_id for c in sample_chunks]
    provider = _TimingProvider(order=order, delay=0.02, reverse_delay=True)

    facts, _ = clause_ner.extract(sample_chunks, provider)

    assert [f.source_chunk_ids[0] for f in facts] == order


def test_extract_runs_chunk_calls_concurrently(sample_chunks):
    order = [c.chunk_id for c in sample_chunks]
    provider = _TimingProvider(order=order, delay=0.03)

    started = time.monotonic()
    clause_ner.extract(sample_chunks, provider)
    elapsed = time.monotonic() - started

    # 11 chunks x 2 calls each, run sequentially at ~0.03-0.3s per call would take well
    # over a second; concurrent execution should finish in a fraction of that.
    assert provider.max_in_flight > 1
    assert elapsed < 1.0
