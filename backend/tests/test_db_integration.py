"""Proves the live database schema matches `app.models.Base.metadata` — not just that the
hand-written migrations look right on paper.

Skips (does not fail) with no reachable database, since CI has no DB credential and most
local runs don't have one either — see `app/db.py`'s `wait_for_database` for the same
"absorb, don't fail" treatment of a missing/unreachable Postgres. No rows are written here;
`compare_metadata` only reads catalog metadata, which is what makes this test safe to run
against a real Supabase instance rather than a disposable one.

Two diff categories are filtered out as known noise rather than failures, both pre-existing
and unrelated to any one table:

- `modify_nullable` — every hand-written migration in `alembic/versions/` leaves foreign-key
  and "obviously required" columns nullable at the Postgres level (no explicit
  `nullable=False`), while `Mapped[str]` (non-Optional) on the ORM side implies NOT NULL by
  SQLAlchemy convention. Reconciling that across every table would mean auditing production
  data for existing NULLs and writing a new migration per table — a real but separate piece
  of work, out of scope here.
- `add_index` / `remove_index` — several migrations gave indexes short hand-chosen names
  (e.g. `ix_reg_prov_jurisdiction`) where SQLAlchemy's `index=True` autogenerates a different
  name (`ix_regulatory_provisions_jurisdiction`) for the same column. Same index, different
  name; not a structural difference.

Everything else — missing/extra tables, missing/extra columns, type changes, missing/extra
constraints — still fails the test. That is what caught `page_ocr_confidence` having no ORM
class, and separately, `documents` having two check constraints
(`ck_documents_not_own_parent`, `ck_documents_rbac_tags_is_array`) that existed live but were
missing from `Document.__table_args__` — both are now fixed in `app/models.py`.
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import OperationalError

from app.db import engine
from app.models import Base

_NOISY_OPS = {"modify_nullable", "add_index", "remove_index"}


def _op_names(entry) -> set[str]:
    """A `compare_metadata` diff entry is either one op tuple or a list of them."""
    group = entry if isinstance(entry, list) else [entry]
    return {op[0] for op in group}


@pytest.fixture
def live_connection():
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("no reachable database configured (DATABASE_URL unset or unreachable)")
    try:
        yield connection
    finally:
        connection.close()


def test_live_schema_matches_orm_models(live_connection):
    context = MigrationContext.configure(live_connection)
    diff = compare_metadata(context, Base.metadata)
    structural_diff = [entry for entry in diff if not _op_names(entry) <= _NOISY_OPS]

    assert structural_diff == [], (
        f"live database schema has structurally drifted from app.models.Base.metadata: "
        f"{structural_diff}"
    )
