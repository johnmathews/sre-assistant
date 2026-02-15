"""FastAPI backend for the SRE assistant.

Provides HTTP endpoints so the agent can be consumed by web clients.
The agent is built once at startup and shared across requests.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.agent.agent import build_agent, invoke_agent
from src.agent.retrieval.embeddings import CHROMA_PERSIST_DIR
from src.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    """Request body for POST /ask."""

    question: str
    session_id: str | None = None


class AskResponse(BaseModel):
    """Response body for POST /ask."""

    response: str
    session_id: str


class ComponentHealth(BaseModel):
    """Health status of a single infrastructure component."""

    name: str
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str
    components: list[ComponentHealth]


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the agent once at startup, tear down on shutdown."""
    logger.info("Building SRE assistant agent...")
    try:
        agent = build_agent()
        app.state.agent = agent
        logger.info("Agent ready")
    except Exception:
        logger.exception("Failed to build agent at startup")
        raise
    yield
    logger.info("Shutting down SRE assistant")


app = FastAPI(title="HomeLab SRE Assistant", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Send a question to the SRE assistant and get a response."""
    session_id = request.session_id or uuid4().hex[:8]

    try:
        response = await invoke_agent(
            app.state.agent,
            request.question,
            session_id=session_id,
        )
    except Exception as exc:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return AskResponse(response=response, session_id=session_id)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Check health of the assistant and its dependencies."""
    settings = get_settings()
    components: list[ComponentHealth] = []

    # --- Prometheus ---
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.prometheus_url}/-/healthy")
            if resp.status_code == 200:
                components.append(ComponentHealth(name="prometheus", status="healthy"))
            else:
                components.append(
                    ComponentHealth(
                        name="prometheus",
                        status="unhealthy",
                        detail=f"HTTP {resp.status_code}",
                    )
                )
    except Exception as exc:
        components.append(ComponentHealth(name="prometheus", status="unhealthy", detail=str(exc)))

    # --- Grafana ---
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.grafana_url}/api/health",
                headers={"Authorization": f"Bearer {settings.grafana_service_account_token}"},
            )
            if resp.status_code == 200:
                components.append(ComponentHealth(name="grafana", status="healthy"))
            else:
                components.append(
                    ComponentHealth(
                        name="grafana",
                        status="unhealthy",
                        detail=f"HTTP {resp.status_code}",
                    )
                )
    except Exception as exc:
        components.append(ComponentHealth(name="grafana", status="unhealthy", detail=str(exc)))

    # --- Vector store ---
    chroma_path = Path(CHROMA_PERSIST_DIR)
    if chroma_path.is_dir():
        components.append(ComponentHealth(name="vector_store", status="healthy"))
    else:
        components.append(
            ComponentHealth(
                name="vector_store",
                status="unhealthy",
                detail=f"{chroma_path}/ not found — run 'make ingest'",
            )
        )

    # --- Overall status ---
    healthy_count = sum(1 for c in components if c.status == "healthy")
    if healthy_count == len(components):
        overall = "healthy"
    elif healthy_count == 0:
        overall = "unhealthy"
    else:
        overall = "degraded"

    return HealthResponse(status=overall, components=components)
