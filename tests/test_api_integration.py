"""Integration tests for the FastAPI backend.

Uses TestClient with mocked agent and HTTP calls — no real services needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_agent() -> MagicMock:
    """A fake agent object to stand in for the real compiled graph."""
    return MagicMock(name="fake_agent")


@pytest.fixture
def client(mock_settings: object, mock_agent: MagicMock) -> TestClient:  # noqa: ARG001 — mock_settings activates patches
    """Create a TestClient with the agent pre-injected into app state."""
    with patch("src.api.main.build_agent", return_value=mock_agent):
        from src.api.main import app

        with TestClient(app) as tc:
            yield tc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# POST /ask
# ---------------------------------------------------------------------------


class TestAskEndpoint:
    """Tests for POST /ask."""

    @pytest.mark.integration
    def test_successful_question(self, client: TestClient) -> None:
        with patch(
            "src.api.main.invoke_agent",
            new_callable=AsyncMock,
            return_value="CPU is at 42% on node-3.",
        ):
            resp = client.post("/ask", json={"question": "What is CPU on node-3?"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == "CPU is at 42% on node-3."
        assert "session_id" in body

    @pytest.mark.integration
    def test_server_generates_session_id(self, client: TestClient) -> None:
        with patch(
            "src.api.main.invoke_agent",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            resp = client.post("/ask", json={"question": "hello"})

        body = resp.json()
        assert len(body["session_id"]) == 8

    @pytest.mark.integration
    def test_client_session_id_echoed(self, client: TestClient) -> None:
        with patch(
            "src.api.main.invoke_agent",
            new_callable=AsyncMock,
            return_value="ok",
        ):
            resp = client.post(
                "/ask",
                json={"question": "hello", "session_id": "my-sess-1"},
            )

        assert resp.json()["session_id"] == "my-sess-1"

    @pytest.mark.integration
    def test_empty_question_returns_422(self, client: TestClient) -> None:
        resp = client.post("/ask", json={})
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_agent_failure_returns_500(self, client: TestClient) -> None:
        with patch(
            "src.api.main.invoke_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM exploded"),
        ):
            resp = client.post("/ask", json={"question": "boom"})

        assert resp.status_code == 500
        assert "LLM exploded" in resp.json()["detail"]

    @pytest.mark.integration
    def test_tool_call_pairing_error_recovered_by_invoke_agent(self, client: TestClient) -> None:
        """invoke_agent handles tool_call pairing errors internally, so the API
        should return 200 with the recovered response, not 500."""
        with patch(
            "src.api.main.invoke_agent",
            new_callable=AsyncMock,
            return_value="Recovered after session reset.",
        ):
            resp = client.post(
                "/ask",
                json={"question": "hello?", "session_id": "broken-sess"},
            )

        assert resp.status_code == 200
        assert resp.json()["response"] == "Recovered after session reset."

    @pytest.mark.integration
    def test_timeout_returns_500(self, client: TestClient) -> None:
        """A timeout that escapes invoke_agent surfaces as a 500."""
        with patch(
            "src.api.main.invoke_agent",
            new_callable=AsyncMock,
            side_effect=TimeoutError("timed out"),
        ):
            resp = client.post("/ask", json={"question": "slow"})

        assert resp.status_code == 500
        assert "timed out" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for GET /health."""

    @pytest.mark.integration
    @respx.mock
    def test_all_healthy(self, client: TestClient, tmp_path: object) -> None:
        respx.get("http://prometheus.test:9090/-/healthy").mock(
            return_value=httpx.Response(200, text="Prometheus Server is Healthy.")
        )
        respx.get("http://grafana.test:3000/api/health").mock(return_value=httpx.Response(200, json={"database": "ok"}))
        respx.get("http://loki.test:3100/ready").mock(return_value=httpx.Response(200, text="ready"))
        respx.get("https://proxmox.test:8006/api2/json/version").mock(
            return_value=httpx.Response(200, json={"data": {"version": "8.1.3"}})
        )
        respx.get("https://pbs.test:8007/api2/json/version").mock(
            return_value=httpx.Response(200, json={"data": {"version": "3.1.2"}})
        )
        respx.get("https://truenas.test/api/v2.0/core/ping").mock(return_value=httpx.Response(200, text="pong"))

        with patch("src.api.main.CHROMA_PERSIST_DIR") as mock_chroma_dir:
            mock_chroma_dir.is_dir.return_value = True
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["model"] == "gpt-4o-mini"
        assert len(body["components"]) == 7
        assert all(c["status"] == "healthy" for c in body["components"])

    @pytest.mark.integration
    @respx.mock
    def test_prometheus_unreachable(self, client: TestClient) -> None:
        respx.get("http://prometheus.test:9090/-/healthy").mock(side_effect=httpx.ConnectError("connection refused"))
        respx.get("http://grafana.test:3000/api/health").mock(return_value=httpx.Response(200, json={"database": "ok"}))
        respx.get("http://loki.test:3100/ready").mock(return_value=httpx.Response(200, text="ready"))
        respx.get("https://truenas.test/api/v2.0/core/ping").mock(return_value=httpx.Response(200, text="pong"))
        respx.get("https://proxmox.test:8006/api2/json/version").mock(
            return_value=httpx.Response(200, json={"data": {"version": "8.1.3"}})
        )
        respx.get("https://pbs.test:8007/api2/json/version").mock(
            return_value=httpx.Response(200, json={"data": {"version": "3.1.2"}})
        )

        with patch("src.api.main.CHROMA_PERSIST_DIR") as mock_chroma_dir:
            mock_chroma_dir.is_dir.return_value = True
            resp = client.get("/health")

        body = resp.json()
        assert body["status"] == "degraded"
        prom = next(c for c in body["components"] if c["name"] == "prometheus")
        assert prom["status"] == "unhealthy"

    @pytest.mark.integration
    @respx.mock
    def test_grafana_unreachable(self, client: TestClient) -> None:
        respx.get("http://prometheus.test:9090/-/healthy").mock(return_value=httpx.Response(200, text="ok"))
        respx.get("http://grafana.test:3000/api/health").mock(side_effect=httpx.ConnectError("connection refused"))
        respx.get("http://loki.test:3100/ready").mock(return_value=httpx.Response(200, text="ready"))
        respx.get("https://truenas.test/api/v2.0/core/ping").mock(return_value=httpx.Response(200, text="pong"))
        respx.get("https://proxmox.test:8006/api2/json/version").mock(
            return_value=httpx.Response(200, json={"data": {"version": "8.1.3"}})
        )
        respx.get("https://pbs.test:8007/api2/json/version").mock(
            return_value=httpx.Response(200, json={"data": {"version": "3.1.2"}})
        )

        with patch("src.api.main.CHROMA_PERSIST_DIR") as mock_chroma_dir:
            mock_chroma_dir.is_dir.return_value = True
            resp = client.get("/health")

        body = resp.json()
        assert body["status"] == "degraded"
        grafana = next(c for c in body["components"] if c["name"] == "grafana")
        assert grafana["status"] == "unhealthy"

    @pytest.mark.integration
    @respx.mock
    def test_all_unhealthy(self, client: TestClient) -> None:
        respx.get("http://prometheus.test:9090/-/healthy").mock(side_effect=httpx.ConnectError("connection refused"))
        respx.get("http://grafana.test:3000/api/health").mock(side_effect=httpx.ConnectError("connection refused"))
        respx.get("http://loki.test:3100/ready").mock(side_effect=httpx.ConnectError("connection refused"))
        respx.get("https://proxmox.test:8006/api2/json/version").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        respx.get("https://pbs.test:8007/api2/json/version").mock(side_effect=httpx.ConnectError("connection refused"))
        respx.get("https://truenas.test/api/v2.0/core/ping").mock(side_effect=httpx.ConnectError("connection refused"))

        with patch("src.api.main.CHROMA_PERSIST_DIR") as mock_chroma_dir:
            mock_chroma_dir.is_dir.return_value = False
            resp = client.get("/health")

        body = resp.json()
        assert body["status"] == "unhealthy"
        assert all(c["status"] == "unhealthy" for c in body["components"])
