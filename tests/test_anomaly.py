from __future__ import annotations
import asyncio
import pytest
from agentgate.anomaly import AnomalyScorer
from agentgate.session import SessionTracker
from agentgate.models import ToolCall


@pytest.fixture
def tracker(tmp_path):
    return SessionTracker(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def scorer(tracker):
    return AnomalyScorer(session_tracker=tracker)


def _call(tool_name: str, agent_id: str = "agent-1", original_task: str | None = None) -> ToolCall:
    return ToolCall(
        tool_name=tool_name,
        args={},
        agent_id=agent_id,
        session_id="session-test",
        original_task=original_task,
    )


# ── Velocity detection ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_velocity_detection(scorer):
    """6 rapid calls to the same tool should trigger a velocity flag."""
    tool = "delete_record"
    for _ in range(6):
        await scorer.score(_call(tool))

    score, reason = await scorer.score(_call(tool))
    assert score > 30, f"Expected elevated score, got {score}"
    assert "velocity" in reason.lower() or "delete_record" in reason


@pytest.mark.asyncio
async def test_scope_drift_detection(scorer):
    """Calling many different tools in a short session flags scope drift."""
    tools = [
        "read_file", "send_email", "execute_sql", "delete_user",
        "export_data", "modify_config", "restart_service",
    ]
    # Call 7 different tools in quick succession
    for t in tools:
        await scorer.score(_call(t))

    # 8th call — should now see high diversity
    score, reason = await scorer.score(_call("another_tool"))
    assert score > 30, f"Expected scope drift flag, got {score}"
    assert "scope" in reason.lower() or "unique" in reason.lower() or score > 30


@pytest.mark.asyncio
async def test_normal_session_not_flagged(scorer):
    """A normal session with 3 calls to the same tool should not be flagged."""
    for _ in range(3):
        score, reason = await scorer.score(_call("issue_refund"))
    assert score < 30, f"Expected low score for normal session, got {score}"


@pytest.mark.asyncio
async def test_different_agents_isolated(scorer):
    """High velocity on agent-1 should not affect agent-2 score."""
    for _ in range(7):
        await scorer.score(_call("delete_record", agent_id="agent-1"))

    # agent-2 makes its first call — should be clean
    score, _ = await scorer.score(_call("delete_record", agent_id="agent-2"))
    assert score < 30, f"agent-2 should not be flagged, got score {score}"


@pytest.mark.asyncio
async def test_session_tracker_records(tracker):
    """SessionTracker.record() stores calls and get_session_stats returns them."""
    tc = _call("read_data", agent_id="tracker-agent")
    await tracker.record(tc)
    await tracker.record(tc)

    stats = await tracker.get_session_stats("tracker-agent", window_minutes=5)
    assert stats["call_count"] == 2
    assert stats["unique_tools"] == 1
    assert stats["tool_frequency"]["read_data"] == 2


@pytest.mark.asyncio
async def test_scorer_never_raises(scorer):
    """AnomalyScorer should swallow internal errors and return a safe default."""
    # Force an error by breaking the tracker's db path after init
    scorer._tracker.db_path = "/nonexistent/path/to/db.sqlite"
    scorer._tracker._initialized = False  # force reinit attempt
    score, reason = await scorer.score(_call("any_tool"))
    # Should return a safe fallback, not raise
    assert isinstance(score, int)
    assert isinstance(reason, str)
