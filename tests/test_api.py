from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.models.retrieval import Hit
from app.models.sql import SQLResult
from app.schemas.documents import DocumentInfo


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

    async def ainvoke(self, state):
        return self.invoke(state)


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


class _TestClient(TestClient):
    """TestClient plus the fakes/tmp dir each test needs, declared so attribute access type-checks."""

    graph: FakeGraph
    store: FakeStore
    tmp: Path


@pytest.fixture
def client(tmp_path):
    object.__setattr__(main.settings, "documents_dir", str(tmp_path))  # frozen pydantic settings
    graph, store = FakeGraph(), FakeStore()
    main.app.dependency_overrides[main.get_graph] = lambda: graph
    main.app.dependency_overrides[main.get_store] = lambda: store
    c = _TestClient(main.app)
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


def test_chat_whitespace_only_message_is_422_after_sanitizing(client):
    # Passes Pydantic's min_length=1 (it's non-empty text) but sanitize_message() strips it to "" -
    # a second, code-level empty-message check, not just schema validation.
    assert client.post("/chat", json={"message": "   "}).status_code == 422


def test_chat_pipeline_failure_is_a_500_not_an_unhandled_exception(client):
    async def boom(state):
        raise RuntimeError("something inside the graph broke")

    client.graph.ainvoke = boom
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 500


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


def test_ingest_upload_of_a_corrupt_file_is_a_422_not_a_500(client):
    """store.ingest_file() raises the real VectorStore's ValueError for a corrupt PDF/DOCX (see
    test_documents.py); the route must turn that into a 422 and clean up the half-written upload, not
    let it fall through as an unhandled exception -> generic 500."""

    def ingest_file(path):
        raise ValueError("Could not read PDF (corrupt or unsupported): bad stream")

    client.store.ingest_file = ingest_file
    r = client.post("/documents/ingest", files={"file": ("bad.pdf", b"not a real pdf", "application/pdf")})
    assert r.status_code == 422
    assert not (client.tmp / "bad.pdf").exists()


def test_ingest_reuploading_a_corrupt_file_does_not_destroy_the_existing_good_one(client):
    """Regression: re-uploading a file under a name that's already on disk used to write the new (bad)
    bytes straight over the old file, then delete it on failure - losing the previously-good content with
    no way to recover it, even though its vectors were still indexed. The new content must be validated
    in a temp location before it ever touches the real file."""
    existing = client.tmp / "leave_policy.md"
    existing.write_text("ORIGINAL GOOD CONTENT")

    def ingest_file(path):
        raise ValueError("Could not read PDF (corrupt or unsupported): bad stream")

    client.store.ingest_file = ingest_file
    r = client.post("/documents/ingest", files={"file": ("leave_policy.md", b"corrupt replacement", "text/markdown")})
    assert r.status_code == 422
    assert existing.read_text() == "ORIGINAL GOOD CONTENT"  # untouched, not overwritten then deleted
    assert list(client.tmp.iterdir()) == [existing]  # no leftover temp file/directory either


def test_ingest_file_too_large_is_413(client):
    original = main.settings.max_upload_bytes
    object.__setattr__(main.settings, "max_upload_bytes", 10)
    try:
        r = client.post("/documents/ingest", files={"file": ("big.txt", b"x" * 20, "text/plain")})
    finally:
        object.__setattr__(main.settings, "max_upload_bytes", original)
    assert r.status_code == 413
    assert not (client.tmp / "big.txt").exists()


def test_ingest_backend_unavailable_during_ingest_is_503(client):
    def ingest_file(path):
        raise ConnectionError("qdrant unreachable")

    client.store.ingest_file = ingest_file
    r = client.post("/documents/ingest", files={"file": ("a.md", b"hello", "text/markdown")})
    assert r.status_code == 503


def test_ingest_two_different_files_concurrently_both_succeed(client):
    """Not a deep race-condition stress test - just confirms concurrent uploads of different filenames
    don't clobber each other's temp files (each gets its own temp directory) or the response."""
    import concurrent.futures

    def upload(name):
        return client.post("/documents/ingest", files={"file": (name, b"content", "text/plain")})

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, ["one.txt", "two.txt"]))
    assert [r.status_code for r in results] == [200, 200]
    assert (client.tmp / "one.txt").read_bytes() == b"content"
    assert (client.tmp / "two.txt").read_bytes() == b"content"


def test_ingest_directory_skips_indexed(client):
    (client.tmp / "new.md").write_text("x")
    (client.tmp / "ignored.bin").write_text("x")
    r = client.post("/documents/ingest").json()
    assert [d["filename"] for d in r["ingested"]] == ["new.md"]


def test_live(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_chat_backend_timeout_is_a_504_distinct_from_backend_down(client):
    """A hung Ollama (ChatOllama's client_kwargs={"timeout": ...}) raises httpx.TimeoutException, which
    must map to 504 - distinct from httpx.ConnectError/ConnectionError (Ollama not running at all), which
    stays a 503. Flapping between the two would otherwise make an operator chase the wrong failure mode."""
    from app.utils.rate_limit import limiter

    async def timed_out(state):
        raise httpx.ReadTimeout("model took too long")

    client.graph.ainvoke = timed_out
    assert client.post("/chat", json={"message": "hi"}).status_code == 504

    async def down(state):
        raise ConnectionError("Ollama not running")

    client.graph.ainvoke = down
    assert client.post("/chat", json={"message": "hi"}).status_code == 503
    limiter.reset()  # these 2 extra /chat calls must not eat into other tests' shared rate-limit quota


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
