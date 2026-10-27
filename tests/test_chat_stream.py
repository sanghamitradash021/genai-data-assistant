"""POST /chat/stream: same response shape as POST /chat, assembled from astream(stream_mode=[...])
instead of invoke() -- see app/routes/chat_routes.py::_build_response (shared by both routes)."""
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.models.retrieval import Hit
from app.services.answer_service import scrub_citations


@dataclass
class _Chunk:
    content: str


class FakeStreamGraph:
    """Mimics a compiled LangGraph's astream(stream_mode=["messages", "values"]): "values" frames for
    the state after each node (rag/sql before the answer node, so hits/route are already known when the
    "answer" node's tokens start), interleaved with "messages" token frames from the "answer" node."""

    def __init__(self, tokens=("Thirty ", "days."), fail=False):
        self.tokens, self.fail = tokens, fail
        self.seen_states = []

    async def astream(self, state, stream_mode=None):
        self.seen_states.append(state)
        if self.fail:
            raise ConnectionError("ollama down")
        hits = [Hit("d1", "refund_policy.md", None, 0, 0.8, "30 days")]
        yield "values", {"route": "document", "standalone": state["question"], "hits": hits, "sql": None,
                          "answer": ""}
        for t in self.tokens:
            yield "messages", (_Chunk(t), {"langgraph_node": "answer"})
        # Mirrors the real "answer" node: it scrubs citations itself before returning `answer`, same as
        # the live per-token stream does independently - see CitationScrubber in app/routes/chat_routes.py.
        reply = scrub_citations("".join(self.tokens), hits)
        yield "values", {"answer": reply, "route": "document", "standalone": state["question"],
                          "hits": hits, "sql": None}


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


def test_stream_never_leaks_a_fabricated_citation_before_its_closing_bracket(client):
    """The model cites a file that was never retrieved (`hits` only has refund_policy.md); the client
    must never see that bracket on the wire, not even split across two token frames."""
    client.graph.tokens = ("Thirty days [made_", "up_file.md].")
    events = _events(client.post("/chat/stream", json={"message": "what is the refund policy?"}))
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "[" not in "".join(tokens)
    assert "".join(tokens) == "Thirty days."  # citation (and its leading space) dropped, same as the
    assert events[-1]["answer"] == "Thirty days."  # one-shot scrub_citations() used for `answer`


def test_stream_timeout_yields_a_distinct_error_from_backend_down(client):
    class _TimeoutGraph(FakeStreamGraph):
        async def astream(self, state, stream_mode=None):
            self.seen_states.append(state)
            raise httpx.ReadTimeout("model took too long")
            yield  # pragma: no cover - makes this an async generator

    client.graph = _TimeoutGraph()
    main.app.dependency_overrides[main.get_graph] = lambda: client.graph
    events = _events(client.post("/chat/stream", json={"message": "hi"}))
    assert events == [{"type": "error", "detail": "Language model backend timed out."}]


def test_stream_empty_message_rejected_before_any_graph_call(client):
    r = client.post("/chat/stream", json={"message": "   "})
    assert r.status_code == 422
    assert client.graph.seen_states == []


def test_stream_ignores_message_frames_from_nodes_other_than_answer(client):
    class _NoisyGraph(FakeStreamGraph):
        async def astream(self, state, stream_mode=None):
            self.seen_states.append(state)
            yield "messages", (_Chunk("condensed question text"), {"langgraph_node": "condense"})
            async for mode, chunk in super().astream(state, stream_mode):
                yield mode, chunk

    client.graph = _NoisyGraph()
    main.app.dependency_overrides[main.get_graph] = lambda: client.graph
    events = _events(client.post("/chat/stream", json={"message": "what is the refund policy?"}))
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "condensed question text" not in tokens and tokens == ["Thirty ", "days."]


def test_stream_internal_error_yields_a_generic_error_event(client):
    class _BrokenGraph(FakeStreamGraph):
        async def astream(self, state, stream_mode=None):
            self.seen_states.append(state)
            raise RuntimeError("unexpected bug")
            yield  # pragma: no cover - makes this an async generator

    client.graph = _BrokenGraph()
    main.app.dependency_overrides[main.get_graph] = lambda: client.graph
    r = client.post("/chat/stream", json={"message": "hi"})
    assert r.status_code == 200  # SSE headers already sent; failure is in-band
    assert _events(r) == [{"type": "error", "detail": "Internal error while answering."}]


def test_stream_with_no_final_values_frame_yields_an_error_not_a_crash(client):
    class _NoFinalStateGraph(FakeStreamGraph):
        async def astream(self, state, stream_mode=None):
            self.seen_states.append(state)
            for t in self.tokens:
                yield "messages", (_Chunk(t), {"langgraph_node": "answer"})
            # No trailing "values" frame at all - e.g. the graph was cancelled mid-stream.

    client.graph = _NoFinalStateGraph()
    main.app.dependency_overrides[main.get_graph] = lambda: client.graph
    events = _events(client.post("/chat/stream", json={"message": "hi"}))
    assert events[-1] == {"type": "error", "detail": "Internal error while answering."}


def test_stream_flushes_a_citation_left_unterminated_when_the_stream_ends(client):
    """The model's reply ends mid-citation (no closing ']'); CitationScrubber never had a complete
    bracket to validate, so the leftover text is flushed as-is rather than silently dropped."""
    client.graph.tokens = ("Thirty days [see refund_policy.md",)
    events = _events(client.post("/chat/stream", json={"message": "what is the refund policy?"}))
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "Thirty days [see refund_policy.md"


def test_stream_and_sync_endpoints_agree_on_response_shape(client):
    from app.routes.chat_routes import _build_response

    state = {"answer": "Thirty days.", "route": "document", "standalone": "what is the refund policy?",
              "hits": [Hit("d1", "refund_policy.md", None, 0, 0.8, "30 days")], "sql": None}
    direct = _build_response("sid", state).model_dump()
    streamed = _events(client.post("/chat/stream", json={"message": "what is the refund policy?"}))[-1]
    streamed.pop("type")
    streamed["session_id"] = "sid"
    assert streamed == direct
