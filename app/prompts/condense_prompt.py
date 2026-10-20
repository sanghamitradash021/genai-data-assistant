"""System prompt for the follow-up condensation LLM call (only reached when `needs_rewrite()` says the
current question genuinely depends on history; see app/agents/condense.py)."""

CONDENSE_SYSTEM = """You rewrite a user's latest question so it can be understood without the conversation.
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
