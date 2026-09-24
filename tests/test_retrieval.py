"""Retrieval pipeline tests. Unit tests on the pure ranking functions, plus end-to-end tests of
VectorStore.search on the real CLAUDEEE.md and the sample documents, using an in-memory Qdrant and a
deterministic bag-of-words embedder (so no Ollama is needed; semantic quality itself is checked live)."""
import hashlib
import math
import re
from dataclasses import replace
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app import retrieval, vectorstore
from app.config import settings
from app.models import Hit

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
DOCS = ROOT / "data" / "documents"


def hit(filename="a.md", text="t", section=None, sem=0.7, idx=0, heading=None, path=None):
    return Hit("id", filename, None, idx, sem, text, section, heading or section, path or section, semantic=sem)


# ---------------------------------------------------------------- unit: terms / keywords ------------------
def test_query_terms_drop_stopwords_and_filenames():
    assert list(retrieval.query_terms("which ORM is used in CLAUDEEE.md?")) == ["orm"]
    assert "the" not in retrieval.query_terms("what are the rules of the frontend")


def test_tokens_normalise_case_plurals_and_compounds():
    t = retrieval.tokens("Prisma 7 with PostgreSQL adapter (`@prisma/adapter-pg`) Rules")
    assert {"prisma", "postgresql", "adapter-pg", "adapter", "pg", "rule"} <= t


def test_keyword_score_exact_term_beats_unrelated_text():
    hits = [hit("c.md", "ORM: Prisma 7 with PostgreSQL adapter", "Database"), hit("r.md", "refund within 30 days")]
    (a, matched), (b, _) = retrieval.keyword_scores("which ORM is used?", hits)
    assert a == 1.0 and matched == ["ORM"] and b == 0.0


def test_rare_terms_weigh_more_than_terms_every_chunk_has():
    hits = [hit(text="frontend rules", idx=0), hit(text="frontend layout", idx=1), hit(text="frontend colors", idx=2)]
    s = [k for k, _ in retrieval.keyword_scores("frontend rules", hits)]
    assert s[0] > s[1] and s[1] == s[2]


def test_root_title_in_breadcrumb_does_not_count_as_a_match():
    h = hit(text="Use pnpm", section="Rules", path="Frontend Standards > Rules", heading="Rules")
    (score, matched), = retrieval.keyword_scores("frontend", [h])
    assert score == 0.0 and matched == []


def test_heading_match_scores_higher_than_body_match():
    a = hit(text="always use pnpm", section="Rules", heading="Rules", path="Doc > Rules", idx=0)
    b = hit(text="the rules are listed elsewhere", section="Other", heading="Other", path="Doc > Other", idx=1)
    (sa, _), (sb, _) = retrieval.keyword_scores("rules", [a, b])
    assert sa >= sb


# ---------------------------------------------------------------- unit: filenames --------------------------
KNOWN = ["CLAUDEEE.md", "README.md", "refund_policy.md", "shipping_faq.md"]


@pytest.mark.parametrize("q,expected", [
    ("which ORM is used in CLAUDEEE.md?", ["CLAUDEEE.md"]),
    ("what are the rules in claudeee.md.", ["CLAUDEEE.md"]),
    ("according to README.md, how do I run it", ["README.md"]),
    ("what does refund_policy say about limits", ["refund_policy.md"]),
    ("summarise shipping_faq.md and README.md", ["README.md", "shipping_faq.md"]),
    ("what is the refund policy?", []),
    ("which ORM is used?", []),
])
def test_detect_filenames(q, expected):
    assert sorted(retrieval.detect_filenames(q, KNOWN)) == sorted(expected)


# ---------------------------------------------------------------- unit: ranking ---------------------------
def test_semantic_only_chunk_below_threshold_is_dropped_but_lexical_evidence_saves_a_lower_score():
    unrelated = hit("refund_policy.md", "refund within 30 days", sem=0.55)
    relevant = hit("c.md", "ORM: Prisma 7", "Database", sem=0.5, idx=1)
    out = retrieval.rank_candidates("which ORM is used?", [unrelated, relevant])
    assert [h.filename for h in out] == ["c.md"]  # 0.5 < RAG_MIN_SCORE but kw=1.0, refund chunk has neither


