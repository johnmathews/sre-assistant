"""LLM factory — creates the correct chat model based on LLM_PROVIDER setting."""

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.config import Settings

# OAuth tokens from `claude setup-token` (Claude Max/Pro subscriptions)
# require Bearer auth plus specific beta/UA headers that identify the
# request as coming from Claude Code.
_OAUTH_TOKEN_PREFIX = "sk-ant-oat"

# Headers required by the Anthropic API for OAuth token authentication.
_OAUTH_BETA = "claude-code-20250219,oauth-2025-04-20"
_OAUTH_USER_AGENT = "claude-cli/2.1.62"


def _is_oauth_token(api_key: str) -> bool:
    """Check if an API key is an Anthropic OAuth token."""
    return api_key.startswith(_OAUTH_TOKEN_PREFIX)


def create_anthropic_chat(
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> ChatAnthropic:
    """Create a ChatAnthropic instance, handling OAuth vs regular API keys.

    OAuth tokens (sk-ant-oat*) require:
      - ``Authorization: Bearer`` instead of ``x-api-key``
      - ``anthropic-beta`` header with ``claude-code-20250219,oauth-2025-04-20``
      - ``user-agent`` matching Claude Code (``claude-cli/{version}``)
      - ``x-app: cli``

    ChatAnthropic doesn't expose the SDK's ``auth_token`` param, so we set
    the required headers via ``default_headers`` and suppress the placeholder
    ``x-api-key`` header with the SDK's ``Omit`` sentinel.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    is_oauth = _is_oauth_token(api_key)
    if is_oauth:
        kwargs["api_key"] = SecretStr("placeholder-for-oauth")
        kwargs["default_headers"] = {
            "Authorization": f"Bearer {api_key}",
            "anthropic-beta": _OAUTH_BETA,
            "user-agent": _OAUTH_USER_AGENT,
            "x-app": "cli",
        }
    else:
        kwargs["api_key"] = SecretStr(api_key)

    llm = ChatAnthropic(**kwargs)  # pyright: ignore[reportCallIssue]

    if is_oauth:
        # Suppress the placeholder x-api-key header.  The SDK's
        # default_headers property merges auth_headers (X-Api-Key from
        # api_key) with _custom_headers (our default_headers).  Injecting
        # Omit() into _custom_headers makes _merge_mappings strip the
        # X-Api-Key entry so only the Authorization: Bearer header is sent.
        from anthropic._types import Omit  # pyright: ignore[reportPrivateUsage]

        omit: Any = Omit()
        for client in (llm._client, llm._async_client):
            client._custom_headers["X-Api-Key"] = omit  # type: ignore[index]  # pyright: ignore[reportPrivateUsage]

    return llm


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
        return create_anthropic_chat(
            api_key=settings.anthropic_api_key,
            model=model,
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
