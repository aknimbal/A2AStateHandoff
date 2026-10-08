"""Environment-driven configuration.

Every tunable value (credentials, scopes, header names, storage paths) is read
from the process environment, which is seeded from a `.env` file when present.
Nothing in this package hardcodes secrets or client identities.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class ClientCredential:
    client_id: str
    token: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class Settings:
    auth_enabled: bool
    auth_header: str
    auth_scheme: str
    session_binding_enabled: bool
    handoff_signing_secret: str
    state_store_path: str
    clients: dict[str, ClientCredential] = field(default_factory=dict)
    agent_scopes: dict[str, str] = field(default_factory=dict)

    def scope_for(self, agent_name: str) -> str | None:
        return self.agent_scopes.get(agent_name)


_DEFAULT_ENV_FILE = ".env"
_cache: Settings | None = None
_cache_lock = Lock()


def load_env_file(path: str | Path | None = None, override: bool = False) -> dict[str, str]:
    """Load `KEY=VALUE` pairs from a dotenv file into `os.environ`.

    Existing environment variables win unless `override` is set, so hosted
    platform settings (for example Azure Functions app settings) take
    precedence over a local `.env` file.
    """
    env_path = Path(path or os.environ.get("ENV_FILE", _DEFAULT_ENV_FILE))
    if not env_path.is_file():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            continue
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def get_settings(refresh: bool = False) -> Settings:
    global _cache
    with _cache_lock:
        if _cache is None or refresh:
            load_env_file()
            _cache = _build_settings()
        return _cache


def reset_settings_cache() -> None:
    """Drop the cached settings so the next read re-evaluates the environment."""
    global _cache
    with _cache_lock:
        _cache = None


def _build_settings() -> Settings:
    auth_enabled = _env_bool("AUTH_ENABLED", True)
    clients = _build_clients(auth_enabled)
    agent_names = _csv(os.environ.get("AGENT_NAMES", ""))
    agent_scopes = _build_agent_scopes(agent_names, auth_enabled)

    signing_secret = os.environ.get("HANDOFF_SIGNING_SECRET", "").strip()
    if auth_enabled and not signing_secret:
        raise ConfigError("HANDOFF_SIGNING_SECRET must be set when AUTH_ENABLED is true.")

    return Settings(
        auth_enabled=auth_enabled,
        auth_header=os.environ.get("AUTH_HEADER_NAME", "Authorization").strip() or "Authorization",
        auth_scheme=os.environ.get("AUTH_SCHEME", "Bearer").strip(),
        session_binding_enabled=_env_bool("AUTH_SESSION_BINDING_ENABLED", True),
        handoff_signing_secret=signing_secret,
        state_store_path=_state_store_path(),
        clients=clients,
        agent_scopes=agent_scopes,
    )


def _build_clients(auth_enabled: bool) -> dict[str, ClientCredential]:
    client_ids = _csv(os.environ.get("AUTH_CLIENT_IDS", ""))
    if auth_enabled and not client_ids:
        raise ConfigError("AUTH_CLIENT_IDS must list at least one client when AUTH_ENABLED is true.")

    clients: dict[str, ClientCredential] = {}
    for client_id in client_ids:
        slug = _slug(client_id)
        token = os.environ.get(f"AUTH_CLIENT_{slug}_TOKEN", "").strip()
        if not token:
            raise ConfigError(f"AUTH_CLIENT_{slug}_TOKEN must be set for client '{client_id}'.")
        scopes = frozenset(_csv(os.environ.get(f"AUTH_CLIENT_{slug}_SCOPES", "")))
        if not scopes:
            raise ConfigError(f"AUTH_CLIENT_{slug}_SCOPES must list at least one scope for client '{client_id}'.")
        clients[client_id] = ClientCredential(client_id=client_id, token=token, scopes=scopes)
    return clients


def _build_agent_scopes(agent_names: list[str], auth_enabled: bool) -> dict[str, str]:
    scopes: dict[str, str] = {}
    for agent_name in agent_names:
        scope = os.environ.get(f"AGENT_SCOPE_{_slug(agent_name)}", "").strip()
        if not scope:
            if auth_enabled:
                raise ConfigError(f"AGENT_SCOPE_{_slug(agent_name)} must be set for agent '{agent_name}'.")
            continue
        scopes[agent_name] = scope
    return scopes


def _state_store_path() -> str:
    configured = os.environ.get("STATE_STORE_PATH", "").strip()
    if configured:
        return configured
    return os.path.join(tempfile.gettempdir(), "a2a_state_handoff", "state.json")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).upper()
