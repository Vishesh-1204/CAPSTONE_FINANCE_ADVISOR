# app/llm/base.py

from abc import ABC, abstractmethod
from langchain_core.language_models.chat_models import BaseChatModel


class LLMProvider(ABC):
    """
    Abstract base class defining what every LLM provider must supply.

    Any new provider (Gemini, OpenAI, HuggingFace/Llama) implements this
    interface. The rest of the application only ever talks to LLMProvider,
    never to a concrete vendor SDK — this is the Dependency Inversion
    Principle, and it's what makes provider swapping a config change
    rather than a rewrite.
    """

    @abstractmethod
    def get_chat_model(self) -> BaseChatModel:
        """
        Returns a configured LangChain chat model instance.
        LangChain's BaseChatModel is the common type all providers
        conform to, which is why our agent code stays vendor-neutral.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name, used for logging and experiment tracking."""
        raise NotImplementedError