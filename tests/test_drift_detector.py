"""Tests for DriftDetector — context drift away from the user's original task."""
from __future__ import annotations
import pytest
from unittest.mock import patch

import aiosqlite

from agentgate.drift_detector import DriftDetector
from agentgate.models import ToolCall


@pytest.fixture
def detector(tmp_path, monkeypatch):
    # Compliance mode disables LLM stage so tests are deterministic.
    return DriftDetector(db_path=str(tmp_path / "drift.db"), compliance_mode=True)


async def _seed_session(db_path: str, agent_id: str, tools: list[str], session_id: str | None = None):
    from datetime import datetime, timezone
    from uuid import uuid4
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_calls (
                id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_id TEXT,
                tool_name TEXT NOT NULL, original_task TEXT, called_at TEXT NOT NULL
            )
        """)
        for i, t in enumerate(tools):
            ts = datetime.now(timezone.utc).isoformat() + f"-{i}"
            await db.execute(
                "INSERT INTO session_calls VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), agent_id, session_id, t, None, ts),
            )
        await db.commit()


async def test_no_drift_matching_task(detector):
    tc = ToolCall(
        tool_name="issue_refund",
        args={"transaction_id": "txn", "amount": 50},
        agent_id="a1",
        original_task="Process refund for customer",
    )
    score, _ = await detector.score(tc)
    assert score < 30


async def test_structural_drift_export_on_read_task(detector):
    tc = ToolCall(
        tool_name="export_customer_data",
        args={"format": "csv"},
        agent_id="a1",
        original_task="Check account balance for cust_001",
    )
    score, reason = await detector.score(tc)
    assert score > 70
    assert "export" in reason.lower()


async def test_structural_drift_destructive_on_lookup(detector):
    tc = ToolCall(
        tool_name="freeze_account",
        args={"account_id": "acc_001"},
        agent_id="a1",
        original_task="Check fraud flags for customer",
    )
    score, _ = await detector.score(tc)
    assert score > 50


async def test_no_original_task_returns_zero(detector):
    tc = ToolCall(
        tool_name="export_customer_data",
        args={},
        agent_id="a1",
        original_task=None,
    )
    score, reason = await detector.score(tc)
    assert score == 0
    assert "no original task" in reason.lower()


async def test_history_drift_sudden_destructive(detector, tmp_path):
    db_path = detector.db_path
    await _seed_session(
        db_path, "a1",
        ["get_customer_info", "list_users", "fetch_balance",
         "read_config", "search_transactions"],
        session_id="s1",
    )
    tc = ToolCall(
        tool_name="freeze_account",
        args={},
        agent_id="a1",
        session_id="s1",
        original_task=None,  # bypass structural; force history-only signal
    )
    score, reason = await detector.score(tc)
    assert score > 60
    assert "destructive" in reason.lower() or "history" in reason.lower()


async def test_insufficient_history_returns_zero(detector):
    db_path = detector.db_path
    await _seed_session(db_path, "a1", ["get_customer_info", "list_users"], session_id="s2")
    tc = ToolCall(
        tool_name="freeze_account",
        args={},
        agent_id="a1",
        session_id="s2",
        original_task=None,
    )
    history_score, history_reason = await detector._history_drift(tc)
    assert history_score == 0
    assert "not enough session history" in history_reason.lower()


async def test_never_raises_on_bad_input(detector):
    tc = ToolCall(tool_name="anything", args={}, agent_id="a1", original_task="check")
    with patch.object(
        detector._session_tracker, "_ensure_init",
        side_effect=RuntimeError("db down"),
    ):
        score, reason = await detector.score(tc)
    assert isinstance(score, int)
    assert isinstance(reason, str)
