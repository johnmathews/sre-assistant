"""LLM factory — creates the correct chat model based on LLM_PROVIDER setting."""

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.config import Settings

# OAuth tokens from `claude setup-token` (Claude Max/Pro subscriptions) use
# Authorization: Bearer header, not x-api-key.  ChatAnthropic doesn't have an
# auth_token parameter, so we detect the token format and pass it via
# default_headers instead.
_OAUTH_TOKEN_PREFIX = "sk-ant-oat01-"


def _build_anthropic_kwargs(
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Build kwargs for ChatAnthropic, handling OAuth vs regular API keys."""
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if api_key.startswith(_OAUTH_TOKEN_PREFIX):
        # OAuth token — send as Authorization: Bearer, not x-api-key.
        # ChatAnthropic requires api_key, so pass a placeholder that won't be
        # used because the Bearer header takes precedence in the Anthropic API.
        kwargs["api_key"] = SecretStr("placeholder-for-oauth")
        kwargs["default_headers"] = {"Authorization": f"Bearer {api_key}"}
    else:
        kwargs["api_key"] = SecretStr(api_key)
    return kwargs


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
        kwargs = _build_anthropic_kwargs(
            api_key=settings.anthropic_api_key,
            model=model,
            temperature=temperature,
            max_tokens=4096,
        )
        return ChatAnthropic(**kwargs)  # pyright: ignore[reportCallIssue]

    model = model_override or settings.openai_model
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=SecretStr(settings.openai_api_key),
        base_url=settings.openai_base_url or None,
    )
