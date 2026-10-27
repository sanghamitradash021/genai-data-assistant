"""LangGraph pipeline:  condense -> route -> (rag | sql | rag+sql in parallel) -> answer.

This is the chat "agent": the orchestrator that wires together the condensation, routing, RAG and SQL
steps into one LangGraph StateGraph. Citation scrubbing and context formatting for the answer prompt live
in app/services/answer_service.py, not here.
"""
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents.condense import condense_question, uses_history
from app.models.retrieval import Hit
from app.models.routing import RouteDecision
from app.models.sql import SQLResult
from app.services.routing_service import rule_route
from app.utils.security import is_sensitive_request, is_write_request, looks_like_injection

log = logging.getLogger(__name__)


def heuristic_route(question: str) -> RouteDecision:
    """Keyword routing; used before the LLM and as its fallback."""
    return rule_route(question) or RouteDecision(route="document", doc_question=question)


class ChatState(TypedDict):
    """Every field is set by initial_state(); each node only ever reads fields earlier nodes already wrote."""

    question: str
    session_id: str | None
    history: list[dict]
    standalone: str
    history_used: bool
    route: str
    doc_question: str
    db_question: str
    hits: list[Hit]
    sql: SQLResult | None
    answer: str


@dataclass
class GraphDeps:
    """Injected capabilities, so the graph wiring can be tested without any model or service.

    `answer` is always async: the real implementation streams tokens (graph.astream_events() is how
    POST /chat/stream gets them to the client), and the app only ever drives this graph with
    graph.ainvoke()/.astream_events(), never the sync .invoke(). Test fakes are async too (see tests/).
    """

    condense: Callable[[str, list[dict]], str]
    route: Callable[[str], RouteDecision]
    retrieve: Callable[[str], list[Hit]]
    run_sql: Callable[[str, str | None], SQLResult]
    answer: Callable[[str, list[Hit] | None, SQLResult | None, RunnableConfig | None], Awaitable[str]]


def initial_state(question: str, history: list[dict], session_id: str | None = None) -> ChatState:
    """Every request starts from a clean slate; nothing carries over from earlier invocations.

    `session_id` is only ever used to tag the query_log audit row (app/agents/sql_agent.py) with which
    chat session it came from; nothing in the graph's own routing/retrieval/answer logic reads it.
    """
    return {
        "question": question,
        "session_id": session_id,
        "history": history,
        "standalone": "",
        "history_used": False,
        "route": "",
        "doc_question": "",
        "db_question": "",
        "hits": [],
        "sql": None,
        "answer": "",
    }


