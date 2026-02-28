"""Core eval runner — patches settings, mocks HTTP, invokes agent, scores results."""

import logging
import sys
from typing import Any
from unittest.mock import patch

import httpx
import respx
from langchain_core.messages import AIMessage

from src.eval.judge import judge_answer
from src.eval.models import EvalCase, EvalResult, JudgeScore, ToolScore

logger = logging.getLogger(__name__)

# Every module that imports get_settings needs to be patched so the cached
# reference is overridden.  Same pattern as tests/conftest.py mock_settings.
_SETTINGS_PATCH_SITES = [
    "src.config.get_settings",
    "src.agent.tools.prometheus.get_settings",
    "src.agent.tools.grafana_alerts.get_settings",
    "src.agent.tools.proxmox.get_settings",
    "src.agent.tools.pbs.get_settings",
    "src.agent.tools.loki.get_settings",
    "src.agent.tools.truenas.get_settings",
    "src.agent.tools.disk_status.get_settings",
    "src.agent.agent.get_settings",
    "src.agent.retrieval.embeddings.get_settings",
    "src.api.main.get_settings",
]

# Service name → settings attribute that controls conditional tool registration
_SERVICE_URL_ATTRS: dict[str, str] = {
    "proxmox": "proxmox_url",
    "pbs": "pbs_url",
    "loki": "loki_url",
    "truenas": "truenas_url",
}


def _build_fake_settings(
    case: EvalCase,
    openai_api_key: str,
    openai_model: str,
    openai_base_url: str = "",
) -> object:
    """Build a FakeSettings object with real OpenAI creds and fake infra URLs.

    Only services listed in case.required_services get non-empty URLs;
    the rest get empty strings so their tools are not registered.
    """
    attrs: dict[str, Any] = {
        "openai_api_key": openai_api_key,
        "openai_model": openai_model,
        "openai_base_url": openai_base_url,
        "extra_docs_dirs": "",
        # Always-required services
        "prometheus_url": "http://prometheus.test:9090",
        "grafana_url": "http://grafana.test:3000",
        "grafana_service_account_token": "glsa_eval_fake",
        # Defaults for optional services (empty = disabled)
        "proxmox_url": "",
        "proxmox_api_token": "",
        "proxmox_verify_ssl": False,
        "proxmox_ca_cert": "",
        "proxmox_node": "proxmox",
        "pbs_url": "",
        "pbs_api_token": "",
        "pbs_verify_ssl": False,
        "pbs_ca_cert": "",
        "pbs_node": "localhost",
        "pbs_default_datastore": "backups",
        "loki_url": "",
        "truenas_url": "",
        "truenas_api_key": "",
        "truenas_verify_ssl": False,
        "truenas_ca_cert": "",
    }

    # Enable services that the case needs
    for svc in case.required_services:
        attr = _SERVICE_URL_ATTRS.get(svc)
        if attr:
            # Use the test URL from the mock definitions
            attrs[attr] = f"http://{svc}.test"
        # Also set auth tokens for services that need them
        if svc == "proxmox":
            attrs["proxmox_url"] = "https://proxmox.test:8006"
            attrs["proxmox_api_token"] = "eval@pam!eval=fake-token"
        elif svc == "pbs":
            attrs["pbs_url"] = "https://pbs.test:8007"
            attrs["pbs_api_token"] = "eval@pbs!eval=fake-token"
        elif svc == "truenas":
            attrs["truenas_url"] = "https://truenas.test"
            attrs["truenas_api_key"] = "1-fake-eval-truenas-key"
        elif svc == "loki":
            attrs["loki_url"] = "http://loki.test:3100"

    return type("FakeSettings", (), attrs)()


def _extract_tool_calls(messages: list[Any]) -> list[str]:
    """Extract tool names from AIMessage.tool_calls in the message history."""
    tool_names: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                name: str = tc["name"]
                tool_names.append(name)
    return tool_names


