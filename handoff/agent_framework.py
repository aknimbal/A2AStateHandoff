from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping, Protocol

from .auth import ANONYMOUS, AuthError, Principal, mint_handoff_token, verify_handoff_token
from .config import Settings, get_settings


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
    principal: Principal
    handoff_token: str | None


AgentHandler = Callable[[AgentContext], AgentResult]
StopCondition = Callable[[AgentState], bool]


@dataclass(frozen=True)
class Agent:
    name: str
    instructions: str
    handler: AgentHandler
    required_scope: str | None = None

    def run(self, context: AgentContext) -> AgentResult:
        return self.handler(context)


class HandoffOrchestrator:
    def __init__(
        self,
        agents: list[Agent],
        state_store: "StateStore",
        stop_conditions: list[StopCondition] | None = None,
        max_turns: int = 12,
        entry_agent: str = "intake",
        settings: Settings | None = None,
    ) -> None:
        self.agents = {agent.name: agent for agent in agents}
        self.state_store = state_store
        self.stop_conditions = stop_conditions or []
        self.max_turns = max_turns
        self.entry_agent = entry_agent
        self.settings = settings or get_settings()

    def run(
        self,
        session_id: str,
        message: str = "",
        buyer_confirmed: bool = False,
        principal: Principal | None = None,
    ) -> AgentState:
        principal = self._resolve_principal(principal)
        state = self.state_store.load(session_id)
        if not state:
            state = {
                "session_id": session_id,
                "status": "new",
                "next_agent": self.entry_agent,
                "shared": {},
                "history": [],
            }

        self._bind_session(state, principal)

        if message:
            state["last_message"] = message
            state.setdefault("messages", []).append({"role": "buyer", "content": message})
        if buyer_confirmed and state.get("status") == "awaiting_confirmation":
            state["status"] = "in_progress"

        context = _Context(
            state=state,
            message=message,
            buyer_confirmed=buyer_confirmed,
            principal=principal,
        )
        turns = 0
        # Exits when state is terminal, no handoff remains, an agent stops, or max_turns is reached.
        while turns < self.max_turns:
            if self._should_stop(state):
                break
            agent_name = state.get("next_agent")
            if not agent_name:
                break
            agent = self.agents.get(agent_name)
            if not agent:
                state["status"] = "stopped"
                state["stop_reason"] = "unknown_agent"
                state["last_response"] = f"Unknown next agent: {agent_name}."
                state["next_agent"] = None
                break
            if not self._authorize(state, agent, principal):
                break
            if not self._accept_handoff(state, agent, principal, session_id, context):
                break
            result = agent.run(context)
            state.setdefault("history", []).append(
                {
                    "agent": agent.name,
                    "response": result.response,
                    "next_agent": result.next_agent,
                    "client_id": principal.client_id,
                }
            )
            state["last_response"] = result.response
            state["next_agent"] = result.next_agent
            self._issue_handoff(state, agent.name, result.next_agent, principal, session_id)
            if result.stop:
                break
            turns += 1

        if turns >= self.max_turns and not self._should_stop(state) and state.get("next_agent"):
            state["status"] = "stopped"
            state["stop_reason"] = "max_turns"
            state["next_agent"] = None

        self.state_store.save(session_id, state)
        return state

    def _should_stop(self, state: AgentState) -> bool:
        return any(condition(state) for condition in self.stop_conditions)

    def _resolve_principal(self, principal: Principal | None) -> Principal:
        if not self.settings.auth_enabled:
            return principal or ANONYMOUS
        if principal is None:
            raise AuthError("An authenticated principal is required.", status_code=401)
        return principal

    def _bind_session(self, state: AgentState, principal: Principal) -> None:
        """Pin a session to the caller that created it and record its claims."""
        auth_state = state.get("auth") or {}
        if (
            self.settings.auth_enabled
            and self.settings.session_binding_enabled
            and auth_state.get("client_id")
            and auth_state["client_id"] != principal.client_id
        ):
            raise AuthError("Session belongs to a different client.", status_code=403)

        auth_state.update(principal.to_claims())
        auth_state.setdefault("delegation_chain", [])
        state["auth"] = auth_state

    def _required_scope(self, agent: Agent) -> str | None:
        return agent.required_scope or self.settings.scope_for(agent.name)

    def _authorize(self, state: AgentState, agent: Agent, principal: Principal) -> bool:
        if not self.settings.auth_enabled:
            return True
        scope = self._required_scope(agent)
        if principal.has_scope(scope):
            return True
        state["status"] = "stopped"
        state["stop_reason"] = "insufficient_scope"
        state["last_response"] = f"Client '{principal.client_id}' lacks scope '{scope}' required by agent '{agent.name}'."
        state["next_agent"] = None
        return False

    def _accept_handoff(
        self,
        state: AgentState,
        agent: Agent,
        principal: Principal,
        session_id: str,
        context: "_Context",
    ) -> bool:
        """Validate the signed credential handed over by the previous agent."""
        pending = (state.get("auth") or {}).get("pending_handoff")
        if not self.settings.auth_enabled or not pending:
            context.handoff_token = None
            return True

        token = pending.get("token", "")
        valid = pending.get("to") == agent.name and verify_handoff_token(
            token,
            principal,
            session_id,
            pending.get("from", ""),
            agent.name,
            self.settings,
        )
        if not valid:
            state["status"] = "stopped"
            state["stop_reason"] = "invalid_handoff_token"
            state["last_response"] = f"Rejected handoff to agent '{agent.name}': invalid pass-through token."
            state["next_agent"] = None
            return False

        context.handoff_token = token
        return True

    def _issue_handoff(
        self,
        state: AgentState,
        from_agent: str,
        to_agent: str | None,
        principal: Principal,
        session_id: str,
    ) -> None:
        auth_state = state.setdefault("auth", {})
        if not to_agent or not self.settings.auth_enabled:
            auth_state.pop("pending_handoff", None)
            return

        token = mint_handoff_token(principal, session_id, from_agent, to_agent, self.settings)
        auth_state["pending_handoff"] = {"from": from_agent, "to": to_agent, "token": token}
        auth_state.setdefault("delegation_chain", []).append(
            {
                "from": from_agent,
                "to": to_agent,
                "client_id": principal.client_id,
                "token_id": token.rpartition(".")[2][:16],
            }
        )


@dataclass
class _Context:
    state: AgentState
    message: str
    buyer_confirmed: bool
    principal: Principal = ANONYMOUS
    handoff_token: str | None = None


class StateStore(Protocol):
    def load(self, session_id: str) -> AgentState:
        ...

    def save(self, session_id: str, state: AgentState) -> None:
        ...
