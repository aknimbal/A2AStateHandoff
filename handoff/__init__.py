"""Agent-to-agent state handoff orchestration package."""

from typing import Any


def build_orchestrator(*args: Any, **kwargs: Any) -> Any:
    from .orchestrator import build_orchestrator as _build_orchestrator

    return _build_orchestrator(*args, **kwargs)

__all__ = ["build_orchestrator"]
