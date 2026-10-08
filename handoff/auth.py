"""Caller authentication and agent-to-agent credential pass-through."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Mapping

from .config import Settings, get_settings


class AuthError(Exception):
    """Raised when a caller cannot be authenticated or is not authorized."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Principal:
    """An authenticated caller that is passed through the agent chain."""

    client_id: str
    scopes: frozenset[str]
    fingerprint: str

    def has_scope(self, scope: str | None) -> bool:
        return scope is None or scope in self.scopes

    def to_claims(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "scopes": sorted(self.scopes),
            "fingerprint": self.fingerprint,
        }


ANONYMOUS = Principal(client_id="anonymous", scopes=frozenset(), fingerprint="anonymous")


def extract_credential(headers: Mapping[str, str], settings: Settings | None = None) -> str:
    """Pull the raw credential out of the configured request header."""
    settings = settings or get_settings()
    lookup = {str(key).lower(): value for key, value in headers.items()}
    raw = lookup.get(settings.auth_header.lower(), "")
    if not raw:
        return ""
    scheme = settings.auth_scheme
    if scheme and raw.lower().startswith(f"{scheme.lower()} "):
        return raw[len(scheme) + 1 :].strip()
    return raw.strip()


def authenticate(credential: str, settings: Settings | None = None) -> Principal:
    """Resolve a shared-secret credential into a scoped principal."""
    settings = settings or get_settings()
    if not settings.auth_enabled:
        return ANONYMOUS
    if not credential:
        raise AuthError(f"Missing credential in '{settings.auth_header}' header.", status_code=401)

    for client in settings.clients.values():
        if hmac.compare_digest(client.token, credential):
            return Principal(
                client_id=client.client_id,
                scopes=client.scopes,
                fingerprint=fingerprint(credential, settings),
            )
    raise AuthError("Invalid credential.", status_code=401)


def authenticate_request(headers: Mapping[str, str], settings: Settings | None = None) -> Principal:
    settings = settings or get_settings()
    return authenticate(extract_credential(headers, settings), settings)


def fingerprint(credential: str, settings: Settings | None = None) -> str:
    """Non-reversible credential identifier that is safe to persist in state."""
    settings = settings or get_settings()
    digest = hmac.new(
        settings.handoff_signing_secret.encode("utf-8"),
        credential.encode("utf-8"),
        hashlib.sha256,
    )
    return digest.hexdigest()[:32]


def mint_handoff_token(
    principal: Principal,
    session_id: str,
    from_agent: str,
    to_agent: str,
    settings: Settings | None = None,
) -> str:
    """Create a signed token that carries the caller identity to the next agent."""
    settings = settings or get_settings()
    payload = _payload(principal, session_id, from_agent, to_agent)
    return f"{payload}.{_sign(payload, settings)}"


def verify_handoff_token(
    token: str,
    principal: Principal,
    session_id: str,
    from_agent: str,
    to_agent: str,
    settings: Settings | None = None,
) -> bool:
    """Verify a pass-through token belongs to this caller, session, and hop."""
    settings = settings or get_settings()
    payload, separator, signature = token.rpartition(".")
    if not separator:
        return False
    expected_payload = _payload(principal, session_id, from_agent, to_agent)
    if not hmac.compare_digest(payload, expected_payload):
        return False
    return hmac.compare_digest(signature, _sign(payload, settings))


def _payload(principal: Principal, session_id: str, from_agent: str, to_agent: str) -> str:
    return "|".join(
        [
            session_id,
            from_agent,
            to_agent,
            principal.client_id,
            principal.fingerprint,
            ",".join(sorted(principal.scopes)),
        ]
    )


def _sign(payload: str, settings: Settings) -> str:
    return hmac.new(
        settings.handoff_signing_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