def test_nothing_relevant_returns_nothing():
    assert retrieval.rank_candidates("which ORM is used?", [hit("r.md", "refund", sem=0.5), hit("s.md", "ship", sem=0.4)]) == []


def test_below_semantic_floor_is_dropped_even_with_keywords():
    assert retrieval.rank_candidates("orm", [hit(text="orm", sem=settings.semantic_floor - 0.05)]) == []


def test_high_cosine_alone_still_passes_min_score():
    out = retrieval.rank_candidates("something odd", [hit(text="unrelated", sem=settings.min_score + 0.05)])
    assert len(out) == 1  # the pre-existing semantic threshold keeps working


def test_named_document_wins_and_others_are_only_a_fallback():
    a, b = hit("CLAUDEEE.md", "ORM Prisma", "Database", 0.6), hit("refund_policy.md", "ORM refund", None, 0.8, 1)
    assert [h.filename for h in retrieval.rank_candidates("ORM in CLAUDEEE.md", [a, b], ["CLAUDEEE.md"])] == ["CLAUDEEE.md"]
    # named document has no candidate at all -> fall back to what exists
    assert [h.filename for h in retrieval.rank_candidates("ORM in X.md", [b], ["X.md"])] == ["refund_policy.md"]


def test_final_score_uses_configurable_weights():
    h = hit(text="orm", sem=0.6)
    lo = replace(settings, semantic_weight=1.0, keyword_weight=0.0)
    hi = replace(settings, semantic_weight=0.0, keyword_weight=1.0)
    assert retrieval.rank_candidates("orm", [h], cfg=lo)[0].score == 0.6
    assert retrieval.rank_candidates("orm", [h], cfg=hi)[0].score == 1.0


def test_select_context_enforces_cutoff_section_diversity_and_size():
    ranked = [replace(hit(section="A", idx=i), score=0.9 - i * 0.01) for i in range(5)]
    ranked += [replace(hit(section="B", idx=9), score=0.85), replace(hit(section="C", idx=10), score=0.4)]
    out = retrieval.select_context(ranked, k=10)
    secs = [h.section for h in out]
    assert secs.count("A") == settings.max_chunks_per_section and "B" in secs and "C" not in secs  # cutoff
    assert len(retrieval.select_context(ranked, k=2)) == 2
    assert "C" not in [h.section for h in retrieval.select_context(ranked, k=10, broad=True)]  # 0.4 < 0.6*0.9
    ranked[-1] = replace(ranked[-1], score=0.6)
    assert "C" in [h.section for h in retrieval.select_context(ranked, k=10, broad=True)]  # looser for broad


def test_broad_question_detection():
    assert retrieval.is_broad("what are the rules used in the Entegris frontend?")
    assert retrieval.is_broad("summarize the conventions")
    assert not retrieval.is_broad("which ORM is used?")


def test_query_expansion_maps_vague_concepts_to_document_vocabulary():
    assert "tech stack" in retrieval.expand_query("what framework is used?")
    assert retrieval.expand_query("what is the refund policy?") == ""


def test_debug_logging_lists_candidates_with_scores(caplog):
    cfg = replace(settings, rag_debug=True)
    with caplog.at_level("INFO", logger="app.retrieval"):
        retrieval.rank_candidates("which ORM is used?", [hit("c.md", "ORM Prisma", "Database", 0.7)], cfg=cfg)
    text = caplog.text
    assert "Query: which ORM is used?" in text and "Section: Database" in text
    assert "Semantic: 0.70" in text and "Keyword: 1.00" in text and "Matched: ORM" in text


