.PHONY: install dev lint typecheck test serve

install:
	uv sync

dev:
	uv sync --group dev

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

format:
	uv run ruff check --fix src/ tests/
	uv run ruff format src/ tests/

typecheck:
	uv run mypy src/

test:
	uv run pytest

serve:
	uv run uvicorn src.api.main:app --reload --port 8000
