"""Conservative follow-up condensation. Bias: PRESERVE the user's question.

Small local LLMs "helpfully" glue earlier context onto questions that are already complete
("What causes coral bleaching?" -> "...to manta rays"). So history is used only when the current question
cannot stand alone, decided by code, with no domain vocabulary:

  1. needs_rewrite()   - counts the question's own *specific* terms (words that are not question words,
                         function words or generic words like "time"/"limit"/"cost"). A question with its own
                         subject is preserved. Unresolved references (it/they/their/"the policy") with little
                         specific content, or no specific content at all, mean history is needed.
  2. LLM rewrite       - few-shot prompt, only the last turns of history.
  3. Validation        - keep every original term (never broader) AND add no new term that is not in the
                         recent conversation (never invent entities).
  4. fallback          - deterministic "<question> (in the context of: <previous question>)".
Security-sensitive input is never rewritten.
"""
import logging
import re
from typing import Callable

from .security import is_sensitive_request

log = logging.getLogger(__name__)

# Personal pronouns always point back; they are unresolved only if the question has little content of its own.
_PRONOUN = re.compile(r"\b(it|its|they|them|their|theirs|he|him|his|she|her)\b", re.I)
# Demonstratives / definite stand-ins ("the policy", "the limit"): unresolved only when nearly content-free.
_DEMONSTRATIVE = re.compile(
    r"\b(that|those|these|this|same|above|former|latter|such)\b"
    r"|\bthe (policy|policies|process|procedure|limit|amount|deadline|duration|rule|rules)\b",
    re.I,
)
# Elliptical openers: "and for contractors?", "what about X?"
_OPENER = re.compile(r"^\s*(and|also|but|then|so|what about|how about|why|how so)\b", re.I)
# "I mean ...", "sorry, ..." are conversational filler, not part of the question.
_CLARIFIER = re.compile(r"^\s*(i mean|i meant|no,|sorry,|actually,|ok,|okay,)\s+", re.I)

# Words that never identify a topic by themselves: question/function words...
_STOP = set(
    "a an the of to in on for and or is are was were be been being do does did what which who whom whose how "
    "when where why can could should would will may might must shall i we you me my our your us there here "
    "about with by at from as if any get give tell show list have has had also not no yes so than then "
    "company's".split()
)
# ...and generic words that need a subject to mean anything ("the time limit", "how much does it cost").
_VAGUE = set(
    "time limit deadline amount cost costs price number total long many much allowed allow allows take takes "
    "taking work works apply applies mean means start end need needed required available happen happens make "
    "makes use used option options first second third last next other more else difference example examples "
    "reason reasons part kind type day days week weeks month months year years hour hours explain describe "
    "please possible thing things one ones way ways".split()
)
_REF_WORDS = {"it", "its", "they", "them", "their", "theirs", "he", "him", "his", "she", "her", "that", "those",
              "these", "this", "same", "above", "former", "latter", "such"}
_TOKEN = re.compile(r"[a-z][a-z0-9'-]*")

_SYSTEM = """You rewrite a user's latest question so it can be understood without the conversation.
Rules:
1. Keep EVERY specific word of the latest question. Never make it broader or vaguer.
2. Only fill in what is missing (a pronoun or omitted subject) using the previous question.
3. If the latest question is about a different topic, return it unchanged.
Output ONLY the rewritten question, on one line.

Examples
Previous: What is the refund policy?
Latest: What is the time limit?
Rewritten: What is the time limit for the refund policy?

Previous: Who are the top 5 customers by revenue?
Latest: What about their total orders?
Rewritten: What are the total orders of the top 5 customers by revenue?

Previous: What do penguins eat?
Latest: What do they eat?
Rewritten: What do penguins eat?

Previous: What do manta rays eat?
Latest: What causes coral bleaching?
Rewritten: What causes coral bleaching?

Previous: What is the company's leave policy?
Latest: What is the security policy?
Rewritten: What is the security policy?"""


def _specific_terms(text: str) -> set[str]:
    """Terms that carry the question's own subject."""
    out = set()
    for w in _TOKEN.findall(text.lower()):
        if w in _STOP or w in _VAGUE or w in _REF_WORDS:
            continue
        for part in w.split("-"):  # "part-time" -> part, time (both vague) ; "sea-turtle" -> sea, turtle
            if len(part) > 1 and part not in _STOP and part not in _VAGUE:
                out.add(_stem(part))
    return out


def needs_rewrite(question: str) -> bool:
    """True only if the question likely cannot be understood without earlier turns (default: False)."""
    n = len(_specific_terms(question))
    if n == 0:
        return True  # nothing of its own to search for: "What is the time limit?", "How many are allowed?"
    if _PRONOUN.search(question) and n <= 2:
        return True  # "What do they eat?"
    if (_DEMONSTRATIVE.search(question) or _OPENER.search(question)) and n <= 1:
        return True  # "What causes this?", "And for contractors?"
    return False


def _stem(w: str) -> str:
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def _terms(text: str) -> set[str]:
    """All non-function words (vague ones included), stemmed. Used for the rewrite validation."""
    return {_stem(w) for w in _TOKEN.findall(text.lower()) if w not in _STOP and w not in _REF_WORDS and len(w) > 1}


def preserves_terms(original: str, rewritten: str) -> bool:
    """The rewrite must keep all terms of the original (i.e. never be broader)."""
    return _terms(original) <= _terms(rewritten)


def introduces_only_known_terms(original: str, rewritten: str, history: list[dict]) -> bool:
    """New words may only come from the recent conversation itself; anything else is an invented entity."""
    known = _terms(original)
    for m in history[-4:]:
        known |= _terms(m["content"])
    return _terms(rewritten) <= known


def _last_user_question(history: list[dict]) -> str | None:
    return next((m["content"] for m in reversed(history) if m["role"] == "user"), None)


def fallback_rewrite(question: str, history: list[dict]) -> str:
    prev = _last_user_question(history)
    if not prev:
        return question
    return f"{question.rstrip('?. ')} (in the context of: {prev.rstrip('?. ')})"


def uses_history(question: str, history: list[dict]) -> bool:
    """Whether history will be consulted for this question (for logging/tests)."""
    q = _clean(question)
    return bool(history) and not is_sensitive_request(q) and needs_rewrite(q)


def _clean(question: str) -> str:
    cleaned = _CLARIFIER.sub("", question, count=1).strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned != question.strip() else question


def condense_question(question: str, history: list[dict], llm: Callable[[str, str], str]) -> str:
    """llm(system, user) -> text. Returns a standalone question that is never less specific than the input."""
    question = _clean(question)
    # Security-sensitive input is never rewritten: the original text must reach the security checks as typed.
    if not uses_history(question, history):
        return question

    recent = history[-4:]  # last two turns: more history invites topic bleed
    convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in recent)
    prev = _last_user_question(history)
    user = f"Conversation so far:\n{convo}\n\nPrevious: {prev}\nLatest: {question}\nRewritten:"
    try:
        out = llm(_SYSTEM, user).strip().splitlines()[0].strip()
        if out.lower().startswith("rewritten:"):
            out = out[len("rewritten:"):].strip()
        out = out.strip('"').strip()
    except Exception:
        log.exception("Condense LLM failed; using fallback")
        return fallback_rewrite(question, history)

    if (not out or len(out) > 3 * len(question) + 120 or not preserves_terms(question, out)
            or not introduces_only_known_terms(question, out, history)):
        log.warning("Rejected condensed question %r for %r; using fallback", out, question)
        return fallback_rewrite(question, history)
    return out
