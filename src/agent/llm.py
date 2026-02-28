"""LLM factory — creates the correct chat model based on LLM_PROVIDER setting."""

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.config import Settings


def create_llm(
    settings: Settings,
    temperature: float = 0.0,
    model_override: str | None = None,
) -> BaseChatModel:
    """Create a chat model instance based on the configured provider.

    Args:
        settings: Application settings (provider, keys, model names).
        temperature: LLM temperature (0.0 for deterministic tool-calling).
        model_override: Override model name from settings (e.g. build_agent's model_name param).

    Returns:
        A ChatAnthropic or ChatOpenAI instance.
    """
    if settings.llm_provider == "anthropic":
        model = model_override or settings.anthropic_model
        return ChatAnthropic(  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
            model=model,
            api_key=SecretStr(settings.anthropic_api_key),
            temperature=temperature,
            max_tokens=4096,
        )

    model = model_override or settings.openai_model
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=SecretStr(settings.openai_api_key),
        base_url=settings.openai_base_url or None,
    )
