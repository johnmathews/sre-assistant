"""Load and validate eval cases from YAML files."""

from pathlib import Path
from typing import Any

import yaml

from src.eval.models import EvalCase

CASES_DIR = Path(__file__).parent / "cases"


def load_eval_cases(case_ids: list[str] | None = None) -> list[EvalCase]:
    """Load eval cases from YAML files in the cases directory.

    Args:
        case_ids: If provided, only load cases with these IDs. Otherwise load all.

    Returns:
        List of validated EvalCase objects.

    Raises:
        FileNotFoundError: If cases directory doesn't exist or a requested case ID not found.
        ValueError: If a YAML file fails validation.
    """
    if not CASES_DIR.is_dir():
        msg = f"Eval cases directory not found: {CASES_DIR}"
        raise FileNotFoundError(msg)

    yaml_files = sorted(CASES_DIR.glob("*.yaml"))
    if not yaml_files:
        msg = f"No YAML files found in {CASES_DIR}"
        raise FileNotFoundError(msg)

    cases: list[EvalCase] = []
    for path in yaml_files:
        raw: Any = yaml.safe_load(path.read_text())
        if raw is None:
            continue
        try:
            case = EvalCase.model_validate(raw)
        except Exception as exc:
            msg = f"Failed to parse {path.name}: {exc}"
            raise ValueError(msg) from exc
        cases.append(case)

    if case_ids is not None:
        id_set = set(case_ids)
        filtered = [c for c in cases if c.id in id_set]
        found_ids = {c.id for c in filtered}
        missing = id_set - found_ids
        if missing:
            msg = f"Eval case IDs not found: {', '.join(sorted(missing))}"
            raise FileNotFoundError(msg)
        return filtered

    return cases
