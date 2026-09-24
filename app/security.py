import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+"
    r"(instructions|rules|prompts?)|reveal\s+(your\s+)?system\s+prompt|you\s+are\s+now\s+",
    re.I,
)
_CONTEXT_TAGS = re.compile(r"</?\s*(documents?|sql_result|context)\s*>", re.I)


def sanitize_message(text: str) -> str:
    return _CONTROL.sub("", text).strip()


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def neutralize_context(text: str) -> str:
    """Untrusted document text must not be able to close our prompt delimiters."""
    return _CONTEXT_TAGS.sub("", text)


def safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", name.replace("\\", "/").split("/")[-1])
    return base.lstrip(".") or "upload"


# --- intent checks on the *user's* text (independent of anything the LLM generates) -----------------
_WRITE_SQL = re.compile(
    r"\b(drop|truncate|alter)\s+(table|database|schema|role|user|index|view)\b"
    r"|\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b"
    r"|\bcreate\s+(table|database|role|user|schema|index|view)\b|\b(grant|revoke)\b.+\b(on|to|from)\b",
    re.I,
)
_WRITE_NL = re.compile(
    r"\b(delete|remove|erase|wipe|destroy|drop|truncate|purge)\s+(all\s+|every\s+|the\s+|my\s+)?(\w+\s+)?"
    r"(orders|customers|products|refunds|rows|records|data|tables?|database)\b",
    re.I,
)
_SECRET = re.compile(
    r"\b(reveal|show|print|display|tell|give|leak|dump|expose|share|what(?:'s| is))\b[^.?!]*"
    r"\b(system prompt|hidden instructions?|initial instructions?|instructions|passwords?|secrets?|api keys?|"
    r"credentials|tokens?|connection string)\b",
    re.I,
)


def is_write_request(text: str) -> bool:
    """User is asking to modify data/schema (SQL or natural language). Never sent to the SQL generator."""
    return bool(_WRITE_SQL.search(text) or _WRITE_NL.search(text))


def is_sensitive_request(text: str) -> bool:
    """Injection attempts, secret/prompt extraction, destructive requests. Must never be rewritten."""
    return is_write_request(text) or looks_like_injection(text) or bool(_SECRET.search(text))
