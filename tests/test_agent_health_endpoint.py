"""Tests for GET /health/agents and the per-agent health computation."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agentgate.audit import AuditLogger
from agentgate.models import Decision, DecisionOutcome, ToolCall


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "health.db")
    monkeypatch.setenv("AGENTGATE_DB_PATH", db_path)
    monkeypatch.setenv("AGENTGATE_POLICY_PATH", str(tmp_path / "policies.yaml"))
    # AGENTGATE_API_KEY must be unset so endpoints don't require auth
    monkeypatch.delenv("AGENTGATE_API_KEY", raising=False)
    return db_path


async def _seed_decision(
    audit: AuditLogger,
    agent_id: str,
    *,
    outcome: DecisionOutcome = DecisionOutcome.ALLOWED,
    risk: int | None = None,
    injection: int | None = None,
    anomaly: int | None = None,
    minutes_ago: int = 0,
    tool_name: str = "issue_refund",
    attack_type: str | None = None,
):
    tc = ToolCall(tool_name=tool_name, args={"amount": 50}, agent_id=agent_id)
    d = Decision(
        outcome=outcome,
        tool_call=tc,
        reason="seed",
        risk_score=risk,
        injection_score=injection,
        anomaly_score=anomaly,
        attack_type=attack_type,
        decided_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    d.reliability_score, d.reliability_summary = Decision.compute_reliability_score(
        risk_score=risk, injection_score=injection, anomaly_score=anomaly,
    )
    await audit.log(d)


@pytest.fixture
def client(temp_db):
    # Import after env vars are set so middleware sees them
    from agentgate.api.main import app
    return TestClient(app)


async def test_health_endpoint_returns_all_agents(temp_db, client):
    audit = AuditLogger(temp_db)
    await _seed_decision(audit, "agent-a", risk=10)
    await _seed_decision(audit, "agent-b", risk=10)
    await _seed_decision(audit, "agent-c", risk=10)

    r = client.get("/health/agents")
    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["total_agents"] == 3
    assert {a["agent_id"] for a in data["agents"]} == {"agent-a", "agent-b", "agent-c"}


async def test_health_endpoint_sorts_worst_first(temp_db, client):
    audit = AuditLogger(temp_db)
    await _seed_decision(audit, "healthy-agent", risk=5)
    await _seed_decision(
        audit, "broken-agent",
        outcome=DecisionOutcome.BLOCKED,
        injection=92, attack_type="goal_hijacking",
    )
    await _seed_decision(audit, "ok-agent", risk=20)

    r = client.get("/health/agents")
    agents = r.json()["agents"]
    # Worst (lowest health_score) must come first
    assert agents[0]["agent_id"] == "broken-agent"
    assert agents[0]["health_status"] == "Critical"
    assert agents[-1]["agent_id"] == "healthy-agent"


async def test_healthy_agent_has_no_active_issues(temp_db, client):
    audit = AuditLogger(temp_db)
    await _seed_decision(audit, "clean-agent", risk=5)
    await _seed_decision(audit, "clean-agent", risk=8)

    r = client.get("/health/agents")
    agents = {a["agent_id"]: a for a in r.json()["agents"]}
    assert agents["clean-agent"]["active_issues"] == []
    assert agents["clean-agent"]["health_status"] == "Healthy"
    assert agents["clean-agent"]["health_score"] >= 90


async def test_degraded_agent_shows_issues(temp_db, client):
    audit = AuditLogger(temp_db)
    # Three recent injection events on the same agent → one rolled-up issue
    await _seed_decision(
        audit, "noisy-agent", outcome=DecisionOutcome.BLOCKED,
        injection=78, attack_type="data_exfiltration",
    )
    await _seed_decision(
        audit, "noisy-agent", outcome=DecisionOutcome.BLOCKED,
        injection=82, attack_type="data_exfiltration",
    )
    await _seed_decision(audit, "noisy-agent", risk=65)

    r = client.get("/health/agents")
    agents = {a["agent_id"]: a for a in r.json()["agents"]}
    issues = agents["noisy-agent"]["active_issues"]
    assert len(issues) >= 1
    types = {i["type"] for i in issues}
    assert "injection" in types
    inj_issue = next(i for i in issues if i["type"] == "injection")
    assert inj_issue["occurrences"] >= 2
