"""Shared pytest configuration and fixtures."""

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run e2e tests that hit real services (requires .env with valid credentials)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-e2e"):
        return
    skip_e2e = pytest.mark.skip(reason="Need --run-e2e flag to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


@pytest.fixture
def mock_settings() -> Generator[Any]:
    """Provide fake settings so tests don't need a .env file.

    Patches get_settings at every import site so cached references are overridden.
    """
    fake_settings = type(
        "FakeSettings",
        (),
        {
            "openai_api_key": "sk-proj-test-fake",
            "openai_model": "gpt-4o-mini",
            "prometheus_url": "http://prometheus.test:9090",
            "grafana_url": "http://grafana.test:3000",
            "grafana_service_account_token": "glsa_test_fake",
            "proxmox_url": "https://proxmox.test:8006",
            "proxmox_api_token": "test@pam!test=fake-token",
            "proxmox_verify_ssl": False,
            "proxmox_ca_cert": "",
            "proxmox_node": "proxmox",
            "pbs_url": "https://pbs.test:8007",
            "pbs_api_token": "test@pbs!test=fake-token",
            "pbs_verify_ssl": False,
            "pbs_ca_cert": "",
            "pbs_node": "localhost",
            "pbs_default_datastore": "backups",
        },
    )()
    with (
        patch("src.config.get_settings", return_value=fake_settings),
        patch("src.agent.tools.prometheus.get_settings", return_value=fake_settings),
        patch("src.agent.tools.grafana_alerts.get_settings", return_value=fake_settings),
        patch("src.agent.tools.proxmox.get_settings", return_value=fake_settings),
        patch("src.agent.tools.pbs.get_settings", return_value=fake_settings),
        patch("src.agent.agent.get_settings", return_value=fake_settings),
        patch("src.agent.retrieval.embeddings.get_settings", return_value=fake_settings),
        patch("src.api.main.get_settings", return_value=fake_settings),
    ):
        yield fake_settings
