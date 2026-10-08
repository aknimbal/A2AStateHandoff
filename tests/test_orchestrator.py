import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from function_app import _validate_payload, invoke_orchestrator
from handoff.auth import AuthError, authenticate
from handoff.config import ConfigError, get_settings, load_env_file, reset_settings_cache
from handoff.orchestrator import build_orchestrator
from handoff.storage import InMemoryStateStore, JsonFileStateStore


BUYER_TOKEN = "test-buyer-token"
INVENTORY_TOKEN = "test-inventory-token"

TEST_ENV = {
    "AUTH_ENABLED": "true",
    "AUTH_HEADER_NAME": "Authorization",
    "AUTH_SCHEME": "Bearer",
    "AUTH_SESSION_BINDING_ENABLED": "true",
    "HANDOFF_SIGNING_SECRET": "test-signing-secret",
    "AUTH_CLIENT_IDS": "buyer-portal,inventory-bot",
    "AUTH_CLIENT_BUYER_PORTAL_TOKEN": BUYER_TOKEN,
    "AUTH_CLIENT_BUYER_PORTAL_SCOPES": "intake.write,inventory.read,quote.read,order.write",
    "AUTH_CLIENT_INVENTORY_BOT_TOKEN": INVENTORY_TOKEN,
    "AUTH_CLIENT_INVENTORY_BOT_SCOPES": "intake.write,inventory.read",
    "AGENT_NAMES": "intake,inventory,quote,confirmation",
    "AGENT_ENTRY_POINT": "intake",
    "AGENT_SCOPE_INTAKE": "intake.write",
    "AGENT_SCOPE_INVENTORY": "inventory.read",
    "AGENT_SCOPE_QUOTE": "quote.read",
    "AGENT_SCOPE_CONFIRMATION": "order.write",
    "STATE_STORE_PATH": "",
}


class EnvTestCase(unittest.TestCase):
    env_overrides: dict[str, str] = {}

    def setUp(self):
        env = dict(TEST_ENV)
        env.update(self.env_overrides)
        patcher = mock.patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_settings_cache()
        self.addCleanup(reset_settings_cache)

    def buyer(self):
        return authenticate(BUYER_TOKEN, get_settings())

    def inventory_bot(self):
        return authenticate(INVENTORY_TOKEN, get_settings())


class OrchestratorTests(EnvTestCase):
    def test_handoff_waits_for_buyer_confirmation(self):
        state = build_orchestrator(InMemoryStateStore()).run("s1", "buy 2 widgets", principal=self.buyer())

        self.assertEqual("awaiting_confirmation", state["status"])
        self.assertEqual("confirmation", state["next_agent"])
        self.assertEqual(3, len(state["history"]))
        self.assertNotIn("orders", state)

    def test_confirmed_buyer_completes_persisted_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "state.json"
            build_orchestrator(JsonFileStateStore(store_path)).run(
                "persist-1", "buy 1 gadget", principal=self.buyer()
            )

            state = build_orchestrator(JsonFileStateStore(store_path)).run(
                "persist-1", buyer_confirmed=True, principal=self.buyer()
            )

        self.assertEqual("completed", state["status"])
        self.assertIsNone(state["next_agent"])
        self.assertEqual("gadget", state["orders"][0]["item"])
        self.assertEqual("confirmation", state["history"][-1]["agent"])

    def test_stop_condition_prevents_unfulfillable_order(self):
        state = build_orchestrator(InMemoryStateStore()).run("s2", "buy 99 widgets", principal=self.buyer())

        self.assertEqual("stopped", state["status"])
        self.assertEqual("insufficient_inventory", state["stop_reason"])
        self.assertIsNone(state["next_agent"])
        self.assertNotIn("orders", state)

    def test_unknown_persisted_agent_stops_cleanly(self):
        store = InMemoryStateStore()
        store.save("bad-agent", {"session_id": "bad-agent", "next_agent": "missing", "status": "in_progress"})

        state = build_orchestrator(store).run("bad-agent", principal=self.buyer())

        self.assertEqual("stopped", state["status"])
        self.assertEqual("unknown_agent", state["stop_reason"])
        self.assertIsNone(state["next_agent"])


class AuthenticationTests(EnvTestCase):
    def test_missing_credential_is_rejected(self):
        with self.assertRaises(AuthError) as ctx:
            authenticate("", get_settings())
        self.assertEqual(401, ctx.exception.status_code)

    def test_invalid_credential_is_rejected(self):
        with self.assertRaises(AuthError) as ctx:
            authenticate("not-a-valid-token", get_settings())
        self.assertEqual(401, ctx.exception.status_code)

    def test_orchestrator_requires_a_principal(self):
        with self.assertRaises(AuthError) as ctx:
            build_orchestrator(InMemoryStateStore()).run("s3", "buy 1 widget")
        self.assertEqual(401, ctx.exception.status_code)

    def test_missing_config_is_reported(self):
        with mock.patch.dict(os.environ, {"HANDOFF_SIGNING_SECRET": ""}, clear=False):
            reset_settings_cache()
            with self.assertRaisesRegex(ConfigError, "HANDOFF_SIGNING_SECRET"):
                get_settings()


