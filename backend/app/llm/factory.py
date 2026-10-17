# app/llm/factory.py
#
# Purpose: builds LLM providers from configuration. The rest of the app never
# imports a vendor SDK; it asks this factory for a provider (or a chain of
# providers: primary first, then LLM_FALLBACK_PROVIDER).

import logging
from functools import lru_cache

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# Registry mapping the config string to its provider class.
# Adding a new provider = add one line here. Nothing else changes.
PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


def build_provider(name: str) -> LLMProvider:
    """Constructs one provider by its config name. Raises ValueError if unknown or its key is missing."""
    provider_key = name.strip().lower()
    provider_class = PROVIDER_REGISTRY.get(provider_key)
    if provider_class is None:
        available = ", ".join(PROVIDER_REGISTRY.keys())
        raise ValueError(f"Unknown LLM provider '{provider_key}'. Available options: {available}")
    return provider_class()


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    """
    Builds and returns the PRIMARY provider (LLM_PROVIDER).

    @lru_cache means the provider is constructed only once and reused
    for the lifetime of the app.
    """
    provider = build_provider(settings.LLM_PROVIDER)
    logger.info(f"LLM provider active: {provider.provider_name}")
    return provider


def get_llm_provider_chain() -> list[LLMProvider]:
    """
    Primary provider, then the fallback (if configured and different).
    A provider that cannot be built (e.g. missing key) is skipped with a warning;
    ValueError is raised only when NONE can be built.
    """
    names = [settings.LLM_PROVIDER]
    fallback = (settings.LLM_FALLBACK_PROVIDER or "").strip().lower()
    if fallback and fallback != settings.LLM_PROVIDER.strip().lower():
        names.append(fallback)

    providers: list[LLMProvider] = []
    errors: list[str] = []
    for name in names:
        try:
            providers.append(build_provider(name))
        except ValueError as exc:
            logger.warning(f"LLM provider '{name}' unavailable: {exc}")
            errors.append(str(exc))

    if not providers:
        raise ValueError("; ".join(errors))
    logger.info(f"LLM provider chain: {[p.provider_name for p in providers]}")
    return providers
