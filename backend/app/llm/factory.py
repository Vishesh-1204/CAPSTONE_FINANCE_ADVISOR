# app/llm/factory.py

import logging
from functools import lru_cache

from app.llm.base import LLMProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.openai_provider import OpenAIProvider
from app.core.config import settings

logger = logging.getLogger(__name__)

# Registry mapping the config string to its provider class.
# Adding a new provider = add one line here. Nothing else changes.
PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    """
    Builds and returns the configured LLM provider.

    @lru_cache means the provider is constructed only once and reused
    for the lifetime of the app — creating a new client on every
    request would be wasteful and slow.
    """
    provider_key = settings.LLM_PROVIDER.lower()

    provider_class = PROVIDER_REGISTRY.get(provider_key)
    if provider_class is None:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(
            f"Unknown LLM_PROVIDER '{provider_key}'. Available options: {available}"
        )

    provider = provider_class()
    logger.info(f"LLM provider active: {provider.provider_name}")
    return provider
