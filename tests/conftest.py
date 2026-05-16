"""
Test-suite isolation.

The dev `.env` file may contain a real ANTHROPIC_API_KEY for running the
demo / dashboard against live LLM scoring. Tests should NOT call the LLM —
they should be deterministic and cost nothing. This conftest forces every
test to use heuristic-only scoring regardless of whatever is in the shell
environment or `.env`.
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent


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
    # Tests run in dev mode so the production-only "AGENTGATE_API_KEY required"
    # guard in the API middleware doesn't 500 the TestClient.
    monkeypatch.setenv("AGENTGATE_ENV", "development")
    yield


@pytest.fixture(autouse=True)
def _no_db_leak_in_repo_root():
    """Fail the test if it drops a SQLite file into the repo root.

    Tests should always use ``tmp_path`` (which pytest cleans up) instead of
    the default ``./agentgate.db`` fallback. Catching the leak here makes the
    accidental ``GatewayClient.from_env()`` pattern immediately visible.
    """
    before = {p.name for p in _REPO_ROOT.glob("*.db")}
    before |= {p.name for p in _REPO_ROOT.glob("*.db-shm")}
    before |= {p.name for p in _REPO_ROOT.glob("*.db-wal")}
    yield
    after = {p.name for p in _REPO_ROOT.glob("*.db")}
    after |= {p.name for p in _REPO_ROOT.glob("*.db-shm")}
    after |= {p.name for p in _REPO_ROOT.glob("*.db-wal")}
    leaked = after - before
    if leaked:
        for name in leaked:
            (_REPO_ROOT / name).unlink(missing_ok=True)
        raise AssertionError(
            f"Test leaked SQLite file(s) into the repo root: {sorted(leaked)}. "
            "Use the tmp_path fixture for any DB path."
        )


@pytest.fixture
def temp_db(tmp_path):
    """Per-test DB path under pytest's auto-cleaning tmp_path."""
    return str(tmp_path / "test.db")


@pytest.fixture
async def gate(temp_db, tmp_path):
    """Generic GatewayClient with a permissive default policy and proper
    teardown. Individual test files keep their own gate fixtures when they
    need bespoke policy shapes — this one is for tests that just need a
    working gate without policy plumbing."""
    from agentgate.client import GatewayClient

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "policies:\n"
        "  - name: allow_default\n"
        "    effect: allow\n"
        "    reason: \"Permitted by default in test fixture\"\n"
    )

    client = GatewayClient(
        policy_path=str(policy_path),
        db_path=temp_db,
        fail_open=False,
    )
    try:
        yield client
    finally:
        try:
            await client.close()
        except Exception:
            pass
