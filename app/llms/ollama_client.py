"""Process-wide Ollama client singletons (chat model, embeddings) and the structured-output wrapper."""
from functools import lru_cache

from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.config.settings import settings


@lru_cache
def chat_model() -> ChatOllama:
    """One shared client for the whole process; @lru_cache means every call site reuses the same instance."""
    return ChatOllama(
        model=settings.chat_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        num_ctx=4096,
    )


@lru_cache
def embeddings() -> OllamaEmbeddings:
    """One shared embeddings client for the whole process, same singleton reasoning as chat_model()."""
    return OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)


def structured(schema):
    """Chat model constrained to emit JSON matching a Pydantic schema."""
    return chat_model().with_structured_output(schema)