def _extract_answer(messages: list[Any]) -> str:
    """Extract the final text answer from the message history."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and isinstance(msg.content, str) and msg.content:
            return msg.content
    return "No response generated."


def _score_tools(case: EvalCase, called_tools: list[str]) -> ToolScore:
    """Score tool selection deterministically against expected_tools."""
    called_set = set(called_tools)
    must_call = set(case.expected_tools.must_call)
    must_not_call = set(case.expected_tools.must_not_call)

    missing = sorted(must_call - called_set)
    forbidden = sorted(must_not_call & called_set)

    return ToolScore(
        passed=len(missing) == 0 and len(forbidden) == 0,
        called_tools=called_tools,
        missing_tools=missing,
        forbidden_tools=forbidden,
    )


async def run_eval_case(
    case: EvalCase,
    openai_api_key: str,
    openai_model: str,
    openai_base_url: str = "",
) -> EvalResult:
    """Run a single eval case: mock HTTP, invoke agent, score results.

    Args:
        case: The eval case definition.
        openai_api_key: Real OpenAI API key (from .env).
        openai_model: OpenAI model name for the agent.
        openai_base_url: Optional OpenAI-compatible proxy URL.

    Returns:
        EvalResult with tool score, judge score, and agent answer.
    """
    fake_settings = _build_fake_settings(case, openai_api_key, openai_model, openai_base_url)

    # Stack all settings patches
    patches = [patch(site, return_value=fake_settings) for site in _SETTINGS_PATCH_SITES]

    # Prevent runbook_search from loading — it requires a vector store on disk.
    # RAG quality is tested separately; eval focuses on tool selection + answer quality.
    # Setting the module to None in sys.modules makes `from ... import` raise
    # ImportError, which _get_tools() catches gracefully (tool never registered).
    runbook_patch = patch.dict(sys.modules, {"src.agent.retrieval.runbooks": None})

    try:
        for p in patches:
            p.start()
        runbook_patch.start()

        # Import agent builder inside the patch context so it sees fake settings
        from src.agent.agent import build_agent

        # Set up respx mocks
        with respx.mock(assert_all_called=False) as router:
            for mock_def in case.mocks:
                method_fn = getattr(router, mock_def.method.lower())
                if isinstance(mock_def.body, str):
                    response = httpx.Response(mock_def.status, text=mock_def.body)
                else:
                    response = httpx.Response(
                        mock_def.status,
                        json=mock_def.body,  # pyright: ignore[reportArgumentType]
                    )
                method_fn(url=mock_def.url).mock(return_value=response)
            # Let real OpenAI traffic pass through (evals use a real LLM)
            router.route(host="api.openai.com").pass_through()
            if openai_base_url:
                from urllib.parse import urlparse

                proxy_host = urlparse(openai_base_url).hostname
                if proxy_host:
                    router.route(host=proxy_host).pass_through()
            # Catch-all: unmocked infra routes return 503
            router.route().mock(return_value=httpx.Response(503, json={"error": "unmocked"}))

            agent = build_agent()

            from langchain_core.messages import HumanMessage

            result: dict[str, Any] = await agent.ainvoke(
                {"messages": [HumanMessage(content=case.question)]},
                config={"configurable": {"thread_id": f"eval-{case.id}"}},
            )

        messages: list[Any] = result.get("messages", [])
        called_tools = _extract_tool_calls(messages)
        answer = _extract_answer(messages)
        tool_score = _score_tools(case, called_tools)

    finally:
        runbook_patch.stop()
        for p in reversed(patches):
            p.stop()

    # LLM-as-judge scoring
    judge_score: JudgeScore
    try:
        judge_score = await judge_answer(
            question=case.question,
            answer=answer,
            rubric=case.rubric,
            openai_api_key=openai_api_key,
            base_url=openai_base_url or None,
        )
    except Exception as exc:
        logger.warning("Judge scoring failed for %s: %s", case.id, exc)
        judge_score = JudgeScore(passed=False, explanation=f"Judge error: {exc}")

    return EvalResult(
        case_id=case.id,
        description=case.description,
        tool_score=tool_score,
        judge_score=judge_score,
        agent_answer=answer,
        passed=tool_score.passed and judge_score.passed,
    )
