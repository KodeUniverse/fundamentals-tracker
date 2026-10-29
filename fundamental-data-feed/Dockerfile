FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install system deps if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

# Copy project files
COPY pyproject.toml* uv.lock* ./
COPY . .

# Install dependencies
RUN if [ -f uv.lock ]; then uv sync --frozen --no-dev; else pip install --no-cache-dir . && pip install --no-cache-dir edgartools psycopg[binary] httpx yfinance pandas sqlalchemy; fi

ENV PATH="/app/.venv/bin:$PATH"

# Run as non-root
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Process entrypoint 
CMD ["python", "main.py"]
