"""AgentGate — access control for AI agents."""
from __future__ import annotations

__version__ = "0.8.4"


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
            },
            # PolicyEvaluator defaults to BLOCK on no-match (security-by-
            # default, since commit 99a5560). Without an explicit allow for
            # lookup_customer the second assertion below would fail with
            # "Expected ALLOWED, got blocked". Allow rule placed *after* the
            # block rule — evaluator returns on the first match, so the
            # block still takes precedence for wire_transfer.
            {
                "name": "allow_lookup_customer",
                "match": {"tool": "lookup_customer"},
                "effect": "allow",
                "reason": "Read-only lookup permitted by quickcheck",
            },
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

        print("AgentGate is installed and working.")

    import logging
    # Suppress internal log noise during
    # quickcheck so users only see the
    # clean summary output
    logging.getLogger("agentgate").setLevel(
        logging.ERROR
    )
    try:
        asyncio.run(_run())
    finally:
        logging.getLogger("agentgate").setLevel(
            logging.WARNING
        )
        try:
            os.unlink(_db_path)
        except OSError:
            pass
