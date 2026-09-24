import pytest

from app.graph import GraphDeps, build_graph
from app.models import Hit, RouteDecision, SQLResult
from app.routing import rule_route


@pytest.mark.parametrize(
    "q,route",
    [
        ("What is the leave policy?", "document"),
        ("What is the maternity leave policy?", "document"),
        ("How many vacation days do employees get?", "document"),
        ("How many vacation days are there?", "document"),
        ("Vacation days for the normal leave policy.", "document"),
        ("What are the sick leave rules and eligibility?", "document"),
        ("What is the remote work VPN guideline?", "document"),
        ("What is the refund policy?", "document"),
        ("What is the shipping policy?", "document"),
        ("Which are the top 5 customers by revenue?", "database"),
        ("What are the total orders of the top 5 customers by revenue?", "database"),
        ("How much was refunded last month?", "database"),
        ("What is the refund policy and how much was refunded last month?", "both"),
        ("What is the leave policy and how many customers do we have?", "both"),
    ],
)
def test_rule_route(q, route):
    assert rule_route(q).route == route


def test_neutral_question_has_no_rule():
    assert rule_route("What is the capital of France?") is None


def make(llm_route, calls):
    return build_graph(GraphDeps(
        condense=lambda q, h: q,
        route=lambda q: (calls.append("llm_route"), llm_route)[1],
        retrieve=lambda q: (calls.append("rag"), [Hit("i", "leave_policy.md", None, 0, 0.9, "t")])[1],
        run_sql=lambda q: (calls.append("sql"), SQLResult(q, sql="SELECT 1"))[1],
        answer=lambda q, h, s: "ok",
    ))


def test_document_question_never_runs_sql_even_if_llm_router_would_say_database():
    calls = []
    bad = RouteDecision(route="database", db_question="total vacation days")  # the reported misroute
    out = make(bad, calls).invoke({"question": "vacation days for normal leave policy", "history": []})
    assert out["route"] == "document"
    assert calls == ["rag"]  # no SQL generated or executed, LLM router not even consulted
    assert "sql" not in out or out["sql"] is None


def test_database_question_never_searches_documents():
    calls = []
    out = make(RouteDecision(route="both"), calls).invoke(
        {"question": "Which are the top 5 customers by revenue?", "history": []})
    assert out["route"] == "database" and calls == ["sql"]


def test_combined_question_runs_both_and_uses_llm_split():
    calls = []
    llm = RouteDecision(route="both", doc_question="refund policy", db_question="refunded last month")
    out = make(llm, calls).invoke(
        {"question": "What is the refund policy and how much was refunded last month?", "history": []})
    assert out["route"] == "both" and set(calls) == {"llm_route", "rag", "sql"}
    assert out["doc_question"] == "refund policy" and out["db_question"] == "refunded last month"


def test_combined_falls_back_when_llm_split_fails():
    calls = []
    deps = GraphDeps(condense=lambda q, h: q, route=lambda q: 1 / 0, retrieve=lambda q: calls.append("rag") or [],
                     run_sql=lambda q: calls.append("sql") or SQLResult(q), answer=lambda q, h, s: "ok")
    out = build_graph(deps).invoke({"question": "refund policy and how much was refunded?", "history": []})
    assert out["route"] == "both" and set(calls) == {"rag", "sql"}


def test_neutral_question_uses_llm_router():
    calls = []
    out = make(RouteDecision(route="document"), calls).invoke({"question": "Tell me something", "history": []})
    assert "llm_route" in calls and out["route"] == "document"


# --- technical-documentation questions must not reach SQL ---------------------------------------------------
@pytest.mark.parametrize("q,route", [
    ("How many customers are there?", "database"),
    ("Who are the top 5 customers by revenue?", "database"),
    ("How much was refunded last month?", "database"),
    ("What were the sales last month?", "database"),
    ("How many customers are in the database?", "database"),
    ("Show the SQL for the top 5 customers", "database"),
    ("What is the refund policy?", "document"),
    ("What is the refund policy and how much was refunded last month?", "both"),
    ("What ORM is used in CLAUDEEE.md?", "document"),
    ("which ORM is used in CLAUDEEE.md?", "document"),
    ("which ORM is used?", "document"),
    ("What frontend framework is used?", "document"),
    ("what framework is used?", "document"),
    ("What are the coding rules?", "document"),
    ("what are the rules used in the Entegris frontend?", "document"),
    ("what database is used by the project?", "document"),
    ("Which testing framework is used?", "document"),
    ("What does CLAUDEEE.md say?", "document"),
    ("How is authentication implemented according to the documentation?", "document"),
    ("Which database schema does the project documentation describe?", "document"),
    ("Which PostgreSQL version does the architecture use?", "document"),
])
def test_technical_words_alone_do_not_mean_database(q, route):
    assert rule_route(q).route == route


@pytest.mark.parametrize("q", ["which ORM is used in CLAUDEEE.md?", "what database is used by the project?",
                               "what are the coding rules?"])
def test_documentation_question_never_reaches_sql_even_if_the_llm_router_says_database(q):
    calls = []
    out = make(RouteDecision(route="database", db_question=q), calls).invoke({"question": q, "history": []})
    assert out["route"] == "document" and calls == ["rag"]


def test_llm_router_prompt_explains_technical_words_are_not_database():
    from app.graph import _ROUTER_SYSTEM
    assert "ORM" in _ROUTER_SYSTEM and "do NOT mean" in _ROUTER_SYSTEM and "DOCUMENTS" in _ROUTER_SYSTEM


@pytest.mark.parametrize("q", [
    "how much does the pro plan cost?",
    "what are the subscription tiers?",
    "is there an annual billing discount?",
    "what is the installation process?",
    "how do I configure the system?",
    "how does authentication work?",
    "how do I deploy the application?",
])
def test_generic_documentation_vocabulary_routes_to_documents_not_database(q):
    assert rule_route(q).route == "document"
