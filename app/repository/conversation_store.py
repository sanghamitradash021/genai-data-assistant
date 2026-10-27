"""In-process conversation memory: bounded LRU of sessions, each a bounded list of turns.

Scope, by design, for this single-process demo (see README "Known limits"):
  - Lost on restart, and not shared across workers/replicas - correct only because the API runs as a
    single `uvicorn` worker (no `--workers`, no horizontal scaling).
  - Two concurrent requests for the *same* session_id can interleave: each reads history before the
    other's answer is added, and turns land in completion order, not request order. The lock below only
    protects the dict itself from corruption, not this read-then-later-write race across requests. A
    real multi-request-per-session client would need per-session serialization or a shared, ordered
    store (e.g. Postgres, or the LangGraph checkpointer); out of scope for the assignment's follow-up
    use case (one request in flight per session at a time).
"""
import threading
from collections import OrderedDict


class ConversationStore:
    def __init__(self, max_turns: int = 6, max_sessions: int = 1000):
        self._max_msgs = max_turns * 2
        self._max_sessions = max_sessions
        self._data: OrderedDict[str, list[dict]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, session_id: str) -> list[dict]:
        with self._lock:
            return list(self._data.get(session_id, []))

    def add_turn(self, session_id: str, question: str, answer: str) -> None:
        """Append a turn, trim to the most recent max_turns, and evict the oldest session if over capacity."""
        with self._lock:
            msgs = self._data.pop(session_id, [])
            msgs += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer[:1500]},
            ]
            self._data[session_id] = msgs[-self._max_msgs :]
            while len(self._data) > self._max_sessions:
                self._data.popitem(last=False)
