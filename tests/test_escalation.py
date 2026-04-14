import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentgate.models import ToolCall, DecisionOutcome
from agentgate.client import GatewayClient
from agentgate.escalation import EscalationQueue
from agentgate.integrations.langchain import guarded_tool, ToolException


@pytest.fixture
def gate(tmp_path):
    policy_file = tmp_path / "policies.yaml"
    policy_file.write_text("""
policies:
  - name: escalate_big_ops
    match:
      tool: dangerous_operation
    effect: escalate
    reason: "Dangerous operations require approval"
""")
    db_path = str(tmp_path / "test.db")
    EscalationQueue.configure(db_path)
    # Reset initialization flag for each test
    EscalationQueue._initialized = False
    return GatewayClient(
        policy_path=str(policy_file),
        db_path=db_path,
        fail_open=True,
    )


# ============================================================================
# Escalation Queue Tests
# ============================================================================


@pytest.mark.asyncio
async def test_escalation_submit_creates_entry(gate):
    """Test that submitting an escalation creates a DB entry."""
    tc = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "production"},
        agent_id="agent-1",
    )
    escalation_id = await EscalationQueue.submit(tc, risk_score=75)

    assert escalation_id is not None
    entry = await EscalationQueue.get_by_id(escalation_id)
    assert entry is not None
    assert entry["status"] == "pending"
    assert entry["tool_name"] == "dangerous_operation"
    assert entry["risk_score"] == 75


@pytest.mark.asyncio
async def test_escalation_approve(gate):
    """Test approving an escalation."""
    tc = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "prod"},
        agent_id="agent-1",
    )
    escalation_id = await EscalationQueue.submit(tc, risk_score=80)

    await EscalationQueue.approve(escalation_id)

    entry = await EscalationQueue.get_by_id(escalation_id)
    assert entry["status"] == "approved"


@pytest.mark.asyncio
async def test_escalation_reject(gate):
    """Test rejecting an escalation."""
    tc = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "prod"},
        agent_id="agent-1",
    )
    escalation_id = await EscalationQueue.submit(tc, risk_score=80)

    await EscalationQueue.reject(escalation_id)

    entry = await EscalationQueue.get_by_id(escalation_id)
    assert entry["status"] == "rejected"


@pytest.mark.asyncio
async def test_wait_for_decision_approved(gate):
    """Test waiting for an approved decision."""
    tc = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "prod"},
        agent_id="agent-1",
    )
    escalation_id = await EscalationQueue.submit(tc, risk_score=80)

    # Approve after a short delay
    async def approve_later():
        await asyncio.sleep(0.1)
        await EscalationQueue.approve(escalation_id)

    asyncio.create_task(approve_later())
    result = await EscalationQueue.wait_for_decision(escalation_id, timeout_sec=1)

    assert result is True


@pytest.mark.asyncio
async def test_wait_for_decision_rejected(gate):
    """Test waiting for a rejected decision."""
    tc = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "prod"},
        agent_id="agent-1",
    )
    escalation_id = await EscalationQueue.submit(tc, risk_score=80)

    # Reject after a short delay
    async def reject_later():
        await asyncio.sleep(0.1)
        await EscalationQueue.reject(escalation_id)

    asyncio.create_task(reject_later())
    result = await EscalationQueue.wait_for_decision(escalation_id, timeout_sec=1)

    assert result is False


@pytest.mark.asyncio
async def test_wait_for_decision_timeout(gate):
    """Test timeout auto-rejects escalation."""
    tc = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "prod"},
        agent_id="agent-1",
    )
    escalation_id = await EscalationQueue.submit(tc, risk_score=80)

    # Don't approve or reject — let it timeout
    result = await EscalationQueue.wait_for_decision(escalation_id, timeout_sec=0.2)

    assert result is False
    entry = await EscalationQueue.get_by_id(escalation_id)
    assert entry["status"] == "rejected"


@pytest.mark.asyncio
async def test_escalation_recent(gate):
    """Test querying recent escalations."""
    tc1 = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "prod"},
        agent_id="agent-1",
    )
    tc2 = ToolCall(
        tool_name="dangerous_operation",
        args={"target": "staging"},
        agent_id="agent-2",
    )

    await EscalationQueue.submit(tc1, risk_score=80)
    await EscalationQueue.submit(tc2, risk_score=60)

    recent = await EscalationQueue.recent(limit=10)
    assert len(recent) == 2


# ============================================================================
# LangChain Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_guarded_tool_async_allowed(gate):
    """Test @guarded_tool decorator with async function (allowed)."""

    @guarded_tool(gateway=gate, agent_id="agent-1")
    async def my_async_tool(value: str) -> str:
        return f"result: {value}"

    # Should pass through (sync function, not in policy)
    result = await my_async_tool(value="test")
    assert result == "result: test"


@pytest.mark.asyncio
async def test_guarded_tool_async_blocked(gate):
    """Test @guarded_tool decorator with async function (blocked)."""

    @guarded_tool(gateway=gate, agent_id="agent-1")
    async def dangerous_operation(target: str) -> str:
        return f"deleted {target}"

    # This should be escalated and auto-rejected due to timeout
    # We need to mock the evaluation to make it block
    with patch.object(
        gate._policy_evaluator, "evaluate"
    ) as mock_evaluate:
        from agentgate.models import Effect
        from agentgate.policy import PolicyResult

        mock_evaluate.return_value = PolicyResult(
            effect=Effect.BLOCK,
            policy_name="test_policy",
            reason="Test block",
        )

        with pytest.raises(ToolException, match="AgentGate blocked"):
            await dangerous_operation(target="prod")


def test_guarded_tool_sync_allowed(gate):
    """Test @guarded_tool decorator with sync function (allowed)."""

    @guarded_tool(gateway=gate, agent_id="agent-1")
    def my_sync_tool(value: str) -> str:
        return f"result: {value}"

    result = my_sync_tool(value="test")
    assert result == "result: test"


def test_guarded_tool_sync_blocked(gate):
    """Test @guarded_tool decorator with sync function (blocked)."""

    @guarded_tool(gateway=gate, agent_id="agent-1")
    def dangerous_operation(target: str) -> str:
        return f"deleted {target}"

    with patch.object(
        gate._policy_evaluator, "evaluate"
    ) as mock_evaluate:
        from agentgate.models import Effect
        from agentgate.policy import PolicyResult

        mock_evaluate.return_value = PolicyResult(
            effect=Effect.BLOCK,
            policy_name="test_policy",
            reason="Test block",
        )

        with pytest.raises(ToolException, match="AgentGate blocked"):
            dangerous_operation(target="prod")


@pytest.mark.asyncio
async def test_guarded_tool_with_custom_context(gate):
    """Test passing custom context and agent_id."""

    @guarded_tool(
        gateway=gate,
        agent_id="special-agent",
        context={"role": "admin", "team": "security"},
    )
    async def restricted_tool(data: str) -> str:
        return f"processed: {data}"

    # Should go through without issues (not blocked by policy)
    result = await restricted_tool(data="sensitive")
    assert result == "processed: sensitive"


@pytest.mark.asyncio
async def test_guarded_tool_preserves_function_name(gate):
    """Test that decorator preserves original function name."""

    @guarded_tool(gateway=gate, agent_id="agent-1")
    async def my_special_tool(x: int) -> int:
        return x * 2

    assert my_special_tool.__name__ == "my_special_tool"
