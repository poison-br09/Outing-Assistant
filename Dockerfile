FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    BACKEND_URL=http://127.0.0.1:8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY start.sh .
RUN useradd -m app && chmod +x start.sh && chown -R app /app
USER app

# Streamlit is the public port (Render injects $PORT); FastAPI stays internal on 127.0.0.1:8000.
EXPOSE 8501
CMD ["./start.sh"]
