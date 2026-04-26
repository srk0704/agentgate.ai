"""Tests for LoopDetector — retry storms + sequence loops."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4

import aiosqlite
import pytest
from unittest.mock import patch

from agentgate.loop_detector import LoopDetector
from agentgate.models import ToolCall


@pytest.fixture
def detector(tmp_path):
    return LoopDetector(db_path=str(tmp_path / "loop.db"))


async def _create_tables(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_calls (
                id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, session_id TEXT,
                tool_name TEXT NOT NULL, original_task TEXT, called_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS output_log (
                id TEXT PRIMARY KEY, call_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                tool_name TEXT NOT NULL, tool_result TEXT, success INTEGER NOT NULL,
                error_message TEXT, agent_final_response TEXT, user_retried INTEGER DEFAULT 0,
                outcome_type TEXT, financial_impact REAL, logged_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def _seed_session(db_path: str, agent_id: str, tools: list[str], session_id: str | None = None):
    await _create_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        for i, t in enumerate(tools):
            ts = datetime.utcnow().isoformat()
            await db.execute(
                "INSERT INTO session_calls VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), agent_id, session_id, t, None, ts),
            )
        await db.commit()


async def _seed_output(db_path: str, agent_id: str, tool_name: str, success: bool, count: int = 1):
    await _create_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        for _ in range(count):
            await db.execute(
                """INSERT INTO output_log
                   (id, call_id, agent_id, tool_name, tool_result, success,
                    error_message, outcome_type, financial_impact, logged_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), str(uuid4()), agent_id, tool_name, "{}",
                    1 if success else 0, None,
                    "success" if success else "failure", None,
                    datetime.utcnow().isoformat(),
                ),
            )
        await db.commit()


async def test_no_loop_normal_usage(detector):
    await _seed_session(
        detector.db_path, "a1",
        ["get_customer", "issue_refund", "send_email"],
        session_id="s1",
    )
    tc = ToolCall(tool_name="search_orders", args={}, agent_id="a1", session_id="s1")
    score, _ = await detector.score(tc)
    assert score == 0


async def test_retry_storm_detected(detector):
    # 5 prior calls of get_account_status + 4 failures → retry storm
    await _seed_session(
        detector.db_path, "a1",
        ["get_account_status"] * 5,
        session_id="s1",
    )
    await _seed_output(detector.db_path, "a1", "get_account_status", success=False, count=4)
    tc = ToolCall(tool_name="get_account_status", args={}, agent_id="a1", session_id="s1")
    score, reason = await detector.score(tc)
    assert score > 70
    assert "retry storm" in reason.lower() or "called" in reason.lower()


async def test_retry_without_failure_low_score(detector):
    await _seed_session(
        detector.db_path, "a1",
        ["get_data"] * 4,
        session_id="s1",
    )
    await _seed_output(detector.db_path, "a1", "get_data", success=True, count=4)
    tc = ToolCall(tool_name="get_data", args={}, agent_id="a1", session_id="s1")
    score, _ = await detector.score(tc)
    assert score < 40


async def test_sequence_loop_detected(detector):
    await _seed_session(
        detector.db_path, "a1",
        ["get_customer", "issue_refund", "get_customer", "issue_refund"],
        session_id="s1",
    )
    tc = ToolCall(tool_name="get_customer", args={}, agent_id="a1", session_id="s1")
    score, reason = await detector.score(tc)
    assert score > 70
    assert "sequence" in reason.lower() or "loop" in reason.lower()


async def test_insufficient_history_no_false_positive(detector):
    await _seed_session(
        detector.db_path, "a1",
        ["get_customer", "issue_refund"],
        session_id="s1",
    )
    tc = ToolCall(tool_name="get_customer", args={}, agent_id="a1", session_id="s1")
    score, _ = await detector.score(tc)
    assert score == 0


async def test_never_raises(detector):
    tc = ToolCall(tool_name="x", args={}, agent_id="a1", session_id="s1")
    with patch.object(
        detector._session_tracker, "_ensure_init",
        side_effect=RuntimeError("db down"),
    ):
        score, reason = await detector.score(tc)
    assert isinstance(score, int)
    assert isinstance(reason, str)
