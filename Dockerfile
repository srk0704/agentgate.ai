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

# Run as a fixed non-root uid so volume mounts can be pre-chowned to it and
# the container survives enterprise pod-security-policy / OpenShift defaults.
RUN useradd -u 1000 -m -s /bin/bash app \
    && chown -R app:app /app /data

ENV AGENTGATE_DB_PATH=/data/agentgate.db
ENV AGENTGATE_POLICY_PATH=/data/policies.yaml

USER app

EXPOSE 8000

CMD ["uvicorn", "agentgate.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
