"""
AgentGate Benchmark
===================
Measures decision latency (p50 / p95 / p99) using pure policy evaluation
(no LLM required — all tools are blocked/allowed by policy).

Run:
    poetry run python scripts/benchmark.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import statistics
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agentgate.client import GatewayClient
from agentgate.models import ToolCall

N_WARMUP = 5
N_RUNS   = 100

POLICIES = [
    {
        "name": "block_wire_transfer",
        "match": {"tool": "wire_transfer"},
        "effect": "block",
        "reason": "Wire transfers not permitted via agent",
    },
    {
        "name": "allow_lookup",
        "match": {"tool": "lookup_customer"},
        "effect": "allow",
        "reason": "Read-only lookup always permitted",
    },
    {
        "name": "escalate_large_refund",
        "match": {"tool": "issue_refund"},
        "conditions": [{"field": "args.amount", "op": "gt", "value": 100}],
        "effect": "escalate",
        "reason": "Large refund requires human approval",
    },
]


async def run_benchmark() -> None:
    _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _tmp.close()

    gate = GatewayClient.from_dict(
        policies=POLICIES,
        db_path=_tmp.name,
        timeout_ms=5000,
        escalation_timeout_sec=0.1,
    )

    tool_calls = [
        ToolCall(
            tool_name="wire_transfer",
            args={"amount": 25000, "to_account": "acc_001"},
            agent_id="bench-agent",
        ),
        ToolCall(
            tool_name="lookup_customer",
            args={"user_id": "cust_001"},
            agent_id="bench-agent",
        ),
        ToolCall(
            tool_name="issue_refund",
            args={"amount": 49.99, "user_id": "cust_002"},
            agent_id="bench-agent",
        ),
    ]

    print(f"\nAgentGate Benchmark  (n={N_RUNS} + {N_WARMUP} warmup)")
    print("=" * 52)

    # Warmup
    print(f"Warming up ({N_WARMUP} runs)…")
    for i in range(N_WARMUP):
        tc = tool_calls[i % len(tool_calls)]
        await gate.evaluate(tc)

    # Timed runs
    latencies: list[float] = []
    by_tool: dict[str, list[float]] = {}

    for i in range(N_RUNS):
        tc = tool_calls[i % len(tool_calls)]
        t0 = time.perf_counter()
        decision = await gate.evaluate(tc)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        by_tool.setdefault(tc.tool_name, []).append(elapsed_ms)

    def pct(data: list[float], p: int) -> float:
        return sorted(data)[int(len(data) * p / 100)]

    print()
    print(f"  Overall  (n={N_RUNS})")
    print(f"  p50:  {pct(latencies, 50):.1f} ms")
    print(f"  p95:  {pct(latencies, 95):.1f} ms")
    print(f"  p99:  {pct(latencies, 99):.1f} ms")
    print(f"  mean: {statistics.mean(latencies):.1f} ms")
    print(f"  max:  {max(latencies):.1f} ms")
    print()
    print("  Per-tool breakdown:")
    for tool_name, times in sorted(by_tool.items()):
        print(f"  {tool_name:<30}  p50={pct(times,50):.1f}ms  p95={pct(times,95):.1f}ms")

    print()
    print("  Note: latency includes SQLite audit log write.")
    print("  In compliance mode (no LLM), latency is deterministic.")
    print("=" * 52)

    try:
        os.unlink(_tmp.name)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(run_benchmark())
