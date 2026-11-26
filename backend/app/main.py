"""FastAPI entrypoint. Stateless: no database, no server-side sessions."""
import logging

from fastapi import FastAPI, HTTPException

from .agent.orchestrator import Orchestrator
from .config import get_settings
from .llm.client import get_metrics
from .schemas.api import AgentResponse, FinalizeRequest, FinalPlan, PlanRequest
from .services import routing

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Outing Assistant API", version="1.0.0")
agent = Orchestrator()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": settings.llm_enabled,
        "venue_provider_configured": bool(settings.geoapify_api_key),
        "traffic_provider_configured": routing.available(),
    }


@app.get("/metrics")
def metrics() -> dict:
    return {"llm": get_metrics()}


@app.post("/api/v1/plan", response_model=AgentResponse)
def plan(req: PlanRequest) -> AgentResponse:
    return agent.run(req)


@app.post("/api/v1/finalize", response_model=FinalPlan)
def finalize(req: FinalizeRequest) -> FinalPlan:
    try:
        return agent.finalize(req)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
