"""Candidate reranking for document retrieval (pure functions: no Qdrant, no LLM).

Pipeline (orchestrated by VectorStore.search):
  1 query embedding -> N candidates (semantic score = cosine)
  2 rank_candidates(): add a lexical score, a filename signal, combine, and drop irrelevant chunks
  3 select_context(): cut to the best few, at most a couple per section, so broad questions see several sections

Semantic similarity alone is unreliable on technical text ("which ORM is used?" is about as close to a
refund policy as to a Database section), so exact terms (ORM, Prisma, pnpm, ...) are a second signal.
"""
import logging
import math
import re
from dataclasses import replace
from typing import Iterable

from .config import Settings, settings as default_settings
from .models import Hit

log = logging.getLogger(__name__)

_WORD = re.compile(r"[A-Za-z0-9]+(?:[.'_-][A-Za-z0-9]+)*")
_FILE = re.compile(r"[\w-]+\.(?:md|markdown|txt|pdf|docx)\b", re.I)

# Words that never make a chunk relevant by themselves.
STOPWORDS = frozenset(
    """a an the of to in on for and or is are was were be been being do does did what which who whom whose how when
    where why can could should would will may might must shall i we you me my our your us there here about with by
    at from as if any get give tell show list have has had also not no yes so than then this that these those it its
    they them their used use uses using project document documents doc docs file according say says said mention
    mentioned described explain please company""".split()
)
# Common technology vocabulary: extra weight even when typed in lowercase ("pnpm", "orm").
TECH_TERMS = frozenset(
    """orm prisma zod zustand vitest jest pnpm npm yarn react vite tailwind typescript javascript node express
    postgres postgresql sql jwt bcrypt docker nginx redis graphql rest api sdk cli css html json yaml eslint husky
    axios router redux next vue angular python fastapi django flask kubernetes linux git ci cd oauth ssl tls http
    https qdrant ollama langchain""".split()
)
_BROAD = re.compile(
    r"\b(rules|standards|conventions|guidelines|practices|overview|summary|summari[sz]e|everything|all|list|"
    r"requirements|patterns|principles|explain)\b",
    re.I,
)


# Vague concept words -> the vocabulary technical docs actually use in headings/tables. Used to (a) enrich the
# text that is embedded for the query and (b) add low-weight lexical terms. Generic query expansion only.
_EXPANSIONS = {
    **dict.fromkeys(["framework", "frameworks", "library", "libraries", "stack", "technology", "technologies",
                     "tool", "tools", "language", "languages"], "tech stack technology"),
    "orm": "database",
    "db": "database",
    **dict.fromkeys(["auth", "authentication", "login"], "authentication security"),
    **dict.fromkeys(["deploy", "deployment", "hosting"], "docker services deployment"),
}
_EXPANSION_WEIGHT = 0.5


def expand_query(query: str) -> str:
    """Extra words implied by vague concept terms ('' if none)."""
    extra: list[str] = []
    for w in _WORD.findall(strip_filenames(query).lower()):
        for word in _EXPANSIONS.get(w, "").split():
            if word not in extra:
                extra.append(word)
    return " ".join(extra)


def is_broad(query: str) -> bool:
    """Plural / summarising questions ("what are the rules ...") whose answer spans several sections."""
    return bool(_BROAD.search(query))


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokens(text: str) -> set[str]:
    """Lower-cased, stemmed word set; compound tokens ('adapter-pg', 'node.js') also yield their parts."""
    out: set[str] = set()
    for w in _WORD.findall(text.lower()):
        out.add(_stem(w))
        if re.search(r"[.'_-]", w):
            out.update(_stem(p) for p in re.split(r"[.'_-]+", w) if len(p) > 1)
    return out


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def heading_search_terms(query: str, limit: int = 5) -> list[str]:
    """Words worth looking for in section headings (informative, not tiny), plus singular forms."""
    words: list[str] = []
    for stem, raw in query_terms(query).items():
        for w in (raw.lower(), stem):
            if len(w) >= 3 and w not in words:
                words.append(w)
    return words[: limit * 2]


# ---------------------------------------------------------------------------------------------------------
# filename-aware retrieval
# ---------------------------------------------------------------------------------------------------------
def strip_filenames(query: str) -> str:
    return _FILE.sub(" ", query)


def detect_filenames(query: str, known: Iterable[str]) -> list[str]:
    """Indexed filenames the user mentions, matched case-insensitively as a full name ("CLAUDEEE.md")
    or as a distinctive stem ("refund_policy"). Works for any document, none are special-cased."""
    found = []
    for name in known:
        stem = name.rsplit(".", 1)[0]
        full = re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", query, re.I)
        by_stem = len(stem) >= 5 and re.search(rf"(?<![\w-]){re.escape(stem)}(?![\w-])", query, re.I)
        if full or by_stem:
            found.append(name)
    return found


# ---------------------------------------------------------------------------------------------------------
# lexical signal
# ---------------------------------------------------------------------------------------------------------
def query_terms(query: str) -> dict[str, str]:
    """{stem: original word} for the informative words of the query."""
    out: dict[str, str] = {}
    for raw in _WORD.findall(strip_filenames(query)):
        low = raw.lower()
        if low in STOPWORDS or len(low) < 2:
            continue
        out.setdefault(_stem(low), raw)
    return out


