"""Qdrant-backed document index: ingest, semantic search, list, delete."""
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from qdrant_client import QdrantClient, models

from app.config.settings import settings
from app.llms.ollama_client import embeddings
from app.models.retrieval import Hit
from app.schemas.documents import DocumentInfo
from app.services import retrieval_service as retrieval
from app.services.document_service import CHUNKER_VERSION, chunk_sections, doc_id_for, load_sections

log = logging.getLogger(__name__)

# nomic-embed-text is trained with task prefixes; other models ignore/need none.
_NOMIC = "nomic" in settings.embed_model
_DOC_PREFIX = "search_document: " if _NOMIC else ""
_QUERY_PREFIX = "search_query: " if _NOMIC else ""
_BATCH = 32


class VectorStore:
    def __init__(self, client: QdrantClient | None = None):
        self._client = client or QdrantClient(url=settings.qdrant_url, timeout=30)
        self._collection = settings.qdrant_collection
        self._filenames_cache: set[str] | None = None
        self._indexes_ready = False

    # -- collection -------------------------------------------------------
    def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        specs = {
            "doc_id": models.PayloadSchemaType.KEYWORD,
            "filename": models.PayloadSchemaType.KEYWORD,
            "heading": models.TextIndexParams(
                type=models.TextIndexType.TEXT, tokenizer=models.TokenizerType.WORD, lowercase=True
            ),
        }
        for field, schema in specs.items():  # idempotent; also upgrades collections created by older versions
            try:
                self._client.create_payload_index(self._collection, field, schema)
            except Exception:
                # Best-effort: an index that fails to create only degrades filter/scroll performance,
                # it never breaks search correctness. Logged (with traceback) rather than silently dropped.
                log.warning("payload index %s not created", field, exc_info=True)
        self._indexes_ready = True

    def _ensure_collection(self, dim: int) -> None:
        """Create the collection sized to the embedding model's dim on first use; a no-op after that."""
        if self._client.collection_exists(self._collection):
            self._ensure_indexes()
            return
        self._client.create_collection(
            self._collection,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        self._ensure_indexes()

    def ping(self) -> None:
        """Raises if Qdrant is unreachable; used by the GET /health check."""
        self._client.get_collections()

    @staticmethod
    def _doc_filter(doc_id: str) -> models.Filter:
        return models.Filter(
            must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
        )

    # -- ingest -----------------------------------------------------------
    def ingest_file(self, path: Path) -> DocumentInfo:
        """Load, chunk, embed and upsert path's content, replacing any existing chunks for the same doc_id."""
        sections = load_sections(path)
        chunks = chunk_sections(sections, path.suffix.lower())
        if not chunks:
            raise ValueError("No extractable text (empty or scanned document).")

        vectors: list[list[float]] = []
        for i in range(0, len(chunks), _BATCH):
            batch = [_DOC_PREFIX + c.embedding_text(path.name) for c in chunks[i : i + _BATCH]]
            vectors.extend(embeddings().embed_documents(batch))

        self._ensure_collection(len(vectors[0]))
        doc_id = doc_id_for(path.name)
        self.delete_vectors(doc_id)  # replace, don't duplicate
        ingested_at = datetime.now(timezone.utc).isoformat()
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.UUID(doc_id), str(c.index))),
                vector=v,
                payload={
                    "doc_id": doc_id,
                    "filename": path.name,
                    "file_type": path.suffix.lower().lstrip("."),
                    "page": c.page,
                    "chunk_index": c.index,
                    "text": c.text,
                    "section": c.section,
                    "heading": c.heading,
                    "heading_path": c.heading_path,
                    "chunker_version": CHUNKER_VERSION,
                    "ingested_at": ingested_at,
                },
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        self._client.upsert(self._collection, points=points, wait=True)
        self._filenames_cache = None
        log.info("Ingested %s (%d chunks)", path.name, len(points))
        return DocumentInfo(
            id=doc_id,
            filename=path.name,
            file_type=path.suffix.lower().lstrip("."),
            chunks=len(points),
            ingested_at=ingested_at,
        )

    # -- search -----------------------------------------------------------
    def _filenames(self) -> set[str]:
        if self._filenames_cache is None:
            self._filenames_cache = {d.filename for d in self.list_documents()}
        return self._filenames_cache

    @staticmethod
    def _to_hit(p) -> Hit:
        """Qdrant scored point -> Hit, reading fields back out of the payload set at ingest time."""
        pl = p.payload
        if pl is None:  # every point this code ingests always carries a full payload; None means corrupt data
            raise ValueError(f"Point {p.id} has no payload")
        return Hit(
            doc_id=pl["doc_id"],
            filename=pl["filename"],
            page=pl.get("page"),
            chunk_index=pl["chunk_index"],
            score=round(p.score, 4),
            text=pl["text"],
            section=pl.get("section"),
            heading=pl.get("heading"),
            heading_path=pl.get("heading_path"),
            semantic=round(p.score, 4),
        )

    def search(self, query: str, k: int | None = None) -> list[Hit]:
        """One query embedding -> candidate chunks -> lexical/filename rerank -> small diverse context."""
        if not self._client.collection_exists(self._collection):
            return []
        implied = retrieval.expand_query(query)
        vector = embeddings().embed_query(_QUERY_PREFIX + (f"{query} ({implied})" if implied else query))  # once
        n = settings.retrieval_candidate_k
        points = {
            str(p.id): p
            for p in self._client.query_points(self._collection, query=vector, limit=n, with_payload=True).points
        }
        mentioned = retrieval.detect_filenames(query, self._filenames())
        if mentioned:  # the named document(s) always get a full candidate pool of their own
            flt = models.Filter(must=[models.FieldCondition(key="filename", match=models.MatchAny(any=mentioned))])
            for p in self._client.query_points(
                self._collection, query=vector, limit=n, query_filter=flt, with_payload=True
            ).points:
                points.setdefault(str(p.id), p)

        # Sections whose *heading* names a query term are candidates even if the embedding ranked others higher
        # (a big section such as a checklist can crowd a short "Rules" section out of the semantic top-N).
        terms = retrieval.heading_search_terms(query)
        if terms:
            flt = models.Filter(should=[
                models.FieldCondition(key="heading", match=models.MatchText(text=t)) for t in terms
            ])
            records, _ = self._client.scroll(self._collection, scroll_filter=flt, limit=n, with_payload=True,
                                             with_vectors=True)
            for r in records:
                if str(r.id) not in points and isinstance(r.vector, list) and r.vector:
                    # r.vector's type also covers named/multi-vector and sparse-vector collections; this
                    # collection is always created with a single unnamed VectorParams (_ensure_collection),
                    # so it is always a plain list[float] here. The isinstance check above rules out the
                    # dict (named-vector) case; cast() records the remaining list[float] vs list[list[float]]
                    # guarantee, which only holds because of that single-vector schema.
                    flat_vector = cast(list[float], r.vector)
                    # A real ScoredPoint, not a stand-in: scroll() doesn't return a score, so it's computed
                    # here from the already-fetched query vector; `version` is unused downstream (_to_hit
                    # only reads id/payload/score) but required by the model, so 0 is a safe placeholder.
                    points[str(r.id)] = models.ScoredPoint(
                        id=r.id, version=0, payload=r.payload, score=retrieval.cosine(vector, flat_vector))

        ranked = retrieval.rank_candidates(query, [self._to_hit(p) for p in points.values()], mentioned)
        broad = retrieval.is_broad(query)
        return retrieval.select_context(ranked, k or (settings.broad_top_k if broad else settings.top_k), broad=broad)

    # -- list / delete ----------------------------------------------------
    def list_documents(self) -> list[DocumentInfo]:
        """Page through the whole collection, aggregating per-chunk payloads into one entry per doc_id."""
        if not self._client.collection_exists(self._collection):
            return []
        docs: dict[str, DocumentInfo] = {}
        offset = None
        while True:
            points, offset = self._client.scroll(
                self._collection,
                limit=500,
                offset=offset,
                with_payload=["doc_id", "filename", "file_type", "ingested_at"],
                with_vectors=False,
            )
            for p in points:
                pl = p.payload
                if pl is None:  # same guarantee as _to_hit; skip rather than fail the whole listing
                    log.warning("Point %s has no payload; skipping", p.id)
                    continue
                info = docs.setdefault(
                    pl["doc_id"],
                    DocumentInfo(
                        id=pl["doc_id"],
                        filename=pl["filename"],
                        file_type=pl.get("file_type", ""),
                        chunks=0,
                        ingested_at=pl.get("ingested_at", ""),
                    ),
                )
                info.chunks += 1
            if offset is None:
                break
        return sorted(docs.values(), key=lambda d: d.filename)

    def get_document(self, doc_id: str) -> DocumentInfo | None:
        return next((d for d in self.list_documents() if d.id == doc_id), None)

    def stale_doc_ids(self) -> set[str]:
        """Documents indexed by an older chunker (no section metadata): they should be re-ingested."""
        if not self._client.collection_exists(self._collection):
            return set()
        stale: set[str] = set()
        offset = None
        while True:
            points, offset = self._client.scroll(
                self._collection, limit=500, offset=offset,
                with_payload=["doc_id", "chunker_version"], with_vectors=False,
            )
            stale |= {
                p.payload["doc_id"]
                for p in points
                if p.payload is not None and p.payload.get("chunker_version") != CHUNKER_VERSION
            }
            if offset is None:
                return stale

    def delete_vectors(self, doc_id: str) -> None:
        """Remove every chunk belonging to doc_id and invalidate the filename cache that search() reads."""
        self._filenames_cache = None
        if self._client.collection_exists(self._collection):
            self._client.delete(
                self._collection,
                points_selector=models.FilterSelector(filter=self._doc_filter(doc_id)),
                wait=True,
            )
