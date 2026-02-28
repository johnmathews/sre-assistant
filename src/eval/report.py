"""Report formatting for eval results."""

import sys

from src.eval.models import EvalResult

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"


def print_case_result(result: EvalResult) -> None:
    """Print a single eval case result to stderr."""
    status = _PASS if result.passed else _FAIL
    print(f"[{status}] {result.case_id}: {result.description}", file=sys.stderr)

    # Tool score
    ts = result.tool_score
    tool_status = _PASS if ts.passed else _FAIL
    print(f"  Tools [{tool_status}]: called {ts.called_tools}", file=sys.stderr)
    if ts.missing_tools:
        print(f"    Missing (must_call): {ts.missing_tools}", file=sys.stderr)
    if ts.forbidden_tools:
        print(f"    Forbidden (must_not_call): {ts.forbidden_tools}", file=sys.stderr)

    # Judge score
    js = result.judge_score
    judge_status = _PASS if js.passed else _FAIL
    print(f"  Answer [{judge_status}]: {js.explanation}", file=sys.stderr)

    # Agent answer (truncated)
    answer_preview = result.agent_answer[:200]
    if len(result.agent_answer) > 200:
        answer_preview += "..."
    print(f"  Answer preview: {answer_preview}\n", file=sys.stderr, flush=True)


def print_summary(results: list[EvalResult]) -> None:
    """Print overall eval summary to stderr."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    tool_passed = sum(1 for r in results if r.tool_score.passed)
    judge_passed = sum(1 for r in results if r.judge_score.passed)

    print(f"\n{'=' * 70}", file=sys.stderr)
    print(f"EVAL SUMMARY: {passed}/{total} passed", file=sys.stderr)
    print(f"  Tool selection: {tool_passed}/{total} passed", file=sys.stderr)
    print(f"  Answer quality: {judge_passed}/{total} passed", file=sys.stderr)
    print(f"{'=' * 70}", file=sys.stderr)

    if passed < total:
        failed = [r for r in results if not r.passed]
        print("\nFailed cases:", file=sys.stderr)
        for r in failed:
            reasons: list[str] = []
            if not r.tool_score.passed:
                reasons.append("tools")
            if not r.judge_score.passed:
                reasons.append("answer")
            print(f"  - {r.case_id} ({', '.join(reasons)})", file=sys.stderr)
