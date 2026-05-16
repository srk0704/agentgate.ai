FROM python:3.11-slim

WORKDIR /app

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy dependency files first for layer caching
COPY pyproject.toml poetry.lock ./

# Install dependencies (no dev, no venv — run directly in container)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Copy source
COPY agentgate/ ./agentgate/
COPY examples/   ./examples/

# Create data directory for SQLite and policies
RUN mkdir -p /data

ENV AGENTGATE_DB_PATH=/data/agentgate.db
ENV AGENTGATE_POLICY_PATH=/data/policies.yaml

EXPOSE 8000

CMD ["uvicorn", "agentgate.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
