# app/llm/gemini_provider.py

import logging
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models.chat_models import BaseChatModel

from app.llm.base import LLMProvider
from app.core.config import settings

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """
    Google Gemini provider, via LangChain's ChatGoogleGenerativeAI wrapper.
    Works with a free Google AI Studio key (no billing account required).
    """

    def __init__(self) -> None:
        # Fail fast at startup rather than mid-request if the key is missing
        if not settings.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. Add it to your .env file. "
                "Get a free key at https://aistudio.google.com/"
            )
        self._model_name = settings.GEMINI_MODEL
        logger.info(f"Initializing Gemini provider with model: {self._model_name}")

    def get_chat_model(self) -> BaseChatModel:
        """Builds the LangChain chat model configured for Gemini."""
        return ChatGoogleGenerativeAI(
            model=self._model_name,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
        )

    @property
    def provider_name(self) -> str:
        return f"gemini:{self._model_name}"