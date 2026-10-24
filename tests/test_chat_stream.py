"""POST /chat/stream: same response shape as POST /chat, assembled from astream_events() instead
of invoke() -- see app/routes/chat_routes.py::_build_response (shared by both routes)."""
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.models.retrieval import Hit


@dataclass
class _Chunk:
    content: str


class FakeStreamGraph:
    """Mimics a compiled LangGraph's astream_events(): a few token events from the "answer" node,
    then the graph's own on_chain_end carrying the full merged state."""

    def __init__(self, tokens=("Thirty ", "days."), fail=False):
        self.tokens, self.fail = tokens, fail
        self.seen_states = []

    async def astream_events(self, state, version="v2"):
        self.seen_states.append(state)
        if self.fail:
            raise ConnectionError("ollama down")
        for t in self.tokens:
            yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "answer"},
                   "data": {"chunk": _Chunk(t)}}
        # A single node's own on_chain_end (no "route") must NOT be mistaken for the final state.
        yield {"event": "on_chain_end", "name": "answer", "data": {"output": {"answer": "".join(self.tokens)}}}
        yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": {
            "answer": "".join(self.tokens), "route": "document", "standalone": state["question"],
            "hits": [Hit("d1", "refund_policy.md", None, 0, 0.8, "30 days")],
            "sql": None,
        }}}


class _TestClient(TestClient):
    graph: FakeStreamGraph
    tmp: Path


@pytest.fixture
def client(tmp_path):
    object.__setattr__(main.settings, "documents_dir", str(tmp_path))
    graph = FakeStreamGraph()
    main.app.dependency_overrides[main.get_graph] = lambda: graph
    c = _TestClient(main.app)
    c.graph, c.tmp = graph, tmp_path
    yield c
    main.app.dependency_overrides.clear()


def _events(response):
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_emits_tokens_then_done_with_full_answer(client):
    r = client.post("/chat/stream", json={"message": "what is the refund policy?"})
    assert r.status_code == 200
    events = _events(r)
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert tokens == ["Thirty ", "days."]
    done = events[-1]
    assert done["type"] == "done" and done["answer"] == "Thirty days."
    assert done["route"] == "document" and done["sql"] is None
    assert done["sources"][0]["filename"] == "refund_policy.md"
    assert done["session_id"]


def test_stream_persists_history_like_the_sync_endpoint(client):
    first = _events(client.post("/chat/stream", json={"message": "first"}))[-1]
    client.post("/chat/stream", json={"message": "second", "session_id": first["session_id"]})
    assert client.graph.seen_states[1]["history"][0]["content"] == "first"


def test_stream_backend_failure_yields_error_event_not_a_500(client):
    client.graph.fail = True
    r = client.post("/chat/stream", json={"message": "hi"})
    assert r.status_code == 200  # SSE headers already sent; failure is in-band
    events = _events(r)
    assert events == [{"type": "error", "detail": "Language model backend is unavailable."}]


def test_stream_empty_message_rejected_before_any_graph_call(client):
    r = client.post("/chat/stream", json={"message": "   "})
    assert r.status_code == 422
    assert client.graph.seen_states == []


def test_stream_and_sync_endpoints_agree_on_response_shape(client):
    from app.routes.chat_routes import _build_response

    state = {"answer": "Thirty days.", "route": "document", "standalone": "what is the refund policy?",
              "hits": [Hit("d1", "refund_policy.md", None, 0, 0.8, "30 days")], "sql": None}
    direct = _build_response("sid", state).model_dump()
    streamed = _events(client.post("/chat/stream", json={"message": "what is the refund policy?"}))[-1]
    streamed.pop("type")
    streamed["session_id"] = "sid"
    assert streamed == direct
