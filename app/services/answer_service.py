"""Turning retrieved hits / SQL results into the LLM's input context, and cleaning up its output.

Kept separate from app/agents/chat_agent.py (graph wiring) so each module has one job.
"""
import json
import re

from app.models.retrieval import Hit
from app.models.sql import SQLResult
from app.utils.security import neutralize_context

_CITATION = re.compile(r"\s*\[([^\[\]]+?\.(?:md|markdown|txt|pdf|docx))(?:[^\[\]]*)\]", re.I)
_TRAILING_WS = re.compile(r"\s*\Z")
_MAX_ROWS_SHOWN = 50  # keeps the prompt within the context window even at SQL_MAX_ROWS=100 fetched rows


def scrub_citations(answer: str, hits: list[Hit] | None) -> str:
    """Drop [file] citations the model made up: only files actually retrieved may be cited."""
    allowed = {h.filename.lower() for h in hits or []}
    return _CITATION.sub(lambda m: m.group(0) if m.group(1).strip().lower() in allowed else "", answer)


def format_context(hits: list[Hit] | None, sql: SQLResult | None) -> str:
    """Render retrieved hits / SQL results into the <documents>/<sql_result> blocks the answer prompt expects."""
    parts = []
    if hits is not None:
        body = "\n\n".join(
            f"[{h.filename}{f' - section: {h.heading_path or h.section}' if (h.heading_path or h.section) else ''}"
            f"{f', page {h.page}' if h.page else ''}]\n{neutralize_context(h.text)}" for h in hits
        ) or "No relevant passages found."
        parts.append(f"<documents>\n{body}\n</documents>")
    if sql is not None:
        if sql.error:
            body = f"Query failed: {sql.error}"
        else:
            shown = sql.rows[:_MAX_ROWS_SHOWN]
            # The label must say so explicitly when rows are cut - row_count in the API response is the
            # *full* count (SQLResult.rows, up to SQL_MAX_ROWS), so a model that only read "rows (N):" and
            # assumed N rows were shown could otherwise claim a truncated result is complete.
            count = f"first {len(shown)} of {len(sql.rows)}" if len(sql.rows) > len(shown) else str(len(shown))
            body = f"SQL: {sql.sql}\ncolumns: {sql.columns}\nrows ({count}): {json.dumps(shown, default=str)}"
        parts.append(f"<sql_result>\n{body}\n</sql_result>")
    return "\n\n".join(parts)


def as_text(content: str | list[str | dict]) -> str:
    """AIMessage.content is typed as str | list[...] (multimodal models can return content blocks);
    this project only ever talks to text-only Ollama models, so it is always str in practice."""
    return content if isinstance(content, str) else str(content)


class CitationScrubber:
    """Incremental version of scrub_citations() for a live token stream.

    The one-shot scrub_citations() above needs the whole reply to find a `[file.md]` span; a token stream
    hands tokens over one at a time, so this holds back an unterminated `[` until its closing `]` arrives
    (or the stream ends) instead of ever showing the client a fabricated citation that a later token would
    have proven invalid.
    """

    def __init__(self, hits: list[Hit] | None):
        self._hits = hits
        self._buf = ""

    def feed(self, token: str) -> str:
        """Text (possibly empty) now safe to send to the client."""
        self._buf += token
        out = []
        while True:
            start = self._buf.find("[")
            if start == -1:
                out.append(self._buf)
                self._buf = ""
                break
            # _CITATION's own \s* eats any whitespace right before "[", so if the citation turns out to
            # be fabricated that whitespace disappears too, and if it's kept it's reproduced unchanged
            # (m.group(0)) - either way it must travel with the bracket, not be emitted ahead of it.
            ws_start = _TRAILING_WS.search(self._buf, 0, start).start()
            out.append(self._buf[:ws_start])
            rest = self._buf[ws_start:]
            end = rest.find("]")
            if end == -1:
                self._buf = rest  # incomplete citation candidate; wait for more tokens
                break
            out.append(scrub_citations(rest[: end + 1], self._hits))
            self._buf = rest[end + 1 :]
        return "".join(out)

    def flush(self) -> str:
        """Call once the stream ends. Whatever remains has no closing `]`, so scrub_citations() could
        never have matched it anyway - it's returned as is."""
        out, self._buf = self._buf, ""
        return out
