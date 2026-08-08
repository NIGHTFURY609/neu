"""Coverage for `app.config._looks_like_production_database` — the DEMO_MODE startup
guard's host classification."""

from app.config import Settings, _looks_like_production_database


def test_localhost_is_not_production():
    assert _looks_like_production_database("postgresql+psycopg://postgres:pw@localhost:5432/db") is False


def test_loopback_ip_is_not_production():
    assert _looks_like_production_database("postgresql+psycopg://postgres:pw@127.0.0.1:5432/db") is False


def test_docker_compose_service_host_is_not_production():
    assert _looks_like_production_database("postgresql+psycopg://postgres:pw@db:5432/legal_copilot") is False


def test_supabase_pooler_host_is_production():
    url = "postgresql+psycopg://user:pw@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres"
    assert _looks_like_production_database(url) is True


def test_supabase_direct_host_is_production():
    url = "postgresql+psycopg://user:pw@db.abcxyzproject.supabase.co:5432/postgres"
    assert _looks_like_production_database(url) is True


def test_demo_mode_defaults_false():
    """Off by default: only dev.sh/dev.ps1 (or an explicit DEMO_MODE=true) turns it on,
    so the startup guard never trips a plain `uvicorn app.api:app` run by surprise."""
    assert Settings().demo_mode is False


def test_demo_mode_can_be_enabled():
    assert Settings(demo_mode=True).demo_mode is True
