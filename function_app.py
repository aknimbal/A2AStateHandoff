from __future__ import annotations

import json

from handoff import build_orchestrator

try:
    import azure.functions as func
except ImportError:  # pragma: no cover - Azure Functions imports this at runtime.
    func = None


def invoke_orchestrator(payload: object) -> dict:
    session_id, message, buyer_confirmed = _validate_payload(payload)
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


def _validate_payload(payload: object) -> tuple[str, str, bool]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    session_id = payload.get("session_id", "default")
    message = payload.get("message", "")
    buyer_confirmed = payload.get("buyer_confirmed", False)

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string.")
    if not isinstance(message, str):
        raise ValueError("message must be a string.")
    if not isinstance(buyer_confirmed, bool):
        raise ValueError("buyer_confirmed must be a boolean.")
    return session_id, message, buyer_confirmed


if func:
    app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

    @app.route(route="orchestrate", methods=["POST"])
    def orchestrate(req: func.HttpRequest) -> func.HttpResponse:
        try:
            payload = req.get_json()
            result = invoke_orchestrator(payload)
        except ValueError as exc:
            return func.HttpResponse(str(exc), status_code=400)
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