# ---------------------------------------------------------------- end to end (in-memory Qdrant) -----------
class BagOfWords:
    """Deterministic embedder: hashed, IDF-weighted bag of (stemmed) content words, L2 normalised.
    Document frequencies accumulate as documents are embedded (unseen words count as rare)."""
    DIM = 1024

    def __init__(self):
        self.df: dict[str, int] = {}
        self.n = 0

    def _vec(self, text, learn=False):
        text = re.sub(r"search_(query|document): ", "", text)
        toks = retrieval.tokens(text) - retrieval.STOPWORDS
        if learn:
            self.n += 1
            for t in toks:
                self.df[t] = self.df.get(t, 0) + 1
        v = [0.0] * self.DIM
        for t in toks:
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % self.DIM] += math.log(2 + self.n / (1 + self.df.get(t, 0)))
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts):
        return [self._vec(t, learn=True) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture(scope="module")
def store():
    saved = {k: getattr(settings, k) for k in ("min_score", "semantic_floor")}
    # bag-of-words cosines are far lower than real embeddings': scale the semantic gates, not the logic
    object.__setattr__(settings, "min_score", 0.3)
    object.__setattr__(settings, "semantic_floor", 0.02)
    mp = pytest.MonkeyPatch()
    emb = BagOfWords()
    mp.setattr(vectorstore, "embeddings", lambda: emb)
    s = vectorstore.VectorStore(client=QdrantClient(":memory:"))
    files = [FIXTURES / "CLAUDEEE.md"] + [DOCS / n for n in
             ("refund_policy.md", "marine_life_aquarium_guide.txt", "leave_policy.md", "shipping_faq.md")]
    for f in files:
        if not f.exists():
            pytest.skip(f"missing sample document {f.name}")
        s.ingest_file(f)
    yield s
    mp.undo()
    for k, v in saved.items():
        object.__setattr__(settings, k, v)


def sections(hits):
    return [(h.filename, h.section) for h in hits]


def test_payload_carries_section_metadata(store):
    hits = store.search("which ORM is used?")
    top = hits[0]
    assert (top.filename, top.section, top.heading) == ("CLAUDEEE.md", "Database", "Database")
    assert top.heading_path == "Frontend Standards > Database" and top.chunk_index >= 0
    assert top.keyword == 1.0 and "ORM" in top.matched and 0 < top.score <= 1


def test_1_orm_question_retrieves_the_database_section_with_prisma(store):
    hits = store.search("which ORM is used?")
    assert sections(hits)[0] == ("CLAUDEEE.md", "Database") and "Prisma 7" in hits[0].text


def test_17_orm_question_does_not_rank_refund_policy_first(store):
    hits = store.search("which ORM is used?")
    assert hits and hits[0].filename != "refund_policy.md"
    assert "refund_policy.md" not in {h.filename for h in hits}


@pytest.mark.parametrize("q", ["which ORM is used in CLAUDEEE.md?", "which orm is used in claudeee.md", "CLAUDEEE.md: which ORM?"])
def test_2_filename_aware_retrieval(store, q):
    hits = store.search(q)
    assert {h.filename for h in hits} == {"CLAUDEEE.md"} and hits[0].section == "Database"


def test_named_document_is_searched_even_when_another_looks_more_similar(store):
    hits = store.search("what is the refund policy according to CLAUDEEE.md?")
    assert {h.filename for h in hits} == {"CLAUDEEE.md"}  # never mixes in refund_policy.md


def test_unknown_filename_falls_back_to_the_other_documents(store):
    assert store.search("what does missing_file.md say about the ORM?")[0].section == "Database"


def test_3_broad_rules_question_retrieves_several_sections_from_the_named_project_doc(store):
    hits = store.search("what are the rules used in the Entegris frontend?")
    secs = {h.section for h in hits}
    assert {h.filename for h in hits} == {"CLAUDEEE.md"}
    assert len(secs) >= 3 and "Rules" in secs and secs != {"Frontend Standards"}
    per_section = [s for _, s in sections(hits)]
    assert max(per_section.count(x) for x in secs) <= settings.max_chunks_per_section
    assert len(hits) <= settings.broad_top_k


def test_20_frontend_rules_reach_rule_sections_not_only_the_intro(store):
    hits = store.search("what are the frontend rules?")
    assert "Rules" in {h.section for h in hits} and {h.section for h in hits} != {"Frontend Standards"}


def test_4_framework_question_reaches_tech_stack(store):
    assert "Tech Stack" in {h.section for h in store.search("what framework is used?")}


def test_5_project_database_question_reaches_the_database_section(store):
    hits = store.search("what database is used by the project?")
    assert hits[0].filename == "CLAUDEEE.md" and hits[0].section == "Database"


def test_18_coral_bleaching_retrieves_the_marine_document(store):
    assert store.search("what causes coral bleaching?")[0].filename == "marine_life_aquarium_guide.txt"


def test_19_manta_rays_retrieves_the_marine_document(store):
    assert store.search("what do manta rays eat?")[0].filename == "marine_life_aquarium_guide.txt"


def test_policy_documents_still_work(store):
    assert store.search("what is the refund policy?")[0].filename == "refund_policy.md"
    assert store.search("how many vacation days do I get?")[0].filename == "leave_policy.md"


def test_unrelated_question_returns_no_context(store):
    assert store.search("what is the capital of France?") == []


def test_query_is_embedded_exactly_once(store, monkeypatch):
    calls = []
    real = vectorstore.embeddings()
    monkeypatch.setattr(vectorstore, "embeddings",
                        lambda: type("E", (), {"embed_query": lambda self, t: (calls.append(t), real.embed_query(t))[1]})())
    store.search("what are the rules used in CLAUDEEE.md?")
    assert len(calls) == 1


def test_candidate_pool_size_is_configurable(store, monkeypatch):
    seen = []
    orig = retrieval.rank_candidates
    monkeypatch.setattr(retrieval, "rank_candidates", lambda q, hits, m=(), **kw: (seen.append(len(hits)), orig(q, hits, m, **kw))[1])
    object.__setattr__(settings, "retrieval_candidate_k", 3)
    try:
        store.search("what is the refund policy?")
    finally:
        object.__setattr__(settings, "retrieval_candidate_k", 10)
    assert seen[0] <= 3 + 10  # semantic pool capped by config (+ heading matches)


def test_stale_documents_are_detected_for_reindexing(store):
    assert store.stale_doc_ids() == set()
    store._client.set_payload(store._collection, payload={"chunker_version": 1}, points=[
        p.id for p in store._client.scroll(store._collection, limit=1)[0]])
    stale = store.stale_doc_ids()
    assert len(stale) == 1
    for d in store.list_documents():  # restore
        store.ingest_file(next(p for p in [FIXTURES / d.filename, DOCS / d.filename] if p.exists()))
    assert store.stale_doc_ids() == set()


# ---------------------------------------------------------------- full graph on the real document ---------
@pytest.fixture
def pipeline(store):
    from app.condense import condense_question
    from app.graph import GraphDeps, _format_context, build_graph, initial_state

    sql_calls = []
    deps = GraphDeps(
        condense=lambda q, h: condense_question(q, h, lambda s, u: "unused"),
        route=lambda q: (_ for _ in ()).throw(RuntimeError("LLM router unavailable")),
        retrieve=store.search,
        run_sql=lambda q: sql_calls.append(q),
        answer=lambda q, hits, sql: _format_context(hits, sql),  # what the LLM would be given
    )
    graph = build_graph(deps)
    return lambda q, history=None: (graph.invoke(initial_state(q, history or [])), sql_calls)


@pytest.mark.parametrize("q", ["which ORM is used?", "which ORM is used in CLAUDEEE.md?"])
def test_pipeline_orm_question_routes_to_documents_and_context_contains_prisma(pipeline, q):
    out, sql_calls = pipeline(q)
    assert out["route"] == "document" and sql_calls == []
    assert "Prisma 7" in out["answer"] and "section: Frontend Standards > Database" in out["answer"]
    assert "refund" not in out["answer"].lower()


@pytest.mark.parametrize("q,section", [
    ("what framework is used?", "Tech Stack"),
    ("what database is used by the project?", "Database"),
])
def test_pipeline_project_questions_use_documentation_not_sql(pipeline, q, section):
    out, sql_calls = pipeline(q)
    assert out["route"] == "document" and sql_calls == [] and f"> {section}" in out["answer"]


def test_pipeline_rules_question_supplies_multiple_sections(pipeline):
    out, sql_calls = pipeline("what are the rules used in the Entegris frontend?")
    assert sql_calls == [] and "> Rules" in out["answer"] and out["answer"].count("[CLAUDEEE.md") >= 3


def test_pipeline_followup_after_unrelated_question_keeps_the_new_topic(pipeline):
    h = [{"role": "user", "content": "What do manta rays eat?"}, {"role": "assistant", "content": "plankton"}]
    out, _ = pipeline("What causes coral bleaching?", h)
    assert out["standalone"] == "What causes coral bleaching?" and out["history_used"] is False
    assert "marine_life_aquarium_guide.txt" in out["answer"]
