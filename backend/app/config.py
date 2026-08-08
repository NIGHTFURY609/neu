"""Tunable knobs for the Clause NER + KG stage.

ARCHITECTURE.md §7 is explicit that the escalation-related numbers below have to be
tuned against real document volume rather than picked up front, so they all live here
rather than being inlined at their use sites.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/legal_copilot"

    # "mock" is the default so fixtures and tests stay deterministic; "live" swaps in
    # the real Anthropic call behind the same LLMProvider interface.
    llm_mode: str = "mock"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    # We call Claude through agentrouter.org rather than Anthropic directly. The router
    # speaks the Anthropic Messages API, so only the endpoint changes — set
    # ANTHROPIC_BASE_URL and put the router's key in ANTHROPIC_API_KEY. Left unset, the
    # SDK goes to api.anthropic.com as before.
    anthropic_base_url: str | None = None

    # Dev 2 has not picked an embedding model yet. Nothing in this stage reads
    # embeddings, but we own the initial migration, so the column needs a width.
    embed_dim: int = 1536

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


settings = Settings()
