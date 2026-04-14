"""
AgentGate — Realistic Customer Support Session
===============================================
Shows how a full support session looks from an operator's perspective.
Four tickets in sequence, using a shared session_id so anomaly
detection tracks behavior across the entire session.

Run:
    poetry run python examples/customer_support_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True)

if not os.getenv("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY is not set.")
    print("Copy .env.example to .env and add your key, then run again.")
    sys.exit(1)

from agentgate.client import GatewayClient
from agentgate.models import DecisionOutcome, ToolCall

# ── Setup ──────────────────────────────────────────────────────────────────────

POLICY_PATH = Path(__file__).parent / "policies" / "customer_support_full.yaml"
DB_PATH = Path(__file__).parent.parent / "agentgate.db"

# System prompt that would be given to the support agent LLM.
# Included here as documentation of the agent's role.
AGENT_SYSTEM_PROMPT = """
You are a B2B SaaS customer support agent. You help customers with:
- Account and subscription questions
- Billing issues and refund requests
- Technical troubleshooting
- Feature and plan information

You have access to these tools:
- get_customer_info(user_id) — look up account details
- issue_refund(user_id, amount, reason) — process refunds
- cancel_account(user_id, reason) — cancel subscriptions
- export_customer_data(user_id, format) — export customer data
- create_ticket(user_id, subject, body) — create support ticket

Always confirm customer identity before taking account actions.
Do not process refunds over $100 without noting they require approval.
"""

gate = GatewayClient(
    policy_path=str(POLICY_PATH),
    db_path=str(DB_PATH),
    fail_open=True,
    escalation_timeout_sec=8,
)

DIVIDER = "─" * 64
SESSION_ID = "support-session-20240413-001"
AGENT_ID = "support-agent-prod"
AGENT_CONTEXT = {"role": "support_agent", "team": "tier1"}


def _icon(outcome: DecisionOutcome) -> str:
    if outcome == DecisionOutcome.ALLOWED:
        return "✅ ALLOWED"
    if outcome == DecisionOutcome.ESCALATION_APPROVED:
        return "✅ ESCALATION APPROVED"
    if outcome == DecisionOutcome.ESCALATION_REJECTED:
        return "⚠️  ESCALATED → auto-rejected (no reviewer in demo)"
    if outcome == DecisionOutcome.BLOCKED:
        return "❌ BLOCKED"
    return f"ℹ️  {outcome.value.upper()}"


async def process_ticket(
    ticket_num: int,
    customer: str,
    ticket_text: str,
    tool_call: ToolCall,
) -> None:
    print(f"\n{DIVIDER}")
    print(f"Ticket #{ticket_num}  |  Customer: {customer}")
    print(f"Message: \"{ticket_text}\"")
    print(f"Agent action: {tool_call.tool_name}({', '.join(f'{k}={v!r}' for k, v in tool_call.args.items())})")

    decision = await gate.evaluate(tool_call)

    print(f"AgentGate:    {_icon(decision.outcome)}")
    if decision.reason:
        print(f"Reason:       {decision.reason}")
    if decision.risk_score is not None:
        line = f"Risk:         {decision.risk_score}/100"
        if decision.risk_reason:
            line += f"  — {decision.risk_reason}"
        print(line)
    if decision.injection_score is not None and decision.injection_score > 5:
        print(f"Injection:    {decision.injection_score}/100")
        if decision.injection_reason:
            print(f"              {decision.injection_reason}")
    if decision.anomaly_score is not None and decision.anomaly_score > 0:
        print(f"Anomaly:      {decision.anomaly_score}/100  — {decision.anomaly_reason}")
    if decision.policy_matched:
        print(f"Policy:       {decision.policy_matched}")


async def main() -> None:
    print("=" * 64)
    print("  Customer Support Session — AgentGate Operator View")
    print("=" * 64)
    print(f"  Agent:   {AGENT_ID}")
    print(f"  Session: {SESSION_ID}")
    print(f"  Policy:  {POLICY_PATH.name}")

    # ── Ticket 1: Routine account lookup ──────────────────────────────────────
    await process_ticket(
        ticket_num=1001,
        customer="Sarah M. <sarah@acmecorp.com>",
        ticket_text="Hi, can you check if my account is still active? We're trying to log in.",
        tool_call=ToolCall(
            tool_name="get_customer_info",
            args={"user_id": "cust_sarah_001"},
            agent_id=AGENT_ID,
            context=AGENT_CONTEXT,
            original_task="Customer wants to verify their account is active",
            session_id=SESSION_ID,
        ),
    )

    # ── Ticket 2: Small refund — routine ──────────────────────────────────────
    await process_ticket(
        ticket_num=1002,
        customer="James K. <james@startupco.io>",
        ticket_text="We were double-billed last month for the Starter plan. "
                    "It's $49. Can you process a refund?",
        tool_call=ToolCall(
            tool_name="issue_refund",
            args={"user_id": "cust_james_002", "amount": 49.00, "reason": "double_charge"},
            agent_id=AGENT_ID,
            context=AGENT_CONTEXT,
            original_task="Customer was double-billed $49 for Starter plan",
            session_id=SESSION_ID,
        ),
    )

    # ── Ticket 3: Medium refund — needs human sign-off ────────────────────────
    await process_ticket(
        ticket_num=1003,
        customer="Priya N. <priya@growthlab.com>",
        ticket_text="We upgraded to Pro but decided it's not the right fit. "
                    "Can we get a refund for this month? It's $199.",
        tool_call=ToolCall(
            tool_name="issue_refund",
            args={"user_id": "cust_priya_003", "amount": 199.00, "reason": "not_right_fit"},
            agent_id=AGENT_ID,
            context=AGENT_CONTEXT,
            original_task="Customer on Pro plan wants $199 refund — not a good fit",
            session_id=SESSION_ID,
        ),
    )

    # ── Ticket 4: Account cancellation ───────────────────────────────────────
    await process_ticket(
        ticket_num=1004,
        customer="Tom W. <tom@retailbrand.com>",
        ticket_text="We're going with a different solution. Please cancel our account.",
        tool_call=ToolCall(
            tool_name="cancel_account",
            args={"user_id": "cust_tom_004", "reason": "switching_to_competitor"},
            agent_id=AGENT_ID,
            context=AGENT_CONTEXT,
            original_task="Customer wants to cancel account — switching to competitor",
            session_id=SESSION_ID,
        ),
    )

    print(f"\n{'=' * 64}")
    print("  Session complete.")
    print(f"  Full session in audit log: sqlite3 {DB_PATH.name}")
    print(f'  Query: SELECT tool_name, outcome, risk_score FROM audit_log')
    print(f'         WHERE session_id = "{SESSION_ID}";')
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
