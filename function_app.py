from __future__ import annotations

import json
from typing import Mapping

from handoff import AuthError, authenticate_request, build_orchestrator

try:
    import azure.functions as func
except ImportError:  # pragma: no cover - Azure Functions imports this at runtime.
    func = None


FORBIDDEN_STOP_REASONS = {"insufficient_scope", "invalid_handoff_token"}


def invoke_orchestrator(payload: object, headers: Mapping[str, str] | None = None) -> dict:
    session_id, message, buyer_confirmed = _validate_payload(payload)
    principal = authenticate_request(headers or {})
    state = build_orchestrator().run(
        session_id,
        message,
        buyer_confirmed=buyer_confirmed,
        principal=principal,
    )
    auth_state = state.get("auth", {})
    return {
        "session_id": state["session_id"],
        "status": state["status"],
        "stop_reason": state.get("stop_reason"),
        "next_agent": state.get("next_agent"),
        "response": state.get("last_response"),
        "shared": state.get("shared", {}),
        "orders": state.get("orders", []),
        "history": state.get("history", []),
        "auth": {
            "client_id": auth_state.get("client_id"),
            "scopes": auth_state.get("scopes", []),
            "delegation_chain": auth_state.get("delegation_chain", []),
        },
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
            result = invoke_orchestrator(payload, dict(req.headers))
        except AuthError as exc:
            return _error_response(str(exc), exc.status_code)
        except ValueError as exc:
            return _error_response(str(exc), 400)

        status_code = 403 if result.get("stop_reason") in FORBIDDEN_STOP_REASONS else 200
        return func.HttpResponse(json.dumps(result), status_code=status_code, mimetype="application/json")

    def _error_response(message: str, status_code: int) -> "func.HttpResponse":
        return func.HttpResponse(
            json.dumps({"error": message}),
            status_code=status_code,
            mimetype="application/json",
        )
