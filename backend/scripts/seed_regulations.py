"""Load `fixtures/regulations/*.json` into `regulatory_provisions`.

Idempotent: `provision_id` is the primary key and this uses `session.merge`, the same
upsert idiom as `kg.store.save_edges` and `risk.store.save_risk_flags`. Re-running after
editing a JSON file updates in place; it never duplicates.

    python -m scripts.seed_regulations
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def load_fixture_files(root: Path) -> list[dict]:
    """Parse every instrument file, flattening to one provision list.

    Instrument-level `jurisdiction`, `source_url` and `retrieved_at` act as defaults that
    a provision may override — a provision of a national statute can sit in a different
    territory than its instrument.
    """
    provisions: list[dict] = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload["provisions"]:
            provisions.append(
                {
                    "provision_id": raw["provision_id"],
                    "instrument": payload["instrument"],
                    "instrument_title": payload["instrument_title"],
                    "citation": raw["citation"],
                    "title": raw["title"],
                    "jurisdiction": raw.get("jurisdiction", payload["jurisdiction"]),
                    "body": raw["body"],
                    "summary": raw["summary"],
                    "topic_tags": raw.get("topic_tags", []),
                    "clause_patterns": raw.get("clause_patterns", []),
                    "rule_ids": raw.get("rule_ids", []),
                    "source_url": raw.get("source_url", payload["source_url"]),
                    "retrieved_at": date.fromisoformat(
                        raw.get("retrieved_at", payload["retrieved_at"])
                    ),
                    "notes": raw.get("notes"),
                }
            )
    return provisions


def seed(session, root: Path = FIXTURES / "regulations") -> int:
    from app import models

    provisions = load_fixture_files(root)
    for provision in provisions:
        session.merge(models.RegulatoryProvision(**provision))
    session.commit()
    return len(provisions)


def main() -> None:
    from app.db import SessionLocal

    root = FIXTURES / "regulations"
    if not root.exists():
        sys.exit(f"no regulation fixtures at {root}")
    with SessionLocal() as session:
        count = seed(session, root)
    print(f"seeded {count} provisions from {root}")


if __name__ == "__main__":
    main()
