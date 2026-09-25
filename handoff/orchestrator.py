from __future__ import annotations

import argparse
import os
import re
import tempfile
from typing import Any

from .agent_framework import Agent, AgentContext, AgentResult, HandoffOrchestrator, StateStore
from .storage import InMemoryStateStore, JsonFileStateStore


CATALOG = {
    "widget": {"price": 19.99, "available": 12},
    "gadget": {"price": 34.50, "available": 5},
}


def build_orchestrator(state_store: StateStore | None = None) -> HandoffOrchestrator:
    return HandoffOrchestrator(
        agents=[
            Agent("intake", "Capture buyer intent and normalize the order request.", _intake_agent),
            Agent("inventory", "Validate requested inventory from shared state.", _inventory_agent),
            Agent("quote", "Prepare a quote and require buyer confirmation.", _quote_agent),
            Agent("confirmation", "Create the order only after explicit buyer confirmation.", _confirmation_agent),
        ],
        state_store=state_store or _default_store(),
        stop_conditions=[_is_terminal, _awaits_confirmation],
    )


def _default_store() -> JsonFileStateStore:
    default_path = os.path.join(tempfile.gettempdir(), "a2a_state_handoff", "state.json")
    path = os.environ.get("STATE_STORE_PATH", default_path)
    return JsonFileStateStore(path)


def _intake_agent(context: AgentContext) -> AgentResult:
    request = context.message or context.state.get("last_message", "")
    item, quantity = _parse_request(request)
    shared = context.state.setdefault("shared", {})
    shared["request"] = {"item": item, "quantity": quantity}
    context.state["status"] = "in_progress"
    if not item:
        context.state["status"] = "stopped"
        context.state["stop_reason"] = "missing_item"
        return AgentResult("Tell me which catalog item you want to buy.", stop=True)
    return AgentResult(f"Captured buyer request for {quantity} {item}.", next_agent="inventory")


def _inventory_agent(context: AgentContext) -> AgentResult:
    request = context.state["shared"]["request"]
    catalog_item = CATALOG.get(request["item"])
    if not catalog_item:
        context.state["status"] = "stopped"
        context.state["stop_reason"] = "unknown_item"
        return AgentResult(f"{request['item']} is not in the catalog.", stop=True)
    if request["quantity"] > catalog_item["available"]:
        context.state["status"] = "stopped"
        context.state["stop_reason"] = "insufficient_inventory"
        return AgentResult(f"Only {catalog_item['available']} {request['item']} are available.", stop=True)

    context.state["shared"]["inventory"] = catalog_item
    return AgentResult(f"Inventory confirmed for {request['quantity']} {request['item']}.", next_agent="quote")


def _quote_agent(context: AgentContext) -> AgentResult:
    request = context.state["shared"]["request"]
    inventory = context.state["shared"]["inventory"]
    total = round(request["quantity"] * inventory["price"], 2)
    context.state["shared"]["quote"] = {
        "item": request["item"],
        "quantity": request["quantity"],
        "unit_price": inventory["price"],
        "total": total,
    }
    context.state["status"] = "awaiting_confirmation"
    return AgentResult(
        f"Quote ready: {request['quantity']} {request['item']} for ${total:.2f}. Confirm to place the order.",
        next_agent="confirmation",
        stop=True,
    )


def _confirmation_agent(context: AgentContext) -> AgentResult:
    if not context.buyer_confirmed:
        context.state["status"] = "awaiting_confirmation"
        return AgentResult("Waiting for buyer confirmation before creating the order.", next_agent="confirmation", stop=True)

    quote = context.state["shared"]["quote"]
    order_count = len(context.state.get("orders", [])) + 1
    order = {
        "id": f"ORD-{context.state['session_id'][:8]}-{order_count}",
        "item": quote["item"],
        "quantity": quote["quantity"],
        "total": quote["total"],
    }
    context.state.setdefault("orders", []).append(order)
    context.state["status"] = "completed"
    return AgentResult(f"Order {order['id']} placed for ${order['total']:.2f}.", stop=True)


def _parse_request(message: str) -> tuple[str | None, int]:
    lowered = message.lower()
    quantity_match = re.search(r"\b(\d+)\b", lowered)
    quantity = int(quantity_match.group(1)) if quantity_match else 1
    for item in CATALOG:
        if item in lowered or f"{item}s" in lowered:
            return item, quantity
    return None, quantity


def _is_terminal(state: dict[str, Any]) -> bool:
    return state.get("status") in {"completed", "stopped"}


def _awaits_confirmation(state: dict[str, Any]) -> bool:
    return state.get("status") == "awaiting_confirmation"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the A2A state handoff orchestrator.")
    parser.add_argument("message", nargs="?", default="")
    parser.add_argument("--session-id", default="local-session")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--memory", action="store_true", help="Use in-memory storage instead of JSON persistence.")
    args = parser.parse_args()

    store = InMemoryStateStore() if args.memory else None
    state = build_orchestrator(store).run(args.session_id, args.message, buyer_confirmed=args.confirm)
    print(state["last_response"])


if __name__ == "__main__":
    main()
