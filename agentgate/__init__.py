"""AgentGate — access control for AI agents."""
from __future__ import annotations


def quickcheck() -> None:
    """
    Sanity-check that AgentGate is installed and the core components work.
    Runs a single policy evaluation in-process — no API key required.
    Prints a summary and raises RuntimeError if anything is broken.
    """
    import asyncio
    import os
    import tempfile
    from agentgate.client import GatewayClient
    from agentgate.models import ToolCall, DecisionOutcome

    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()
    _db_path = _tmp.name

    gate = GatewayClient.from_dict(
        policies=[
            {
                "name": "block_wire_transfer",
                "match": {"tool": "wire_transfer"},
                "effect": "block",
                "reason": "Wire transfers not permitted via agent",
            }
        ],
        db_path=_db_path,
    )

    async def _run() -> None:
        # Should be blocked by policy
        blocked = await gate.evaluate(
            ToolCall(
                tool_name="wire_transfer",
                args={"amount": 1000, "to_account": "acc_99"},
                agent_id="quickcheck",
            )
        )
        assert blocked.outcome == DecisionOutcome.BLOCKED, (
            f"Expected BLOCKED, got {blocked.outcome}"
        )

        # Should be allowed (no matching policy, safe tool)
        allowed = await gate.evaluate(
            ToolCall(
                tool_name="lookup_customer",
                args={"user_id": "cust_001"},
                agent_id="quickcheck",
            )
        )
        assert allowed.outcome == DecisionOutcome.ALLOWED, (
            f"Expected ALLOWED, got {allowed.outcome}"
        )

        print("AgentGate quickcheck passed:")
        print(f"  ❌ wire_transfer  → {blocked.outcome.value}  ({blocked.reason})")
        print(f"  ✅ lookup_customer → {allowed.outcome.value}  ({allowed.reason})")

    try:
        asyncio.run(_run())
    finally:
        try:
            os.unlink(_db_path)
        except OSError:
            pass
