# Outing Assistant: project guide for Claude

Read `instruction.md` (rules), `plan.md` (full product brief) and `project-requirement.md` (original assignment) before changing behaviour.

## Layout
```
backend/app/
  main.py            FastAPI app: /health, /metrics, /api/v1/plan, /api/v1/finalize
  config.py          env-driven Settings and assumptions loader
  schemas/           Pydantic models (preferences, context, itinerary, api)
  llm/client.py      Nemotron client: retry, JSON validation, metrics
  services/          external providers: geo.py (Open-Meteo + Geoapify), routing.py (Google Routes)
  tools/             the 14 tools, one module each
  agent/             orchestrator.py, trace.py
  data/assumptions.json   editable estimation assumptions (NOT venue data)
backend/tests/       pytest, no network
frontend/app.py      Streamlit: form tab + chat tab
Dockerfile, start.sh, render.yaml
```

## Commands
- Setup: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
- Tests: `cd backend && pytest -q`
- Backend: `cd backend && uvicorn app.main:app --reload --port 8000`
- Frontend: `streamlit run frontend/app.py` (reads `BACKEND_URL`, default `http://localhost:8000`)
- Both in Docker: `docker build -t outing . && docker run --env-file .env -p 8501:8501 outing`

## Rules
- LLM provider: Nemotron only. No other provider.
- No hardcoded venues, cities or models. Data comes from providers; assumptions come from `assumptions.json`.
- Tools stay separate, take typed inputs and return typed outputs. Keep calculation and validation deterministic.
- Every tool failure must degrade gracefully and add a warning to the trace. Never fabricate live data.
- Tests must not hit the network. Stub the services layer.
- Never commit `.env` or keys.
