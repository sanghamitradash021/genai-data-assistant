"""Deterministic routing signals. The LLM router only decides questions these rules cannot.

Document concepts (leave, vacation, policies...) and database concepts (customers, revenue, refunded
amounts...) are matched by keyword. Exactly one kind of signal -> that route, no LLM involved, so a small
model can never send "How many vacation days are there?" to SQL.

Deliberately generic: no names of specific libraries/frameworks (React, Prisma, pnpm, ...) are hardcoded,
only the vocabulary of talking *about* a stack/codebase (framework, orm, architecture, documentation...),
so this doesn't silently favor whatever tech stack the demo's own sample documents happen to use.
"""
import re

from app.models.routing import RouteDecision
from app.utils.security import is_sensitive_request, is_write_request

# Things that live in the company documents, including technical documentation about a software project.
# Unambiguous: nothing here also names a kind of business data, so a match is never also a DB_SIGNAL match.
DOC_SIGNAL = re.compile(
    r"\b(leave|vacation|holidays?|sick|maternity|paternity|parental|caregivers?|remote work|remotely|"
    r"work from home|policy|policies|handbook|procedures?|guidelines?|eligib\w+|entitled|warranty|"
    r"shipping|vpn|passwords?|security|onboarding|faq|stipend|probation|according to|"
    # technical documentation: stack, conventions, project structure (categories, not specific tools)
    r"orm|frameworks?|frontend|backend|documentation|readme|codebase|repo|repository|"
    r"tech stack|architecture|coding conventions?|coding standards|naming convention\w*|"
    r"styling|linting|installation|configur\w+|authenticat\w+|authoriz\w+|deploy\w*|"
    r"pricing|subscriptions?|billing|invoic\w+|manual|instructions?|"
    r"(the|our|this) (project|app|application|stack|system)|"
    r"(what|which) (database|db) (is|are) used|used (by|in) the (project|app|application|stack))\b"
    r"|\.(md|markdown|txt|pdf|docx)\b",
    re.I,
)
# Weaker/ambiguous document vocabulary: these words are common outside documentation too ("pro plan",
# "business rules", "React hooks" vs. "fishing hooks"), so a bare match of one of these never forces
# "both" when a DB_SIGNAL is also present - the business-data reading wins instead (see rule_route()).
WEAK_DOC_SIGNAL = re.compile(r"\b(rules?|guides?|components?|hooks?|plans?)\b", re.I)
# Things that only exist as rows in PostgreSQL (business data). Deliberately excludes generic words like
# "how many", "total", "refund" (a refund *policy* is a document; refunded *amounts* are data) and technical
# words such as "database", "SQL", "schema", "PostgreSQL": those describe how a project is built, and only
# mean "query the data" when combined with a business noun below.
DB_SIGNAL = re.compile(
    r"\b(revenue|sales|customers|orders|products|top \d+|best[- ]?sell\w*|refunded|refunds? "
    r"(amount|total|count|rate)|total refunds|inventory|in stock|stock levels?|average order|"
    r"per (month|customer|product|country)|by (country|segment|category))\b"
    r"|\bhow much\b[^?]*\brefund\w*",
    re.I,
)
# Splits a combined question into its two halves on a connective ("...and...", "...; ...").
_CONNECTIVE = re.compile(r"\s*,?\s+\band\b\s+|\s*;\s*", re.I)


def _split_combined(question: str) -> tuple[str, str] | None:
    """Best-effort (doc_clause, db_clause) split of a combined question, used so "both" doesn't always
    send the identical unsplit text to RAG and SQL - only the LLM router did that split before, so a
    combined question got a real split only when the LLM call succeeded. None if the question doesn't
    split into exactly two clauses, one with each signal and none with both (the caller then falls back
    to the LLM split, and ultimately to the whole question on both sides if that fails too)."""
    parts = [p.strip() for p in _CONNECTIVE.split(question, maxsplit=1)]
    if len(parts) != 2:
        return None
    doc0, doc1 = bool(DOC_SIGNAL.search(parts[0])), bool(DOC_SIGNAL.search(parts[1]))
    db0, db1 = bool(DB_SIGNAL.search(parts[0])), bool(DB_SIGNAL.search(parts[1]))
    if doc0 and db1 and not (db0 or doc1):
        return parts[0], parts[1]
    if doc1 and db0 and not (db1 or doc0):
        return parts[1], parts[0]
    return None


def rule_route(question: str) -> RouteDecision | None:
    """Route by signals; None when the question has neither (LLM decides)."""
    if is_write_request(question):
        return RouteDecision(route="database", db_question=question)
    if is_sensitive_request(question):  # injection / secret extraction: no SQL, no LLM routing
        return RouteDecision(route="document", doc_question=question)
    strong_doc = bool(DOC_SIGNAL.search(question))
    db = bool(DB_SIGNAL.search(question))
    # A weak doc word ("plans", "rules"...) only counts as a document signal when there is no competing
    # business-data signal - "which plans have the most orders?" is a database question, not "both".
    doc = strong_doc or (not db and bool(WEAK_DOC_SIGNAL.search(question)))
    if doc and db:
        doc_q, db_q = _split_combined(question) or (question, question)
        return RouteDecision(route="both", doc_question=doc_q, db_question=db_q)
    if db:
        return RouteDecision(route="database", db_question=question)
    if doc:
        return RouteDecision(route="document", doc_question=question)
    return None
