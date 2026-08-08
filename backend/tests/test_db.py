"""`wait_for_database` warms the connection pool at process startup.

A transient DNS failure against the Supabase pooler (see app/api.py's OperationalError
handler) is most likely on the very first connection a fresh process makes. Retrying a
few times before serving traffic means that blip gets absorbed here, quietly, instead of
surfacing as a 500/503 on whichever request happens to arrive first.
"""

from sqlalchemy.exc import OperationalError

from app.db import engine, wait_for_database


def test_engine_pre_pings_pooled_connections():
    """Supabase's pooler drops idle connections without warning; a pooled connection that
    looked fine at checkout can be dead by the time a query actually runs, surfacing as
    'server closed the connection unexpectedly' mid-request. pre_ping makes SQLAlchemy
    probe with a cheap SELECT before handing a connection to a route, transparently
    reconnecting instead of failing the request.
    """
    assert engine.pool._pre_ping is True


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    """Fails `connect()` a fixed number of times, then succeeds."""

    def __init__(self, failures: int):
        self.failures = failures
        self.attempts = 0

    def connect(self):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OperationalError("SELECT 1", {}, Exception("getaddrinfo failed"))
        return _FakeConnection()


def test_retries_transient_connection_failure_then_succeeds():
    engine = _FakeEngine(failures=2)

    wait_for_database(engine, retries=3, delay=0)

    assert engine.attempts == 3


def test_gives_up_after_max_retries_without_raising():
    engine = _FakeEngine(failures=999)

    wait_for_database(engine, retries=3, delay=0)  # must not raise

    assert engine.attempts == 3
