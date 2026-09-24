"""In-process conversation memory: bounded LRU of sessions, each a bounded list of turns."""
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
        with self._lock:
            msgs = self._data.pop(session_id, [])
            msgs += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer[:1500]},
            ]
            self._data[session_id] = msgs[-self._max_msgs :]
            while len(self._data) > self._max_sessions:
                self._data.popitem(last=False)
