"""Internal domain object: structured output contract for the LLM router call."""
from typing import Literal

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    """Structured output of the router LLM call."""

    route: Literal["document", "database", "both"] = Field(
        description="document = policies/handbooks/text files; database = numbers, customers, "
        "orders, revenue, refunds amounts; both = needs a document AND database figures"
    )
    doc_question: str = Field(default="", description="Sub-question for the documents ('' if none)")
    db_question: str = Field(default="", description="Sub-question for the database ('' if none)")
