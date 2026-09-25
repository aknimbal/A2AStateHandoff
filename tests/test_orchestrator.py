import tempfile
import unittest
from pathlib import Path

from function_app import _validate_payload
from handoff.orchestrator import build_orchestrator
from handoff.storage import InMemoryStateStore, JsonFileStateStore


class OrchestratorTests(unittest.TestCase):
    def test_handoff_waits_for_buyer_confirmation(self):
        state = build_orchestrator(InMemoryStateStore()).run("s1", "buy 2 widgets")

        self.assertEqual("awaiting_confirmation", state["status"])
        self.assertEqual("confirmation", state["next_agent"])
        self.assertEqual(3, len(state["history"]))
        self.assertNotIn("orders", state)

    def test_confirmed_buyer_completes_persisted_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "state.json"
            build_orchestrator(JsonFileStateStore(store_path)).run("persist-1", "buy 1 gadget")

            state = build_orchestrator(JsonFileStateStore(store_path)).run("persist-1", buyer_confirmed=True)

        self.assertEqual("completed", state["status"])
        self.assertIsNone(state["next_agent"])
        self.assertEqual("gadget", state["orders"][0]["item"])
        self.assertEqual("confirmation", state["history"][-1]["agent"])

    def test_stop_condition_prevents_unfulfillable_order(self):
        state = build_orchestrator(InMemoryStateStore()).run("s2", "buy 99 widgets")

        self.assertEqual("stopped", state["status"])
        self.assertEqual("insufficient_inventory", state["stop_reason"])
        self.assertIsNone(state["next_agent"])
        self.assertNotIn("orders", state)

    def test_unknown_persisted_agent_stops_cleanly(self):
        store = InMemoryStateStore()
        store.save("bad-agent", {"session_id": "bad-agent", "next_agent": "missing", "status": "in_progress"})

        state = build_orchestrator(store).run("bad-agent")

        self.assertEqual("stopped", state["status"])
        self.assertEqual("unknown_agent", state["stop_reason"])
        self.assertIsNone(state["next_agent"])

    def test_function_payload_validation_rejects_non_objects(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            _validate_payload([])

    def test_function_payload_validation_rejects_invalid_types(self):
        with self.assertRaisesRegex(ValueError, "message"):
            _validate_payload({"session_id": "s1", "message": 123})


if __name__ == "__main__":
    unittest.main()
