"""
Test-suite isolation.

The dev `.env` file may contain a real ANTHROPIC_API_KEY for running the
demo / dashboard against live LLM scoring. Tests should NOT call the LLM —
they should be deterministic and cost nothing. This conftest forces every
test to use heuristic-only scoring regardless of whatever is in the shell
environment or `.env`.
"""
from __future__ import annotations
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_test_env(monkeypatch):
    # Force heuristic scoring across all tests.
    monkeypatch.setenv("AGENTGATE_COMPLIANCE_MODE", "true")
    # Drop any inherited Anthropic key so accidental LLM paths fail closed.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Don't let .env override AGENTGATE_DB_PATH that individual tests set.
    monkeypatch.delenv("AGENTGATE_DB_PATH", raising=False)
    # Strip any auth gating that could 401 the FastAPI TestClient.
    monkeypatch.delenv("AGENTGATE_API_KEY", raising=False)
    yield
