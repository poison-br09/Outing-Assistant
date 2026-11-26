"""FastAPI entrypoint. Stateless: no database, no server-side sessions."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException

from .agent.orchestrator import Orchestrator
from .config import get_settings
from .llm.client import get_metrics
from .schemas.api import AgentResponse, FinalizeRequest, FinalPlan, PlanRequest, TripRequest, TripResponse
from .services import routing

settings = get_settings()
_logs = Path(__file__).resolve().parents[2] / "logs"
_logs.mkdir(exist_ok=True)
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), RotatingFileHandler(_logs / "app.log", maxBytes=5_000_000, backupCount=3)],
)

app = FastAPI(title="Outing Assistant API", version="1.0.0")
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines print URLs, which carry API keys
api_log = logging.getLogger("app.api")


@app.middleware("http")
async def log_io(request, call_next):
    t0 = time.monotonic()
    body = (await request.body()).decode("utf-8", "replace")
    api_log.info("REQ %s %s body=%.2000s", request.method, request.url.path, body)
    resp = await call_next(request)
    api_log.info("RES %s %s -> %s in %dms", request.method, request.url.path, resp.status_code,
                 (time.monotonic() - t0) * 1000)
    return resp
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


@app.post("/api/v1/trip", response_model=TripResponse)
def trip(req: TripRequest) -> TripResponse:
    return agent.run_trip(req)


@app.post("/api/v1/finalize", response_model=FinalPlan)
def finalize(req: FinalizeRequest) -> FinalPlan:
    try:
        return agent.finalize(req)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
