"""Agent-to-agent state handoff orchestration package."""

from .agent_framework import HandoffOrchestrator, StateStore
from .auth import AuthError, Principal, authenticate, authenticate_request, extract_credential
from .config import ConfigError, Settings, get_settings, load_env_file, reset_settings_cache


def build_orchestrator(state_store: "StateStore | None" = None) -> "HandoffOrchestrator":
    from .orchestrator import build_orchestrator as _build_orchestrator

    return _build_orchestrator(state_store)


__all__ = [
    "AuthError",
    "ConfigError",
    "HandoffOrchestrator",
    "Principal",
    "Settings",
    "StateStore",
    "authenticate",
    "authenticate_request",
    "build_orchestrator",
    "extract_credential",
    "get_settings",
    "load_env_file",
    "reset_settings_cache",
]
