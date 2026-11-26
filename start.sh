#!/usr/bin/env bash
# Runs FastAPI (internal) and Streamlit (public) in one container. Exits if either dies.
set -euo pipefail

(cd backend && exec uvicorn app.main:app --host 127.0.0.1 --port 8000) &
for _ in $(seq 1 30); do
  python -c "import httpx; httpx.get('http://127.0.0.1:8000/health', timeout=1).raise_for_status()" 2>/dev/null && break
  sleep 1
done

streamlit run frontend/app.py --server.port "${PORT:-8501}" --server.address 0.0.0.0 \
  --server.headless true --browser.gatherUsageStats false &

wait -n
exit $?
