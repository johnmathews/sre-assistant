"""LLM-as-judge — scores agent answers against a rubric."""

import json
import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.agent.llm import create_anthropic_chat
from src.eval.models import JudgeScore

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
You are an evaluation judge for an SRE assistant chatbot. Your job is to assess \
whether the assistant's answer meets the quality criteria in the rubric.

## Question asked
{question}

## Assistant's answer
{answer}

## Rubric
{rubric}

## Instructions
Evaluate the answer against the rubric. Consider:
- Does the answer address the key points in the rubric?
- Is the information accurate based on the data the assistant had access to?
- Is the answer actionable and clear?

Respond with ONLY a JSON object (no markdown fences):
{{"passed": true, "explanation": "Brief explanation of your assessment"}}

Set "passed" to true if the answer substantially meets the rubric criteria. \
Minor omissions are acceptable; only fail if important criteria are missed.
"""


async def judge_answer(
    question: str,
    answer: str,
    rubric: str,
    openai_api_key: str = "",
    model: str = "gpt-4o-mini",
    base_url: str | None = None,
    llm_provider: str = "openai",
    anthropic_api_key: str = "",
) -> JudgeScore:
    """Score an agent answer against a rubric using LLM-as-judge.

    Args:
        question: The original question asked.
        answer: The agent's answer text.
        rubric: Quality criteria the answer should meet.
        openai_api_key: OpenAI API key for the grading LLM.
        model: Model to use for grading (default: gpt-4o-mini).
        base_url: Optional OpenAI-compatible proxy URL.
        llm_provider: "openai" or "anthropic".
        anthropic_api_key: Anthropic API key (required when llm_provider=anthropic).

    Returns:
        JudgeScore with passed/failed and explanation.
    """
    llm: BaseChatModel
    if llm_provider == "anthropic":
        llm = create_anthropic_chat(
            api_key=anthropic_api_key,
            model=model,
            temperature=0.0,
            max_tokens=1024,
        )
    else:
        llm = ChatOpenAI(
            model=model,
            temperature=0.0,
            api_key=SecretStr(openai_api_key),
            base_url=base_url,
        )

    prompt = _JUDGE_PROMPT.format(question=question, answer=answer, rubric=rubric)
    response = await llm.ainvoke(prompt)
    raw_text = str(response.content)

    try:
        parsed: dict[str, object] = json.loads(raw_text)
        return JudgeScore(
            passed=bool(parsed.get("passed", False)),
            explanation=str(parsed.get("explanation", "No explanation provided")),
        )
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Failed to parse judge response: %s", exc)
        return JudgeScore(
            passed=False,
            explanation=f"Failed to parse judge response: {raw_text}",
        )