class AuthorizationTests(EnvTestCase):
    def test_insufficient_scope_stops_the_chain(self):
        state = build_orchestrator(InMemoryStateStore()).run(
            "scope-1", "buy 1 widget", principal=self.inventory_bot()
        )

        self.assertEqual("stopped", state["status"])
        self.assertEqual("insufficient_scope", state["stop_reason"])
        self.assertIsNone(state["next_agent"])
        self.assertEqual(["intake", "inventory"], [entry["agent"] for entry in state["history"]])

    def test_session_is_bound_to_the_creating_client(self):
        store = InMemoryStateStore()
        build_orchestrator(store).run("bound-1", "buy 1 widget", principal=self.buyer())

        with self.assertRaises(AuthError) as ctx:
            build_orchestrator(store).run("bound-1", principal=self.inventory_bot())
        self.assertEqual(403, ctx.exception.status_code)

    def test_session_binding_can_be_disabled(self):
        store = InMemoryStateStore()
        build_orchestrator(store).run("bound-2", "buy 1 widget", principal=self.buyer())

        with mock.patch.dict(os.environ, {"AUTH_SESSION_BINDING_ENABLED": "false"}, clear=False):
            reset_settings_cache()
            state = build_orchestrator(store).run("bound-2", principal=self.inventory_bot())

        self.assertEqual("inventory-bot", state["auth"]["client_id"])


class PassThroughTests(EnvTestCase):
    def test_delegation_chain_records_every_hop(self):
        state = build_orchestrator(InMemoryStateStore()).run("chain-1", "buy 2 widgets", principal=self.buyer())

        chain = state["auth"]["delegation_chain"]
        self.assertEqual(
            [("intake", "inventory"), ("inventory", "quote"), ("quote", "confirmation")],
            [(hop["from"], hop["to"]) for hop in chain],
        )
        self.assertTrue(all(hop["client_id"] == "buyer-portal" for hop in chain))
        self.assertEqual("confirmation", state["auth"]["pending_handoff"]["to"])

    def test_agents_receive_the_passed_through_credential(self):
        seen: list[tuple[str, str | None]] = []
        orchestrator = build_orchestrator(InMemoryStateStore())
        for name, agent in orchestrator.agents.items():
            handler = agent.handler

            def wrapped(context, _name=name, _handler=handler):
                seen.append((_name, context.handoff_token))
                self.assertEqual("buyer-portal", context.principal.client_id)
                return _handler(context)

            orchestrator.agents[name] = type(agent)(agent.name, agent.instructions, wrapped, agent.required_scope)

        orchestrator.run("chain-2", "buy 1 widget", principal=self.buyer())

        self.assertIsNone(seen[0][1])
        self.assertTrue(all(token for _, token in seen[1:]))

    def test_tampered_handoff_token_is_rejected(self):
        store = InMemoryStateStore()
        build_orchestrator(store).run("tamper-1", "buy 1 widget", principal=self.buyer())
        tampered = store.load("tamper-1")
        tampered["auth"]["pending_handoff"]["token"] = tampered["auth"]["pending_handoff"]["token"][:-4] + "0000"
        store.save("tamper-1", tampered)

        state = build_orchestrator(store).run("tamper-1", buyer_confirmed=True, principal=self.buyer())

        self.assertEqual("stopped", state["status"])
        self.assertEqual("invalid_handoff_token", state["stop_reason"])
        self.assertNotIn("orders", state)


class AuthDisabledTests(EnvTestCase):
    env_overrides = {"AUTH_ENABLED": "false"}

    def test_chain_runs_without_credentials(self):
        state = build_orchestrator(InMemoryStateStore()).run("open-1", "buy 1 widget")

        self.assertEqual("awaiting_confirmation", state["status"])
        self.assertEqual("anonymous", state["auth"]["client_id"])


class ConfigLoaderTests(EnvTestCase):
    def test_env_file_is_parsed_without_overriding_the_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                '# comment\nexport AUTH_SCHEME="Token"\nSAMPLE_NEW_KEY=sample-value\ninvalid-line\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"AUTH_SCHEME": "Bearer"}, clear=False):
                loaded = load_env_file(env_file)
                self.assertEqual("Token", loaded["AUTH_SCHEME"])
                self.assertEqual("Bearer", os.environ["AUTH_SCHEME"])
                self.assertEqual("sample-value", os.environ["SAMPLE_NEW_KEY"])
            os.environ.pop("SAMPLE_NEW_KEY", None)


class FunctionAppTests(EnvTestCase):
    def test_function_payload_validation_rejects_non_objects(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            _validate_payload([])

    def test_function_payload_validation_rejects_invalid_types(self):
        with self.assertRaisesRegex(ValueError, "message"):
            _validate_payload({"session_id": "s1", "message": 123})

    def test_invoke_orchestrator_requires_an_authorization_header(self):
        with self.assertRaises(AuthError) as ctx:
            invoke_orchestrator({"session_id": "http-401", "message": "buy 1 widget"}, {})
        self.assertEqual(401, ctx.exception.status_code)

    def test_invoke_orchestrator_shapes_http_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(
                os.environ, {"STATE_STORE_PATH": str(Path(temp_dir) / "state.json")}, clear=False
            ):
                reset_settings_cache()
                result = invoke_orchestrator(
                    {"session_id": "http-1", "message": "buy 1 widget"},
                    {"authorization": f"Bearer {BUYER_TOKEN}"},
                )

        self.assertEqual("http-1", result["session_id"])
        self.assertEqual("awaiting_confirmation", result["status"])
        self.assertEqual("confirmation", result["next_agent"])
        self.assertIn("Quote ready", result["response"])
        self.assertEqual("widget", result["shared"]["quote"]["item"])
        self.assertEqual([], result["orders"])
        self.assertEqual(["intake", "inventory", "quote"], [entry["agent"] for entry in result["history"]])
        self.assertEqual("buyer-portal", result["auth"]["client_id"])
        self.assertEqual(3, len(result["auth"]["delegation_chain"]))


if __name__ == "__main__":
    unittest.main()
