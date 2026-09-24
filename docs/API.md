# API reference

Interactive docs: `http://localhost:8000/docs` (OpenAPI JSON at `/openapi.json`).

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
Errors: `422` invalid input, `503` LLM backend down, `500` unexpected.

## POST /documents/ingest
- `multipart/form-data` with `file` (`.pdf .docx .txt .md`, ≤ 10 MB): saves to `data/documents/` and indexes it.
- No body: indexes every supported file in `data/documents/` not yet indexed. `?force=true` re-embeds all.

```bash
curl -F file=@handbook.pdf localhost:8000/documents/ingest
curl -X POST 'localhost:8000/documents/ingest?force=true'
```
Response: `{"ingested":[{id,filename,file_type,chunks,ingested_at}], "skipped":[…], "errors":{"file":"reason"}}`
Errors: `413` too large, `415` unsupported type, `422` no extractable text, `503` embedding/vector store down.

## GET /documents
`[{"id","filename","file_type","chunks","ingested_at"}]`

## DELETE /documents/{id}
Deletes the document's vectors and its file. `200 {"deleted","filename"}` or `404`.

## GET /health
`200 {"status":"ok","services":{"qdrant":"ok","ollama":"ok","postgres":"ok"}}`, or `503` with `"status":"degraded"` and the failing service.