def build_graph(d: GraphDeps):
    """Wire condense/route/rag/sql/answer into the compiled StateGraph described at the top of this module."""

    def condense(state: ChatState) -> dict:
        history = state.get("history") or []
        used = uses_history(state["question"], history)
        return {
            "standalone": d.condense(state["question"], history) if history else state["question"],
            "history_used": used,
        }

    def route(state: ChatState) -> dict:
        q = state["standalone"]
        decision = rule_route(q)  # unambiguous signals never reach the LLM
        try:
            if decision is None:
                decision = d.route(q)
            elif decision.route == "both":
                # rules already know it's "both" and have their own best-effort split (rule_route's
                # _split_combined); the LLM only refines that split, and falls back to it - not to the
                # whole unsplit question - if the LLM call comes back empty.
                split = d.route(q)
                decision = RouteDecision(
                    route="both",
                    doc_question=split.doc_question.strip() or decision.doc_question,
                    db_question=split.db_question.strip() or decision.db_question,
                )
        except Exception:
            log.exception("LLM router failed; using keyword rules")
            decision = decision or heuristic_route(q)
        if decision.route != "both":
            # A single-intent question needs no split: trusting the model's rewritten sub-question here would
            # let it silently retrieve on a different question than the one asked (small models can paraphrase
            # or hallucinate an unrelated "example" question instead of echoing this one back).
            return {"route": decision.route, "doc_question": q, "db_question": q}
        return {
            "route": "both",
            "doc_question": decision.doc_question.strip() or q,
            "db_question": decision.db_question.strip() or q,
        }

    def rag(state: ChatState) -> dict:
        if is_sensitive_request(state["doc_question"]):
            return {"hits": []}  # nothing to look up for injection / secret-extraction attempts
        try:
            return {"hits": d.retrieve(state["doc_question"])}
        except Exception:
            log.exception("Document retrieval failed")
            return {"hits": []}

    def sql(state: ChatState) -> dict:
        try:
            return {"sql": d.run_sql(state["db_question"], state.get("session_id"))}
        except Exception:
            log.exception("SQL agent failed")
            return {"sql": SQLResult(state["db_question"], error="The database is currently unavailable.")}

    def _prep_answer(state: ChatState) -> tuple[list[Hit] | None, SQLResult | None]:
        route = state["route"]
        hits = state.get("hits", []) if route in ("document", "both") else None
        sql_res = state.get("sql") if route in ("database", "both") else None
        log.info(
            "chat trace\n  Original question: %r\n  History available: %s\n  Was condensation needed: %s\n"
            "  Standalone question: %r\n  Route: %s\n  Document hits: %s\n  Generated SQL: %s\n  SQL validation error: %s",
            state["question"], str(bool(state.get("history"))).lower(), str(state.get("history_used", False)).lower(),
            state["standalone"], route,
            [(h.filename, h.score) for h in hits] if hits is not None else "n/a (not queried)",
            (sql_res.sql or "none") if sql_res else "n/a (not run)",
            (sql_res.error or "none") if sql_res else "n/a",
        )
        return hits, sql_res

    async def answer(state: ChatState, config: RunnableConfig) -> dict:
        hits, sql_res = _prep_answer(state)
        return {"answer": await d.answer(state["standalone"], hits, sql_res, config)}

    def pick(state: ChatState) -> list[str]:
        return {"document": ["rag"], "database": ["sql"], "both": ["rag", "sql"]}[state["route"]]

    g = StateGraph(ChatState)
    for name, fn in [("condense", condense), ("route", route), ("rag", rag), ("sql", sql), ("answer", answer)]:
        g.add_node(name, fn)
    g.add_edge(START, "condense")
    g.add_edge("condense", "route")
    g.add_conditional_edges("route", pick, ["rag", "sql"])  # "both" fans out in parallel
    g.add_edge("rag", "answer")
    g.add_edge("sql", "answer")
    g.add_edge("answer", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Default LLM-backed implementations
# ---------------------------------------------------------------------------
def default_deps(store) -> GraphDeps:
    """The real GraphDeps: Ollama-backed condense/route/answer plus the given store's retrieval and SQL agent."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agents.sql_agent import run_sql_agent
    from app.llms.ollama_client import chat_model, structured
    from app.prompts.answer_prompt import ANSWER_SYSTEM
    from app.prompts.router_prompt import ROUTER_SYSTEM
    from app.services.answer_service import as_text, format_context, scrub_citations

    def condense(question: str, history: list[dict]) -> str:
        def llm(system: str, user: str) -> str:
            return as_text(chat_model().invoke([SystemMessage(system), HumanMessage(user)]).content)

        return condense_question(question, history, llm)

    def route(question: str) -> RouteDecision:
        result = structured(RouteDecision).invoke([SystemMessage(ROUTER_SYSTEM), HumanMessage(question)])
        # with_structured_output may hand back either the Pydantic model or a plain dict depending on
        # the backend; this project always configures it for RouteDecision, so coerce defensively.
        return result if isinstance(result, RouteDecision) else RouteDecision.model_validate(result)

    async def answer(question: str, hits, sql, config: RunnableConfig | None = None) -> str:
        """Streams the model's reply (astream, not invoke) so a graph.astream_events() caller sees each
        token as it's generated; graph.ainvoke() just awaits this the same way and gets the final string."""
        if is_sensitive_request(question) and not is_write_request(question):
            log.warning("Refused sensitive request (injection / secret extraction)")
            return "I can't help with that request."
        if looks_like_injection(question):
            log.warning("Possible prompt injection in user message")
        if hits == [] and sql is None:  # nothing retrieved: don't let the model answer from memory
            return "I couldn't find anything relevant in the company documents to answer that."
        human = f"{format_context(hits, sql)}\n\nQuestion: {question}"
        parts: list[str] = []
        async for chunk in chat_model().astream([SystemMessage(ANSWER_SYSTEM), HumanMessage(human)], config=config):
            parts.append(as_text(chunk.content))
        reply = "".join(parts).strip()
        return scrub_citations(reply, hits)

    return GraphDeps(condense, route, store.search, run_sql_agent, answer)
