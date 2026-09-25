from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping, Protocol


AgentState = MutableMapping[str, Any]


@dataclass(frozen=True)
class AgentResult:
    response: str
    next_agent: str | None = None
    stop: bool = False


class AgentContext(Protocol):
    state: AgentState
    message: str
    buyer_confirmed: bool


AgentHandler = Callable[[AgentContext], AgentResult]
StopCondition = Callable[[AgentState], bool]


@dataclass(frozen=True)
class Agent:
    name: str
    instructions: str
    handler: AgentHandler

    def run(self, context: AgentContext) -> AgentResult:
        return self.handler(context)


class HandoffOrchestrator:
    def __init__(
        self,
        agents: list[Agent],
        state_store: "StateStore",
        stop_conditions: list[StopCondition] | None = None,
        max_turns: int = 12,
    ) -> None:
        self.agents = {agent.name: agent for agent in agents}
        self.state_store = state_store
        self.stop_conditions = stop_conditions or []
        self.max_turns = max_turns

    def run(self, session_id: str, message: str = "", buyer_confirmed: bool = False) -> AgentState:
        state = self.state_store.load(session_id)
        if not state:
            state = {
                "session_id": session_id,
                "status": "new",
                "next_agent": "intake",
                "shared": {},
                "history": [],
            }

        if message:
            state["last_message"] = message
            state.setdefault("messages", []).append({"role": "buyer", "content": message})
        if buyer_confirmed and state.get("status") == "awaiting_confirmation":
            state["status"] = "in_progress"

        context = _Context(state=state, message=message, buyer_confirmed=buyer_confirmed)
        turns = 0
        while turns < self.max_turns and not self._should_stop(state):
            agent_name = state.get("next_agent")
            if not agent_name:
                break
            agent = self.agents[agent_name]
            result = agent.run(context)
            state.setdefault("history", []).append(
                {
                    "agent": agent.name,
                    "response": result.response,
                    "next_agent": result.next_agent,
                }
            )
            state["last_response"] = result.response
            state["next_agent"] = result.next_agent
            if result.stop:
                break
            turns += 1
        else:
            if turns >= self.max_turns:
                state["status"] = "stopped"
                state["stop_reason"] = "max_turns"
                state["next_agent"] = None

        self.state_store.save(session_id, state)
        return state

    def _should_stop(self, state: AgentState) -> bool:
        return any(condition(state) for condition in self.stop_conditions)


@dataclass
class _Context:
    state: AgentState
    message: str
    buyer_confirmed: bool


class StateStore(Protocol):
    def load(self, session_id: str) -> AgentState:
        ...

    def save(self, session_id: str, state: AgentState) -> None:
        ...
