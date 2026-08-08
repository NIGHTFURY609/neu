"""Coverage for `app.auth.jwt.verify_supabase_jwt`.

No live Supabase call: RS256 cases sign with a locally-generated RSA keypair and stub
`_jwk_client` to hand back a signing key built from that same keypair, rather than hitting
a real JWKS endpoint. `_jwk_client` is `lru_cache`d, so every test that touches it clears
the cache first — otherwise whichever test runs first pins the client for the rest of the
module.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.jwt import TokenError, _jwk_client, verify_supabase_jwt
from app.config import settings

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(**claims_overrides) -> str:
    claims = {
        "sub": "user-1",
        "email": "user@example.com",
        "aud": settings.jwt_audience,
        "exp": int(time.time()) + 3600,
    }
    claims.update(claims_overrides)
    return pyjwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(_PRIVATE_KEY.public_key())


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """Every test starts from a clean slate: no JWKS URL, no HS256 secret configured."""
    monkeypatch.setattr(settings, "supabase_jwks_url", None)
    monkeypatch.setattr(settings, "supabase_jwt_secret", None)
    monkeypatch.setattr(settings, "supabase_url", None)
    _jwk_client.cache_clear()
    yield
    _jwk_client.cache_clear()


def _use_fake_jwks(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwks_url", "https://example.supabase.co/jwks")
    monkeypatch.setattr("app.auth.jwt._jwk_client", lambda: _FakeJWKClient())


def test_valid_rs256_token_is_accepted(monkeypatch):
    _use_fake_jwks(monkeypatch)

    claims = verify_supabase_jwt(_token())

    assert claims["sub"] == "user-1"
    assert claims["email"] == "user@example.com"


def test_tampered_signature_is_rejected(monkeypatch):
    _use_fake_jwks(monkeypatch)
    token = _token()
    # Flip a character in the middle of the signature segment, not the last one — the
    # last base64url character of a JWT signature can carry unused padding bits, so
    # mutating it doesn't reliably change the decoded bytes.
    header, payload, signature = token.split(".")
    middle = len(signature) // 2
    flipped = "A" if signature[middle] != "A" else "B"
    tampered = f"{header}.{payload}.{signature[:middle]}{flipped}{signature[middle + 1:]}"

    with pytest.raises(TokenError):
        verify_supabase_jwt(tampered)


def test_expired_token_is_rejected(monkeypatch):
    _use_fake_jwks(monkeypatch)
    expired = _token(exp=int(time.time()) - settings.jwt_leeway_seconds - 60)

    with pytest.raises(TokenError):
        verify_supabase_jwt(expired)


def test_audience_mismatch_is_rejected(monkeypatch):
    _use_fake_jwks(monkeypatch)
    wrong_audience = _token(aud="some-other-audience")

    with pytest.raises(TokenError):
        verify_supabase_jwt(wrong_audience)


def test_hs256_fallback_path_is_accepted(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", "shared-secret-at-least-32-bytes-long")
    token = pyjwt.encode(
        {"sub": "user-2", "aud": settings.jwt_audience, "exp": int(time.time()) + 3600},
        "shared-secret-at-least-32-bytes-long",
        algorithm="HS256",
    )

    claims = verify_supabase_jwt(token)

    assert claims["sub"] == "user-2"


def test_no_verification_material_configured_raises_token_error():
    with pytest.raises(TokenError, match="no verification material configured"):
        verify_supabase_jwt(_token())
