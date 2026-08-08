"""The FastAPI dependencies routes depend on."""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import TokenError, claim_at, verify_supabase_jwt
from app.auth.principal import Principal
from app.auth.tags import normalize_tags
from app.config import settings

logger = logging.getLogger("app.auth")

# auto_error=False: a missing header is resolved below according to `auth_mode`, not
# rejected here. A *malformed* one still is.
_bearer = HTTPBearer(auto_error=False)


def _superuser(user_id: str = "system", role: str = "system") -> Principal:
    return Principal(user_id=user_id, role=role, rbac_tags=frozenset({"*"}))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_roles: str | None = Header(default=None, alias="X-Roles"),
) -> Principal:
    """Resolve the caller.

    A presented-but-invalid token is always a 401, in every mode. Falling back to the dev
    principal there would make a forged token indistinguishable from no token at all,
    which is the failure that makes an auth layer decorative.
    """
    if settings.auth_mode == "off":
        return _superuser()

    if credentials is not None:
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

    if settings.auth_mode == "supabase":
        raise _unauthorized("authentication required")

    # --- dev mode ---------------------------------------------------------------
    # Identity is unverified here, but enforcement is not bypassed: the tag-overlap
    # filter below runs identically in every mode, and a dev principal simply happens to
    # carry whatever `DEV_PRINCIPAL_TAGS` says. That keeps the enforcement path exercised
    # by every test run rather than only in the demo.
    if x_user_id:
        return Principal(
            user_id=x_user_id,
            role="dev",
            rbac_tags=frozenset(normalize_tags(x_roles)),
        )
    return Principal(
        user_id="dev-anonymous",
        role="anonymous",
        rbac_tags=frozenset(normalize_tags(settings.dev_principal_tags)),
    )


def require_principal(principal: Principal = Depends(get_principal)) -> Principal:
    """`get_principal`, but rejects the anonymous caller on write routes.

    In dev mode the anonymous principal is allowed through: the existing test suite
    constructs `TestClient(api.app)` and sends no headers, and turning ~40 tests red to
    express a policy that dev mode does not enforce anyway would be theatre.
    """
    if principal.role == "anonymous" and settings.auth_mode != "dev":
        raise _unauthorized("authentication required")
    return principal
