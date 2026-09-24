import pytest
from fastapi.testclient import TestClient

from app import main
from app.models import Hit, SQLResult
from app.schemas import DocumentInfo


class FakeGraph:
    def __init__(self, route="both"):
        self.route, self.seen = route, []

    def invoke(self, state):
        self.seen.append(state)
        return {
            "answer": "ans", "route": self.route, "standalone": state["question"],
            "hits": [Hit("d1", "refund_policy.md", None, 0, 0.8, "30 days"),
                     Hit("d1", "refund_policy.md", None, 1, 0.7, "more")],
            "sql": SQLResult("q", sql="SELECT 1", columns=["n"], rows=[[1]]),
        }


class FakeStore:
    def __init__(self):
        self.docs = {"d1": DocumentInfo(id="d1", filename="a.txt", file_type="txt", chunks=2, ingested_at="now")}
        self.ingested = []

    def list_documents(self): return list(self.docs.values())
    def get_document(self, i): return self.docs.get(i)
    def delete_vectors(self, i): self.docs.pop(i, None)
    def ingest_file(self, path):
        self.ingested.append(path.name)
        return DocumentInfo(id="n", filename=path.name, file_type="txt", chunks=1, ingested_at="now")
    def ping(self): pass
    def stale_doc_ids(self): return set()


@pytest.fixture
def client(tmp_path):
    object.__setattr__(main.settings, "documents_dir", str(tmp_path))  # frozen dataclass
    graph, store = FakeGraph(), FakeStore()
    main.app.dependency_overrides[main.get_graph] = lambda: graph
    main.app.dependency_overrides[main.get_store] = lambda: store
    c = TestClient(main.app)
    c.graph, c.store, c.tmp = graph, store, tmp_path
    yield c
    main.app.dependency_overrides.clear()


def test_chat_returns_answer_sources_sql_and_session(client):
    r = client.post("/chat", json={"message": "hi"}).json()
    assert r["answer"] == "ans" and r["session_id"]
    assert len(r["sources"]) == 1 and r["sources"][0]["filename"] == "refund_policy.md"  # deduped
    assert r["sql"]["query"] == "SELECT 1" and r["sql"]["row_count"] == 1


def test_chat_history_carried_across_turns(client):
    sid = client.post("/chat", json={"message": "first"}).json()["session_id"]
    client.post("/chat", json={"message": "second", "session_id": sid})
    assert client.graph.seen[1]["history"][0]["content"] == "first"


@pytest.mark.parametrize("body", [{"message": ""}, {"message": "x" * 2001}, {"message": "a", "session_id": "bad id!"}])
def test_chat_validation(client, body):
    assert client.post("/chat", json=body).status_code == 422


def test_list_and_delete_document(client):
    (client.tmp / "a.txt").write_text("x")
    assert client.get("/documents").json()[0]["id"] == "d1"
    assert client.delete("/documents/d1").status_code == 200
    assert not (client.tmp / "a.txt").exists()
    assert client.delete("/documents/nope").status_code == 404


def test_ingest_upload_and_rejects(client):
    ok = client.post("/documents/ingest", files={"file": ("../evil.txt", b"hello", "text/plain")})
    assert ok.status_code == 200 and (client.tmp / "evil.txt").exists()
    bad = client.post("/documents/ingest", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 415


def test_ingest_directory_skips_indexed(client):
    (client.tmp / "new.md").write_text("x")
    (client.tmp / "ignored.bin").write_text("x")
    r = client.post("/documents/ingest").json()
    assert [d["filename"] for d in r["ingested"]] == ["new.md"]


def test_live(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_sources_expose_section_and_heading_but_hide_debug_by_default(client):
    class G(FakeGraph):
        def invoke(self, state):
            out = super().invoke(state)
            out["hits"] = [Hit("d1", "CLAUDEEE.md", None, 76, 0.84, "ORM: Prisma 7", "Database", "Database",
                               "Frontend Standards > Database", 0.8, 1.0, ["ORM"])]
            return out
    main.app.dependency_overrides[main.get_graph] = lambda: G()
    src = client.post("/chat", json={"message": "which ORM is used?"}).json()["sources"][0]
    assert (src["filename"], src["section"], src["heading"], src["score"]) == ("CLAUDEEE.md", "Database", "Database", 0.84)
    assert src["debug"] is None


def test_debug_details_are_exposed_only_when_rag_debug_is_on(client):
    object.__setattr__(main.settings, "rag_debug", True)
    try:
        src = client.post("/chat", json={"message": "hi"}).json()["sources"][0]
    finally:
        object.__setattr__(main.settings, "rag_debug", False)
    assert set(src["debug"]) == {"chunk_index", "semantic", "keyword", "final", "matched", "heading_path"}


def test_sources_from_different_markdown_sections_are_all_returned(client):
    class G(FakeGraph):
        def invoke(self, state):
            out = super().invoke(state)
            out["hits"] = [Hit("d1", "C.md", None, i, 0.8, "t", s, s, f"Doc > {s}") for i, s in enumerate(["Rules", "Testing"])]
            return out
    main.app.dependency_overrides[main.get_graph] = lambda: G()
    secs = [s["section"] for s in client.post("/chat", json={"message": "rules?"}).json()["sources"]]
    assert secs == ["Rules", "Testing"]
