from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

from .agent_framework import AgentState


class InMemoryStateStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AgentState] = {}

    def load(self, session_id: str) -> AgentState:
        return json.loads(json.dumps(self._sessions.get(session_id, {})))

    def save(self, session_id: str, state: AgentState) -> None:
        self._sessions[session_id] = json.loads(json.dumps(state))


class JsonFileStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def load(self, session_id: str) -> AgentState:
        with self._lock:
            data = self._read_all()
            return data.get(session_id, {})

    def save(self, session_id: str, state: AgentState) -> None:
        with self._lock:
            data = self._read_all()
            data[session_id] = state
            self.path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(data, indent=2, sort_keys=True)
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w",
                    delete=False,
                    dir=self.path.parent,
                    encoding="utf-8",
                ) as temp_file:
                    temp_file.write(content)
                    temp_name = temp_file.name
                os.replace(temp_name, self.path)
            except Exception:
                if temp_name:
                    Path(temp_name).unlink(missing_ok=True)
                raise

    def _read_all(self) -> dict[str, AgentState]:
        if not self.path.exists():
            return {}
        content = self.path.read_text(encoding="utf-8")
        if not content.strip():
            return {}
        return json.loads(content)
