"""Deterministic routing signals. The LLM router only decides questions these rules cannot.

Document concepts (leave, vacation, policies...) and database concepts (customers, revenue, refunded
amounts...) are matched by keyword. Exactly one kind of signal -> that route, no LLM involved, so a small
model can never send "How many vacation days are there?" to SQL.
"""
import re

from .models import RouteDecision
from .security import is_sensitive_request, is_write_request

# Things that live in the company documents, including technical documentation about a software project.
DOC_SIGNAL = re.compile(
    r"\b(leave|vacation|holidays?|sick|maternity|paternity|parental|caregivers?|remote work|remotely|"
    r"work from home|policy|policies|handbook|procedures?|guidelines?|eligib\w+|entitled|warranty|"
    r"shipping|vpn|passwords?|security|onboarding|faq|stipend|probation|according to|"
    # technical documentation: stack, conventions, project structure
    r"orm|framework|frameworks|frontend|backend|typescript|javascript|react|prisma|zod|zustand|vitest|pnpm|"
    r"tailwind|nginx|docker|jwt|express|vite|eslint|documentation|readme|codebase|repo|repository|"
    r"tech stack|architecture|coding|conventions?|standards|rules|naming|components?|hooks?|routing|"
    r"styling|linting|installation|configur\w+|authenticat\w+|authoriz\w+|deploy\w*|"
    r"pricing|subscriptions?|billing|invoic\w+|plans?|manual|guide|instructions?|"
    r"(the|our|this) (project|app|application|stack|system)|"
    r"(what|which) (database|db) (is|are) used|used (by|in) the (project|app|application|stack))\b"
    r"|\.(md|markdown|txt|pdf|docx)\b",
    re.I,
)
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


def rule_route(question: str) -> RouteDecision | None:
    """Route by signals; None when the question has neither (LLM decides)."""
    if is_write_request(question):
        return RouteDecision(route="database", db_question=question)
    if is_sensitive_request(question):  # injection / secret extraction: no SQL, no LLM routing
        return RouteDecision(route="document", doc_question=question)
    doc, db = bool(DOC_SIGNAL.search(question)), bool(DB_SIGNAL.search(question))
    if doc and db:
        return RouteDecision(route="both", doc_question=question, db_question=question)
    if db:
        return RouteDecision(route="database", db_question=question)
    if doc:
        return RouteDecision(route="document", doc_question=question)
    return None
