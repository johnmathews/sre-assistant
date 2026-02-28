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


def _get_openai_config() -> tuple[str, str, str]:
    """Load OpenAI config from environment / .env."""
    from src.config import get_settings

    settings = get_settings()
    return settings.openai_api_key, settings.openai_model, settings.openai_base_url


async def _run_all(case_ids: list[str] | None) -> bool:
    """Run eval cases and return True if all passed."""
    openai_api_key, openai_model, openai_base_url = _get_openai_config()
    cases = load_eval_cases(case_ids)

    print(f"Running {len(cases)} eval case(s)...", file=sys.stderr)

    results = []
    for case in cases:
        result = await run_eval_case(case, openai_api_key, openai_model, openai_base_url)
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
