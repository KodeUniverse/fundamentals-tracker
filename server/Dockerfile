FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml* uv.lock* ./
COPY . .

RUN if [ -f uv.lock ]; then uv sync --frozen --no-dev; else pip install --no-cache-dir .; fi

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# FastAPI server
CMD ["sh", "-c", "if [ -f .venv/bin/fastapi ]; then .venv/bin/fastapi run main.py --host 0.0.0.0 --port 8000; elif [ -f .venv/bin/python ]; then .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000; else python -m uvicorn main:app --host 0.0.0.0 --port 8000; fi"]
