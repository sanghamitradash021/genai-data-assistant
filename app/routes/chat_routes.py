"""POST /chat."""
import logging
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.agents.chat_agent import initial_state
from app.config.settings import settings
from app.models.retrieval import Hit
from app.repository.conversation_store import ConversationStore
from app.routes.dependencies import get_graph
from app.schemas.chat import ChatRequest, ChatResponse, Source, SQLInfo
from app.utils.security import sanitize_message

log = logging.getLogger("app")

router = APIRouter()
_conversations = ConversationStore(max_turns=settings.history_turns)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, graph=Depends(get_graph)):
    message = sanitize_message(req.message)
    if not message:
        raise HTTPException(422, "Message is empty.")
    session_id = req.session_id or uuid4().hex
    try:
        state = graph.invoke(initial_state(message, _conversations.get(session_id)))
    except (httpx.HTTPError, ConnectionError) as e:
        log.exception("LLM backend unavailable")
        raise HTTPException(503, "Language model backend is unavailable. Is Ollama running with the models pulled?") from e
    except Exception as e:
        log.exception("Chat pipeline failed")
        raise HTTPException(500, "Internal error while answering.") from e

    _conversations.add_turn(session_id, message, state["answer"])

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
    debug = None
    if settings.rag_debug:  # ranking internals are for development only
        debug = {"chunk_index": h.chunk_index, "semantic": h.semantic, "keyword": h.keyword,
                 "final": h.score, "matched": h.matched, "heading_path": h.heading_path}
    return Source(document_id=h.doc_id, filename=h.filename, page=h.page, score=h.score, snippet=h.text[:240],
                  section=h.section, heading=h.heading, debug=debug)
