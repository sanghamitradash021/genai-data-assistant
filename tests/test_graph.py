import pytest

from app.graph import GraphDeps, build_graph, heuristic_route
from app.models import Hit, RouteDecision, SQLResult
from app.memory import ConversationStore


def make(route, calls, fail_router=False):
    def do_route(q):
        if fail_router:
            raise RuntimeError("llm down")
        return RouteDecision(route=route, doc_question="doc?", db_question="db?")

    deps = GraphDeps(
        condense=lambda q, h: (calls.append("condense"), f"standalone({q})")[1],
        route=do_route,
        retrieve=lambda q: (calls.append("rag"), [Hit("id", "f.md", None, 0, 0.9, "text")])[1],
        run_sql=lambda q: (calls.append("sql"), SQLResult(q, sql="SELECT 1", columns=["a"], rows=[[1]]))[1],
        answer=lambda q, hits, sql: f"hits={hits is not None} sql={sql is not None}",
    )
    return build_graph(deps)


@pytest.mark.parametrize(
    "route,expected,answer",
    [
        ("document", {"rag"}, "hits=True sql=False"),
        ("database", {"sql"}, "hits=False sql=True"),
        ("both", {"rag", "sql"}, "hits=True sql=True"),
    ],
)
def test_routing(route, expected, answer):
    calls = []
    out = make(route, calls).invoke({"question": "q", "history": []})
    assert set(calls) == expected and out["answer"] == answer
    assert "condense" not in calls  # no history -> no rewrite


def test_followup_is_condensed():
    calls = []
    out = make("document", calls).invoke({"question": "and sick leave?", "history": [{"role": "user", "content": "x"}]})
    assert "condense" in calls and out["standalone"] == "standalone(and sick leave?)"


def test_router_failure_falls_back_to_heuristic():
    calls = []
    out = make("document", calls, fail_router=True).invoke(
        {"question": "Which are the top 5 customers by revenue?", "history": []}
    )
    assert out["route"] == "database" and calls == ["sql"]


@pytest.mark.parametrize(
    "q,route",
    [
        ("What is the company's leave policy?", "document"),
        ("Which are the top 5 customers by revenue?", "database"),
        ("What is the refund policy and how much was refunded last month?", "both"),
    ],
)
def test_heuristic(q, route):
    assert heuristic_route(q).route == route


def test_conversation_store_is_bounded():
    s = ConversationStore(max_turns=2, max_sessions=2)
    for i in range(5):
        s.add_turn("a", f"q{i}", "a")
    assert len(s.get("a")) == 4
    s.add_turn("b", "q", "a"); s.add_turn("c", "q", "a")
    assert s.get("a") == []  # LRU evicted
