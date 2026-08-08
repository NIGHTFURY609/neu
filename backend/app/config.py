"""Tunable knobs for the Clause NER + KG stage.

ARCHITECTURE.md §7 is explicit that the escalation-related numbers below have to be
tuned against real document volume rather than picked up front, so they all live here
rather than being inlined at their use sites.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url


class Settings(BaseSettings):
    # utf-8-sig, not utf-8: a BOM-prefixed .env (e.g. saved by Notepad on Windows) would
    # otherwise attach the BOM to the first key's name, silently dropping just that one
    # var to its default with no error — see the ANTHROPIC_BASE_URL incident.
    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/legal_copilot"
    # `dev.sh`/`dev.ps1` already set this explicitly for the frontend/shell-level demo
    # flow; this is the backend's own read of it, used only for the startup guard below.
    # Defaults False rather than mirroring dev.sh's true default: a code-level default of
    # True would trip the guard for anyone whose DATABASE_URL isn't local and who hasn't
    # gone through dev.sh (e.g. running uvicorn directly), which is exactly what happened
    # here — DEMO_MODE has to be turned on explicitly, not assumed.
    demo_mode: bool = False

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    # We call Codex through agentrouter.org rather than Anthropic directly. The router
    # speaks the Anthropic Messages API, so only the endpoint changes — set
    # ANTHROPIC_BASE_URL and put the router's key in ANTHROPIC_API_KEY. Left unset, the
    # SDK goes to api.anthropic.com as before.
    anthropic_base_url: str | None = None

    # Dev 2 has not picked an embedding model yet. Nothing in this stage reads
    # embeddings, but we own the initial migration, so the column needs a width.
    embed_dim: int = 1536

    # §3.2 extraction is I/O-bound (live LLM round trips); this many chunks' fact/edge
    # calls run concurrently instead of one call at a time.
    extraction_concurrency: int = 8
    # §3.2 bounded retry loop. Start at 3 and tune.
    retry_budget: int = 3
    # Confidence a retry strategy must reach before we treat an edge as resolved.
    resolve_confidence: float = 0.75
    # How far the winning parse must beat the runner-up in the alternate_parse strategy.
    alternate_parse_margin: float = 0.15

    # §4.1 active retrieval loop, both listed as open decisions in §8.
    # Rounds of query/refine before the Redline Generator gives up and escalates.
    retrieval_budget: int = 4
    # Score below which a generated redline is held for approval instead of served.
    redline_confidence: float = 0.75

    # ------------------------------------------------------------------ auth (§7)
    # ARCHITECTURE.md §7 called RBAC "a label not enforcement". This closes that gap:
    # every caller must present a verified Supabase access token (Authorization: Bearer).
    supabase_url: str | None = None
    supabase_jwks_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_secret_key: str | None = None
    # Legacy (pre-2025) projects sign HS256 with a JWT secret instead of publishing a
    # JWKS. This is NOT `supabase_secret_key` above — that is an API bearer credential
    # and cannot verify a signature. Set only if the JWKS URL 404s.
    supabase_jwt_secret: str | None = None

    jwt_audience: str = "authenticated"
    jwt_leeway_seconds: int = 10
    # Dot paths into the token. `app_metadata` is writable only with the service-role
    # key; `user_metadata` is writable by the end user via supabase.auth.updateUser(),
    # which would make role assignment self-service. Do not point these at it.
    rbac_claim_path: str = "app_metadata.rbac_tags"
    role_claim_path: str = "app_metadata.app_role"

    default_rbac_tags: str = "legal-team"
    default_jurisdiction: str = "US-NY"

    # ---------------------------------------------------------------- search (§2.2)
    # "postgres_fts" ranks with ts_rank_cd, which is cover density over weighted
    # lexemes — not Okapi BM25, which is why the class is not named BM25Backend.
    # "memory_bm25" is real BM25 and is what the tests use, since no test in this repo
    # touches a database.
    search_backend: str = "postgres_fts"
    search_default_limit: int = 20
    regulation_match_limit: int = 6

    # ------------------------------------------------- clause alignment (§4.2 compare)
    # Reasoned starting values, not tuned against real documents. fixtures/mock/ is a
    # usable calibration set: DOC-001 vs DOC-003 is one contract off a bad scan and
    # should align near 1.0; DOC-001 vs DOC-004 is the false-positive test.
    align_match_threshold: float = 0.55
    # Below this, matching clause_refs are a renumbering coincidence, not a match.
    align_ref_trust_floor: float = 0.35
    align_prefilter: float = 0.45
    redline_applied_threshold: float = 0.85


settings = Settings()


# Local docker-compose's service hostname (see backend/docker-compose.yml's `services.db`)
# — the fourth host DEMO_MODE is allowed to point at without tripping the startup guard
# in app/api.py.
_LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "db"}


def _looks_like_production_database(url: str) -> bool:
    """True for anything that isn't the local/docker-compose Postgres — a Supabase
    pooler host included. Used to refuse startup with DEMO_MODE=true pointed at a
    database that isn't clearly a local/disposable one."""
    return make_url(url).host not in _LOCAL_DATABASE_HOSTS
