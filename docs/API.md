# API reference

Interactive docs: `http://localhost:8000/docs` (OpenAPI JSON at `/openapi.json`).

**Auth.** Every route below requires `X-API-Key: <value>` only if `API_KEY` is set in the environment; if it's unset (the default), there is no auth at all and every route - including `DELETE /documents/{id}` and `POST /chat/stream` - is open to anyone who can reach the port. This is acceptable only because `docker-compose.yml` binds every port to `127.0.0.1`; see the README's "Auth and rate limiting" section before exposing this beyond localhost.

**Single process, in-memory session state.** The API runs as one `uvicorn` worker with no `--workers`. Conversation history (used for `session_id` follow-ups) lives in that one process's memory - it's lost on restart and not shared if this is ever scaled to multiple workers/replicas. See the README's "Known limits".

## POST /chat
Request
```json
{ "message": "What is the refund policy and how much was refunded last month?", "session_id": "optional-existing-id" }
```
`message`: 1–2000 chars. Omit `session_id` to start a conversation; reuse the returned one for follow-ups.

Response `200`
```json
{
  "session_id": "3f1c…",
  "answer": "Refunds are allowed within 30 days of delivery [refund_policy.md]. Last month $21,279.96 was refunded.",
  "route": "both",
  "standalone_question": "What is the refund policy and how much was refunded last month?",
  "sources": [
    {"document_id": "…", "filename": "refund_policy.md", "page": null, "score": 0.71, "snippet": "Customers may request a refund within 30 days…"}
  ],
  "sql": {
    "query": "SELECT COALESCE(SUM(amount), 0) AS total_refunded FROM refunds WHERE … LIMIT 100",
    "columns": ["total_refunded"], "rows": [[21279.96]], "row_count": 1, "error": null
  }
}
```
`route` ∈ `document | database | both`. `sources` empty for `database`; `sql` null for `document`.
Errors: `422` invalid input, `503` LLM backend unreachable, `504` LLM backend reachable but timed out (`OLLAMA_TIMEOUT_S`, default 120s), `500` unexpected.

## POST /chat/stream
Same request body as `POST /chat`. Not part of the original assignment's endpoint list, but it is public API.

Response: `200`, `Content-Type: text/event-stream`. Each line is `data: <json>\n\n`. Frames, in order:
- `{"type": "token", "text": "..."}` - zero or more, as the model generates them. Citations (`[file.md]`) are already scrubbed per-token, same rule as the final answer: only a filename that's actually in this answer's `sources` may appear.
- exactly one of:
  - `{"type": "done", "session_id", "answer", "route", "standalone_question", "sources", "sql"}` - same fields as `POST /chat`'s response body, plus `"type"`.
  - `{"type": "error", "detail": "..."}` - on failure. The HTTP status is still `200` (the stream already started); the failure is in-band. `detail` distinguishes an unreachable backend from a timeout from an internal error, matching `POST /chat`'s 503/504/500 cases.

```bash
curl -N -X POST localhost:8000/chat/stream -H 'content-type: application/json' \
  -d '{"message":"What is the refund policy?"}'
```

## POST /documents/ingest
- `multipart/form-data` with `file` (`.pdf .docx .txt .md`, ≤ 10 MB): saves to `data/documents/` and indexes it.
- No body: indexes every supported file in `data/documents/` not yet indexed. `?force=true` re-embeds all.

```bash
curl -F file=@handbook.pdf localhost:8000/documents/ingest
curl -X POST 'localhost:8000/documents/ingest?force=true'
```
Response: `{"ingested":[{id,filename,file_type,chunks,ingested_at}], "skipped":[…], "errors":{"file":"reason"}}`
Errors: `413` too large, `415` unsupported type, `422` unreadable (no extractable text, corrupt/malformed PDF or DOCX, or a DOCX that would decompress far beyond its upload size), `503` embedding/vector store down. A `422` upload never touches a same-named existing file: the new content is validated in a temp location before it's atomically moved into place, so a corrupt re-upload can't overwrite a previously-good file.

## GET /documents
`[{"id","filename","file_type","chunks","ingested_at"}]`

## DELETE /documents/{id}
Deletes the document's vectors and its file. `200 {"deleted","filename"}` or `404`.

## GET /health
Diagnostic, not a liveness probe (see `/health/live` below): `200 {"status":"ok","services":{"qdrant":"ok","ollama":"ok","postgres":"ok"},"ingest":"done"}`, or `503` with `"status":"degraded"` and the failing service(s) marked `"unavailable: <ExceptionType>"`. `ingest` ∈ `disabled | pending | running | done | failed` - auto-ingest status, so a client can tell "not indexed yet" apart from "no matching document" right after startup.

## GET /health/live
`200 {"status":"ok"}`, unconditionally, once the process can serve HTTP - never touches Qdrant/Ollama/Postgres. This is the one container orchestrators (Docker/Kubernetes) should point a liveness or readiness check at; a dependency outage must not make them restart or stop routing to an otherwise-healthy process.