def _is_technical(raw: str) -> bool:
    return (raw.lower() in TECH_TERMS or (raw.isupper() and len(raw) >= 2) or any(c.isdigit() for c in raw)
            or bool(re.search(r"[a-z][A-Z]", raw)))


def _chunk_tokens(h: Hit) -> tuple[set[str], set[str]]:
    """(body+location tokens, closest-heading tokens).

    The document's root title (first heading) is left out of the location: it is in every chunk's
    breadcrumb, so matching it would make every chunk look relevant to any question naming the product.
    """
    parts = (h.heading_path or "").split(" > ")
    location = " > ".join(parts[1:]) if len(parts) > 1 else (h.heading_path or "")
    heading = tokens(h.heading or "")
    return tokens(f"{h.text} {location} {h.filename}") | heading, heading


def keyword_scores(query: str, hits: list[Hit]) -> list[tuple[float, list[str]]]:
    """Weighted share of the query's informative terms that a chunk contains, in [0, 1].

    Term weight = rarity among the candidates (a term only one chunk has is discriminative, one every chunk
    has is not) x 1.5 for technical terms. A term in the chunk's closest heading counts a bit extra.
    """
    terms = query_terms(query)
    implied = {k: v for k, v in query_terms(expand_query(query)).items() if k not in terms}
    if not terms or not hits:
        return [(0.0, [])] * len(hits)
    per_chunk = [_chunk_tokens(h) for h in hits]
    n = len(hits)
    weights = {}
    for stem, raw in {**terms, **implied}.items():
        df = sum(stem in body for body, _ in per_chunk)
        weights[stem] = (1 + math.log((n + 1) / (df + 1))) * (1.5 if _is_technical(raw) else 1.0)
        if stem in implied:
            weights[stem] *= _EXPANSION_WEIGHT
    # Implied terms only add credit (they can stand in for a missing literal term); they never raise the bar.
    total = sum(w for stem, w in weights.items() if stem not in implied)
    out = []
    for body, heading in per_chunk:
        got = sum(w * (1.25 if s in heading else 1.0) for s, w in weights.items() if s in body)
        out.append((min(1.0, got / total), [terms[s] for s in terms if s in body]))
    return out


# ---------------------------------------------------------------------------------------------------------
# rank + select
# ---------------------------------------------------------------------------------------------------------
def rank_candidates(query: str, hits: list[Hit], mentioned: Iterable[str] = (), cfg: Settings = default_settings) -> list[Hit]:
    """Score every candidate and keep only those with real evidence of relevance, best first.

    A chunk qualifies if ANY of: semantic >= RAG_MIN_SCORE; strong lexical match (>= KEYWORD_MIN_SCORE);
    it is from a document the user named. All still need semantic >= SEMANTIC_FLOOR. A high cosine alone
    never proves relevance for the weaker routes, and low-evidence chunks are never sent to the LLM.
    """
    wanted = {m.lower() for m in mentioned}
    if wanted and any(h.filename.lower() in wanted for h in hits):
        hits = [h for h in hits if h.filename.lower() in wanted]  # named document first; others only as fallback

    ranked = []
    for h, (kw, matched) in zip(hits, keyword_scores(query, hits)):
        file_match = h.filename.lower() in wanted
        final = cfg.semantic_weight * h.semantic + cfg.keyword_weight * kw + (cfg.filename_boost if file_match else 0.0)
        relevant = h.semantic >= cfg.semantic_floor and (
            h.semantic >= cfg.min_score or kw >= cfg.keyword_min_score or file_match
        )
        ranked.append((relevant, replace(h, keyword=round(kw, 4), matched=matched, score=round(min(final, 1.0), 4))))
    if cfg.rag_debug:
        _log_candidates(query, ranked)
    return sorted((h for ok, h in ranked if ok), key=lambda h: h.score, reverse=True)


def select_context(ranked: list[Hit], k: int, cfg: Settings = default_settings, broad: bool = False) -> list[Hit]:
    """Best `k` chunks: within cutoff of the top score, at most N per section (section diversity).
    Broad questions use a looser cutoff so that several sections can contribute."""
    if not ranked:
        return []
    floor = ranked[0].score * (cfg.broad_relative_cutoff if broad else cfg.relative_cutoff)
    per_section: dict[tuple, int] = {}
    picked = []
    for h in ranked:
        if h.score < floor or len(picked) >= k:
            continue
        key = (h.filename, h.section or h.chunk_index)
        if per_section.get(key, 0) >= cfg.max_chunks_per_section:
            continue
        per_section[key] = per_section.get(key, 0) + 1
        picked.append(h)
    return picked


def _log_candidates(query: str, ranked: list[tuple[bool, Hit]]) -> None:
    lines = [f"retrieval debug\n  Query: {query}"]
    for ok, h in sorted(ranked, key=lambda r: r[1].score, reverse=True):
        lines.append(
            f"  {'KEEP' if ok else 'DROP'} {h.filename} | Section: {h.section or '-'} | chunk {h.chunk_index}"
            f" | Semantic: {h.semantic:.2f} Keyword: {h.keyword:.2f} Final: {h.score:.2f}"
            f" | Matched: {', '.join(h.matched) or '-'}"
        )
    log.info("\n".join(lines))
