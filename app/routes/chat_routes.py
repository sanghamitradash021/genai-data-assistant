"""POST /chat, POST /chat/stream."""
import json
import logging
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agents.chat_agent import initial_state
from app.config.settings import settings
from app.models.retrieval import Hit
from app.repository.conversation_store import ConversationStore
from app.routes.dependencies import get_graph, require_api_key
from app.schemas.chat import ChatRequest, ChatResponse, Source, SQLInfo
from app.utils.rate_limit import limiter
from app.utils.security import sanitize_message

log = logging.getLogger("app")

router = APIRouter(dependencies=[Depends(require_api_key)])
_conversations = ConversationStore(max_turns=settings.history_turns)


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(lambda: settings.rate_limit_chat)
async def chat(req: ChatRequest, request: Request, graph=Depends(get_graph)):
    """Ask a question; routes to document search, SQL, or both, using session_id (if given) for follow-ups.

    Uses graph.ainvoke() (not .invoke()) because the real graph's "answer" node is async - see
    build_graph() in chat_agent.py. ainvoke() runs sync nodes (condense/route/rag/sql) exactly as
    invoke() would (LangGraph dispatches them to a thread automatically); nothing about routing or
    retrieval behavior changes, only how the final node is awaited.
    """
    message = sanitize_message(req.message)
    if not message:
        raise HTTPException(422, "Message is empty.")
    session_id = req.session_id or uuid4().hex
    try:
        state = await graph.ainvoke(initial_state(message, _conversations.get(session_id)))
    except (httpx.HTTPError, ConnectionError) as e:
        log.exception("LLM backend unavailable")
        raise HTTPException(503, "Language model backend is unavailable. Is Ollama running with the models pulled?") from e
    except Exception as e:
        log.exception("Chat pipeline failed")
        raise HTTPException(500, "Internal error while answering.") from e

    _conversations.add_turn(session_id, message, state["answer"])
    return _build_response(session_id, state)


@router.post("/chat/stream")
@limiter.limit(lambda: settings.rate_limit_chat)
async def chat_stream(req: ChatRequest, request: Request, graph=Depends(get_graph)):
    """Same compiled graph as POST /chat (no duplicated routing/retrieval logic), driven by
    astream_events() instead of invoke() so the answer node's tokens reach the client as they're
    generated. Emits newline-delimited SSE `data:` frames:
      {"type": "token", "text": "..."}               - zero or more, raw (pre-citation-scrub) tokens
      {"type": "done", ...same fields as ChatResponse} - exactly one, with the final scrubbed answer
      {"type": "error", "detail": "..."}              - instead of "done", on failure
    """
    message = sanitize_message(req.message)
    if not message:
        raise HTTPException(422, "Message is empty.")
    session_id = req.session_id or uuid4().hex
    history = _conversations.get(session_id)

    async def events():
        final_state: dict | None = None
        try:
            async for ev in graph.astream_events(initial_state(message, history), version="v2"):
                if ev["event"] == "on_chat_model_stream" and ev["metadata"].get("langgraph_node") == "answer":
                    token = ev["data"]["chunk"].content
                    token = token if isinstance(token, str) else str(token)
                    if token:
                        yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
                elif ev["event"] == "on_chain_end":
                    output = ev.get("data", {}).get("output")
                    # The *graph's* final output carries every state key; a single node's own
                    # on_chain_end (e.g. the "answer" node returning {"answer": ...}) does not.
                    if isinstance(output, dict) and "answer" in output and "route" in output:
                        final_state = output
        except (httpx.HTTPError, ConnectionError):
            log.exception("LLM backend unavailable during stream")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Language model backend is unavailable.'})}\n\n"
            return
        except Exception:
            log.exception("Chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Internal error while answering.'})}\n\n"
            return

        if final_state is None:
            log.error("Stream ended with no final graph state")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Internal error while answering.'})}\n\n"
            return

        _conversations.add_turn(session_id, message, final_state["answer"])
        done = {"type": "done", **_build_response(session_id, final_state).model_dump()}
        yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def _build_response(session_id: str, state: dict) -> ChatResponse:
    """Shared by /chat and /chat/stream so the two never assemble the response differently."""
    route = state["route"]
    sql = state.get("sql") if route in ("database", "both") else None
    sources: list[Source] = []
    if route in ("document", "both"):
        seen = set()
        for h in state.get("hits", []):
            key = (h.doc_id, h.page, h.heading_path)  # one source per page / Markdown section
            if key in seen:
                continue
            seen.add(key)
            sources.append(_source(h))
    return ChatResponse(
        session_id=session_id,
        answer=state["answer"],
        route=route,
        standalone_question=state["standalone"],
        sources=sources,
        sql=SQLInfo(query=sql.sql, columns=sql.columns, rows=sql.rows, row_count=len(sql.rows), error=sql.error)
        if sql
        else None,
    )


def _source(h: Hit) -> Source:
    """Hit -> API Source; the debug block is included only when RAG_DEBUG is enabled."""
    debug = None
    if settings.rag_debug:  # ranking internals are for development only
        debug = {"chunk_index": h.chunk_index, "semantic": h.semantic, "keyword": h.keyword,
                 "final": h.score, "matched": h.matched, "heading_path": h.heading_path}
    return Source(document_id=h.doc_id, filename=h.filename, page=h.page, score=h.score, snippet=h.text[:240],
                  section=h.section, heading=h.heading, debug=debug)
