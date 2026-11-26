# Outing Assistant: less planning, more living

An AI planning agent that turns a person's city, budget, time, mood, interests and constraints into a feasible, explained outing plan. You can describe the outing in a **form** or in **chat**, review the proposed plans (with trade-offs, cost breakdown and a visible agent trace), and only then approve one.

- **Backend:** Python, FastAPI, Pydantic. Stateless: there is no database.
- **Frontend:** Streamlit (form tab and chat tab).
- **LLM:** NVIDIA Nemotron only (OpenAI-compatible endpoint). The LLM does language work: free-text parsing, explanations, final overview. Deterministic code does ranking, costing, scheduling and validation.
- **No hardcoded venues or cities.** Everything comes from providers, with graceful fallbacks when they are missing.

## How it works

```
form / chat ─▶ 1 parseUserPreferences ─▶ 2 resolveTemporalContext ─▶ 3 getWeatherReport
   ─▶ 7 getActivityOptions ─▶ 8 rankActivities (uses 5 estimateCrowdLevel)
   ─▶ 4 getTrafficReport ─▶ 6 getVenueAvailability ─▶ 9 getFoodOptions
   ─▶ 11 buildItinerary ─▶ 12 optimiseItinerary ─▶ 10 estimateCost ─▶ 13 validatePlan
   ─▶ proposals (budget-friendly / balanced / experience-first) ─▶ user approval ─▶ 14 generateFinalPlan
```

| # | Tool | Module | Data |
|---|------|--------|------|
| 1 | parseUserPreferences | `tools/preference_parser.py` | Nemotron + regex fallback; classifies hard vs soft constraints; max two clarifying questions |
| 2 | resolveTemporalContext | `tools/temporal_resolver.py` | exact / tentative / period timing in the city's timezone |
| 3 | getWeatherReport | `tools/weather_tool.py` | Open-Meteo hourly forecast (live) |
| 4 | getTrafficReport | `tools/traffic_tool.py` | Google Routes if keyed (traffic-aware); otherwise a labelled **estimate** |
| 5 | estimateCrowdLevel | `tools/crowd_estimator.py` | heuristic, always labelled as such |
| 6 | getVenueAvailability | `tools/venue_availability.py` | parses provider opening hours; otherwise "unverified" |
| 7 | getActivityOptions | `tools/activity_finder.py` | Geoapify Places |
| 8 | rankActivities | `tools/activity_ranker.py` | weighted score with reasons; hard constraints are filters |
| 9 | getFoodOptions | `tools/food_finder.py` | Geoapify Places; dietary data flagged verified/unverified |
| 10 | estimateCost | `tools/cost_estimator.py` | activity + food + transport + parking + buffer (all estimates) |
| 11 | buildItinerary | `tools/itinerary_planner.py` | times, travel legs, buffers, hour checks |
| 12 | optimiseItinerary | `tools/itinerary_optimizer.py` | searches order x start time on travel, weather, crowds, hours, meal timing |
| 13 | validatePlan | `tools/plan_validator.py` | budget (+10% rule), timing, hours, diet, weather, crowds |
| 14 | generateFinalPlan | `tools/final_plan_generator.py` | only after explicit approval |

### Behaviour worth knowing
- **Budget:** the stated budget is a hard limit. The optional 10% extension only applies if the user chose "Yes" or ticked the approval box ("Show me the option and let me decide"). Finalisation re-validates server-side.
- **Exact timing** is never violated; tentative timing may shift within the declared flexibility; period timing lets the optimiser pick the best start.
- **Data labelling:** prices are *estimates* (providers return none), crowd levels are *heuristics*, opening hours come from the place provider and are *unverified* unless parsed, travel times are *estimates* unless Google Routes is configured.
- **Failure handling** (all visible in the trace and warnings): weather outage, traffic outage, unknown city, no venues, no food, tight budget (free-first plan), too little time (shorter plan), venue provider missing (generic, clearly labelled ideas, no invented venues), Nemotron timeout/error/invalid JSON (bounded retries, then deterministic logic with disclosure).

## Configuration

Copy `.env.example` to `.env` and fill in what you have. Everything is optional; missing providers degrade gracefully.

| Variable | Purpose |
|---|---|
| `NVIDIA_API_KEY`, `NVIDIA_NEMOTRON_MODEL`, `NVIDIA_BASE_URL` | Nemotron. The model id is intentionally not defaulted. |
| `LLM_REQUEST_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | bounded timeout / retries before deterministic fallback |
| `GEOAPIFY_API_KEY` | venues, place geocoding, opening hours |
| `GOOGLE_MAPS_API_KEY` | traffic-aware routing (optional) |
| `BACKEND_URL` | used by the Streamlit app (default `http://localhost:8000`) |

Estimation assumptions (speeds, fares, price levels, durations, scoring weights) live in `backend/app/data/assumptions.json`. They are estimates, not venue data. Edit them for other regions or currencies.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # add your keys

cd backend && uvicorn app.main:app --reload --port 8000     # terminal 1
streamlit run frontend/app.py                                # terminal 2 (from repo root)
```

Tests (no network needed): `cd backend && pytest -q`

## Docker

```bash
docker build -t outing-assistant .
docker run --env-file .env -p 8501:8501 outing-assistant   # open http://localhost:8501
```

## Deploy on Render

1. Push this repo to GitHub and create a **Blueprint** in Render from `render.yaml` (or a Docker web service).
2. Set the secret env vars in the dashboard (`NVIDIA_API_KEY`, `NVIDIA_NEMOTRON_MODEL`, `GEOAPIFY_API_KEY`, optionally `GOOGLE_MAPS_API_KEY`).
3. Open the service URL and run a real plan. Note the free tier sleeps when idle, so the first request can take about a minute.

## API

- `GET /health`: configuration status (no secrets)
- `GET /metrics`: Nemotron call, error, timeout and latency counters
- `POST /api/v1/plan`: `{fields, message, message_overrides, excluded_venue_ids}` returns clarification questions, proposals, or a labelled fallback, plus the trace
- `POST /api/v1/finalize`: `{preferences, proposal, temporal, weather, approved, accept_extra_budget}` returns the final plan (403 without approval)

## Known limitations
- Venue prices are estimates; hours and diet data depend on provider coverage (OpenStreetMap-derived data is thinner in some areas).
- Crowd levels are heuristics. Travel times are estimates unless Google Routes is configured.
- No bookings are made. The plan is a proposal.
- The proposal is echoed back by the client at finalisation. It is re-costed and re-validated server-side, but venue details are not re-fetched.
