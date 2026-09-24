# app/llm/openai_provider.py

import logging
from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel

from app.llm.base import LLMProvider
from app.core.config import settings

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """
    OpenAI provider, via LangChain's ChatOpenAI wrapper.
    """

    def __init__(self) -> None:
        if not settings.OPENAI_API_KEY:
            raise ValueError(
                "OPENAI_API_KEY is not set. Add it to your .env file."
            )
        self._model_name = settings.OPENAI_MODEL
        logger.info(f"Initializing OpenAI provider with model: {self._model_name}")

    def get_chat_model(self) -> BaseChatModel:
        """Builds the LangChain chat model configured for OpenAI."""
        return ChatOpenAI(
            model=self._model_name,
            api_key=settings.OPENAI_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
        )

    @property
    def provider_name(self) -> str:
        return f"openai:{self._model_name}"