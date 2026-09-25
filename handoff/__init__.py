"""Agent-to-agent state handoff orchestration package."""

from .agent_framework import HandoffOrchestrator, StateStore


def build_orchestrator(state_store: "StateStore | None" = None) -> "HandoffOrchestrator":
    from .orchestrator import build_orchestrator as _build_orchestrator

    return _build_orchestrator(state_store)

__all__ = ["build_orchestrator"]
