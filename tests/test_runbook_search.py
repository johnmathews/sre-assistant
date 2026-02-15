"""Unit tests for the runbook search tool — formatting and input validation."""

from src.agent.retrieval.runbooks import RunbookSearchInput


class TestRunbookSearchInput:
    def test_default_num_results(self) -> None:
        inp = RunbookSearchInput(query="DNS troubleshooting")
        assert inp.num_results == 4

    def test_custom_num_results(self) -> None:
        inp = RunbookSearchInput(query="UPS battery", num_results=2)
        assert inp.num_results == 2

    def test_max_num_results(self) -> None:
        inp = RunbookSearchInput(query="NFS shares", num_results=10)
        assert inp.num_results == 10

    def test_rejects_zero_results(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RunbookSearchInput(query="test", num_results=0)

    def test_rejects_over_max(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RunbookSearchInput(query="test", num_results=11)
