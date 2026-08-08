"""The FastAPI dependencies routes depend on."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import TokenError, claim_at, verify_supabase_jwt
from app.auth.principal import Principal
from app.auth.tags import normalize_tags
from app.config import settings

logger = logging.getLogger("app.auth")

# auto_error=False: a missing header is a 401 raised below (with our own detail message),
# not FastAPI's default one. A *malformed* header is still rejected by HTTPBearer itself.
_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Resolve the caller from a verified Supabase access token.

    A presented-but-invalid token is always a 401. Falling back to an unauthenticated
    principal there would make a forged token indistinguishable from no token at all,
    which is the failure that makes an auth layer decorative.
    """
    if credentials is None:
        raise _unauthorized("authentication required")
    try:
        claims = verify_supabase_jwt(credentials.credentials)
    except TokenError as exc:
        logger.warning("rejected a bearer token: %s", exc)
        raise _unauthorized("invalid or expired token") from exc
    return Principal(
        user_id=str(claims.get("sub", "")),
        email=claims.get("email"),
        role=str(claim_at(claims, settings.role_claim_path) or "authenticated"),
        rbac_tags=frozenset(normalize_tags(claim_at(claims, settings.rbac_claim_path))),
        claims=claims,
    )


def require_principal(principal: Principal = Depends(get_principal)) -> Principal:
    """`get_principal`. Kept as a distinct name so write routes can be explicit that
    they require an authenticated caller, even though `get_principal` already rejects
    anyone it cannot verify."""
    return principal
