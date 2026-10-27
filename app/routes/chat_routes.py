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
from app.services.answer_service import CitationScrubber, as_text
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
        state = await graph.ainvoke(initial_state(message, _conversations.get(session_id), session_id))
    except httpx.TimeoutException as e:
        log.exception("LLM backend timed out")
        raise HTTPException(504, "Language model backend timed out.") from e
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
    astream(stream_mode=["messages", "values"]) so the answer node's tokens reach the client as they're
    generated. "values" also yields the full graph state after every node, so the final frame is read
    straight off its last value - not sniffed out of on_chain_end by guessing which output dict is the
    graph's own (brittle: it broke if a node's returned keys ever happened to look like the whole state).
    Citations are scrubbed per-token (CitationScrubber), not just once at the end, so a fabricated
    [file.md] is never shown to the client even for the instant before the "done" frame arrives.
    Emits newline-delimited SSE `data:` frames:
      {"type": "token", "text": "..."}               - zero or more, already citation-scrubbed tokens
      {"type": "done", ...same fields as ChatResponse} - exactly one, with the final answer
      {"type": "error", "detail": "..."}              - instead of "done", on failure
    """
    message = sanitize_message(req.message)
    if not message:
        raise HTTPException(422, "Message is empty.")
    session_id = req.session_id or uuid4().hex
    history = _conversations.get(session_id)

    async def events():
        state: dict = {}
        scrubber: CitationScrubber | None = None
        try:
            async for mode, chunk in graph.astream(
                initial_state(message, history, session_id), stream_mode=["messages", "values"]
            ):
                if mode == "values":
                    state = chunk
                    continue
                msg, metadata = chunk
                if metadata.get("langgraph_node") != "answer":
                    continue
                if scrubber is None:
                    # By the time the answer node's model starts streaming, "rag"/"sql" have already run
                    # and their outputs are in `state` (whichever "values" frame arrived most recently).
                    route = state.get("route")
                    hits = state.get("hits") if route in ("document", "both") else None
                    scrubber = CitationScrubber(hits)
                token = scrubber.feed(as_text(msg.content))
                if token:
                    yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
        except httpx.TimeoutException:
            log.exception("LLM backend timed out during stream")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Language model backend timed out.'})}\n\n"
            return
        except (httpx.HTTPError, ConnectionError):
            log.exception("LLM backend unavailable during stream")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Language model backend is unavailable.'})}\n\n"
            return
        except Exception:
            log.exception("Chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Internal error while answering.'})}\n\n"
            return

        if "answer" not in state:
            log.error("Stream ended with no final graph state")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Internal error while answering.'})}\n\n"
            return
        if scrubber is not None:
            tail = scrubber.flush()
            if tail:
                yield f"data: {json.dumps({'type': 'token', 'text': tail})}\n\n"

        _conversations.add_turn(session_id, message, state["answer"])
        done = {"type": "done", **_build_response(session_id, state).model_dump()}
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
