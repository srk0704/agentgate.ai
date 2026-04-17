#!/bin/bash
# Start AgentGate dashboard with a clean database every time.

DB="./examples/fintech_live_agent/agent_demo.db"

echo "Clearing demo database..."
rm -f "$DB" "${DB}-shm" "${DB}-wal"
echo "Done. Starting server..."
echo ""

poetry run uvicorn agentgate.api.main:app --host 0.0.0.0 --port 8000
