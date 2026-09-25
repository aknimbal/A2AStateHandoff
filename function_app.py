from __future__ import annotations

import json

from handoff import build_orchestrator

try:
    import azure.functions as func
except ImportError:  # pragma: no cover - Azure Functions imports this at runtime.
    func = None


def invoke_orchestrator(payload: dict) -> dict:
    session_id = payload.get("session_id") or "default"
    message = payload.get("message", "")
    buyer_confirmed = bool(payload.get("buyer_confirmed", False))
    state = build_orchestrator().run(session_id, message, buyer_confirmed=buyer_confirmed)
    return {
        "session_id": state["session_id"],
        "status": state["status"],
        "next_agent": state.get("next_agent"),
        "response": state.get("last_response"),
        "shared": state.get("shared", {}),
        "orders": state.get("orders", []),
        "history": state.get("history", []),
    }


if func:
    app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

    @app.route(route="orchestrate", methods=["POST"])
    def orchestrate(req: func.HttpRequest) -> func.HttpResponse:
        try:
            payload = req.get_json()
        except ValueError:
            return func.HttpResponse("Request body must be JSON.", status_code=400)
        result = invoke_orchestrator(payload)
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
