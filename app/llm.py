from functools import lru_cache

from langchain_ollama import ChatOllama, OllamaEmbeddings

from .config import settings


@lru_cache
def chat_model() -> ChatOllama:
    return ChatOllama(
        model=settings.chat_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        num_ctx=4096,
    )


@lru_cache
def embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model=settings.embed_model, base_url=settings.ollama_base_url)


def structured(schema):
    """Chat model constrained to emit JSON matching a Pydantic schema."""
    return chat_model().with_structured_output(schema)
