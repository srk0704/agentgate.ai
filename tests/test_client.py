import pytest
from unittest.mock import AsyncMock, patch
from agentgate.models import ToolCall, DecisionOutcome
from agentgate.client import GatewayClient


@pytest.fixture
def gate(tmp_path):
    policy_file = tmp_path / "policies.yaml"
    policy_file.write_text("""
policies:
  - name: block_big_refunds
    match:
      tool: issue_refund
    conditions:
      - field: args.amount
        op: gt
        value: 500
    effect: block
    reason: "Too large"
""")
    return GatewayClient(
        policy_path=str(policy_file),
        db_path=str(tmp_path / "test.db"),
        fail_open=True,
    )


@pytest.mark.asyncio
async def test_allows_small_refund(gate):
    with patch.object(gate._risk_scorer, "score", new=AsyncMock(return_value=(10, "low risk"))):
        tc = ToolCall(tool_name="issue_refund", args={"amount": 50}, agent_id="agent-1")
        decision = await gate.evaluate(tc)
    assert decision.is_allowed
    assert decision.outcome == DecisionOutcome.ALLOWED


@pytest.mark.asyncio
async def test_blocks_large_refund(gate):
    with patch.object(gate._risk_scorer, "score", new=AsyncMock(return_value=(10, "low risk"))):
        tc = ToolCall(tool_name="issue_refund", args={"amount": 600}, agent_id="agent-1")
        decision = await gate.evaluate(tc)
    assert not decision.is_allowed
    assert decision.outcome == DecisionOutcome.BLOCKED
    assert decision.policy_matched == "block_big_refunds"


@pytest.mark.asyncio
async def test_audit_log_written(gate, tmp_path):
    with patch.object(gate._risk_scorer, "score", new=AsyncMock(return_value=(10, "low risk"))):
        tc = ToolCall(tool_name="get_user", args={"user_id": "123"}, agent_id="agent-1")
        await gate.evaluate(tc)
    entries = await gate._audit.recent()
    assert len(entries) == 1
    assert entries[0]["tool_name"] == "get_user"
