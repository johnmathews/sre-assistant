"""Entry point for running the evaluation framework.

Usage:
    uv run python -m scripts.run_eval                    # Run all cases
    uv run python -m scripts.run_eval --case alert-explain-high-cpu  # Single case
"""

import argparse
import asyncio
import sys

from src.eval.loader import load_eval_cases
from src.eval.report import print_case_result, print_summary
from src.eval.runner import run_eval_case


def _get_llm_config() -> dict[str, str]:
    """Load LLM config from environment / .env."""
    from src.config import get_settings

    settings = get_settings()
    return {
        "llm_provider": settings.llm_provider,
        "openai_api_key": settings.openai_api_key,
        "openai_model": settings.openai_model,
        "openai_base_url": settings.openai_base_url,
        "anthropic_api_key": settings.anthropic_api_key,
        "anthropic_model": settings.anthropic_model,
    }


async def _run_all(case_ids: list[str] | None) -> bool:
    """Run eval cases and return True if all passed."""
    config = _get_llm_config()
    cases = load_eval_cases(case_ids)

    provider = config["llm_provider"]
    model = config["anthropic_model"] if provider == "anthropic" else config["openai_model"]
    print(f"LLM provider: {provider}", file=sys.stderr)
    print(f"LLM model:    {model}", file=sys.stderr)
    print(f"Runbook search: disabled (evals use mocked HTTP, not RAG)", file=sys.stderr)
    print(f"Running {len(cases)} eval case(s)...\n", file=sys.stderr, flush=True)

    # Anthropic has a 30k input tokens/min rate limit — each case uses ~16k tokens
    # across 2-4 LLM round-trips, so we need to pace requests to avoid 429s.
    rate_limit_delay = 5.0 if provider == "anthropic" else 0.0

    results = []
    for i, case in enumerate(cases, 1):
        if i > 1 and rate_limit_delay > 0:
            print(
                f"  Waiting {rate_limit_delay:.0f}s (rate limit pacing)...",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(rate_limit_delay)
        print(f"[{i}/{len(cases)}] Running: {case.id}...", file=sys.stderr, flush=True)
        result = await run_eval_case(
            case,
            openai_api_key=config["openai_api_key"],
            openai_model=config["openai_model"],
            openai_base_url=config["openai_base_url"],
            llm_provider=config["llm_provider"],
            anthropic_api_key=config["anthropic_api_key"],
            anthropic_model=config["anthropic_model"],
        )
        print_case_result(result)
        results.append(result)

    print_summary(results)
    return all(r.passed for r in results)


def main() -> None:
    """Parse args and run eval."""
    parser = argparse.ArgumentParser(description="Run SRE assistant eval suite")
    parser.add_argument(
        "--case",
        type=str,
        action="append",
        default=None,
        help="Run specific case(s) by ID (can be repeated)",
    )
    args = parser.parse_args()

    all_passed = asyncio.run(_run_all(args.case))
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
