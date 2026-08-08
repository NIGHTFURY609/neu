"""Verifying a Supabase access token.

Asymmetric (RS256/ES256) via JWKS is the primary path. `SUPABASE_SECRET_KEY` in `.env`
looks like it could sign, but it is the new-format API secret key — a bearer credential
for PostgREST and the Admin API — not an HMAC signing secret, so it cannot verify an
HS256 signature. Projects old enough to predate asymmetric signing keys have a separate
`SUPABASE_JWT_SECRET`, which is the HS256 fallback below.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config import settings


class TokenError(Exception):
    """Verification failed. Never leak the underlying token in the message."""


def _require_pyjwt():
    """Import PyJWT, or explain how to get it.

    Every import of `jwt` in this module is function-local so the package stays importable
    without PyJWT installed — `auth_mode="dev"` never reaches any of this, which is what
    keeps the default path and the test suite working on a machine that has not installed
    the extra. Without this guard the omission surfaces as a bare ModuleNotFoundError at
    first request, which reads like an application bug rather than a missing dependency.
    """
    try:
        import jwt
    except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise TokenError(
            "PyJWT is not installed, so Supabase tokens cannot be verified. "
            "Run `pip install 'pyjwt[crypto]>=2.8'` (it is declared in pyproject.toml), "
            "or set AUTH_MODE=dev to run without token verification."
        ) from exc
    return jwt


@lru_cache(maxsize=1)
def _jwk_client():
    """Lazy and cached.

    Built on first use rather than at import so that `pytest`, `alembic upgrade` and any
    `import app.api` never reach for the network. PyJWKClient keeps its own key cache, so
    this is one fetch per process per key-rotation window, not one per request.
    """
    jwt = _require_pyjwt()

    if not settings.supabase_jwks_url:
        raise TokenError("SUPABASE_JWKS_URL is not set")
    return jwt.PyJWKClient(settings.supabase_jwks_url, cache_keys=True, lifespan=300)


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """Return the verified claims, or raise `TokenError`.

    `algorithms` is always passed explicitly. Omitting it, or accepting "none", is the
    algorithm-confusion vulnerability: an attacker re-signs the payload with the public
    key as an HMAC secret and the library happily agrees.
    """
    jwt = _require_pyjwt()

    options: dict[str, Any] = {
        "audience": settings.jwt_audience,
        "leeway": settings.jwt_leeway_seconds,
        "options": {"require": ["exp", "sub"]},
    }
    if settings.supabase_url:
        options["issuer"] = f"{settings.supabase_url.rstrip('/')}/auth/v1"

    try:
        if settings.supabase_jwks_url:
            key = _jwk_client().get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=["RS256", "ES256"], **options)
        if settings.supabase_jwt_secret:
            return jwt.decode(
                token, settings.supabase_jwt_secret, algorithms=["HS256"], **options
            )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    raise TokenError("no verification material configured (set SUPABASE_JWKS_URL)")


def claim_at(claims: dict[str, Any], dot_path: str) -> Any:
    """Read a dotted path such as `app_metadata.rbac_tags`, or None if absent.

    Roles come from `app_metadata` because it is writable only with the service-role key.
    `user_metadata` is writable by the end user through `supabase.auth.updateUser()`,
    which would make role assignment self-service; the top-level `role` claim is always
    the literal "authenticated" and carries no information.
    """
    current: Any = claims
    for part in dot_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current
