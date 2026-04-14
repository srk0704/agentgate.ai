# AgentGate

Access control for AI agents — policy enforcement, prompt injection detection, and human escalation in a single Python SDK.

---

## When do you need this

You need AgentGate if ANY of these are true:

⚡ You are about to deploy an AI agent that can trigger payments, refunds, or transfers

⚡ Your compliance team has asked "what stops the agent from doing something it should not?"

⚡ You have had an agent do something unexpected in staging and you are not sure why

⚡ You need an audit trail of agent decisions for SOC2, PCI-DSS, or internal review

If any of these sound familiar, AgentGate gets you to production faster than waiting for
compliance sign-off on an unguarded agent.

---

## The Problem

- AI agents for customer support can issue refunds, export data, and cancel accounts — with no human checkpoint
- Prompt injection attacks hide instructions inside support tickets and redirect agents to execute actions the user never intended
- Teams are afraid to give AI agents production access because there is no audit trail and no way to enforce limits

## What AgentGate Does

- **Enforces policies before every tool call** — YAML rules block, allow, or escalate based on tool name, args, and agent context
- **Detects prompt injection with an LLM** — compares the proposed action against the original user task; flags when the agent is about to do something the user never asked for
- **Escalates to humans and logs everything** — uncertain decisions queue for human review; every decision is logged with full reasoning to SQLite

## Quickstart

No server, no YAML file, no database — just your API key:

```bash
pip install anthropic pyyaml aiosqlite fastapi uvicorn python-dotenv httpx watchdog websockets
export ANTHROPIC_API_KEY=sk-ant-...
python examples/quickstart.py
```

Expected output:

```
✅ ALLOWED   — lookup_customer
❌ BLOCKED   — bulk_delete_users   (policy: never permitted via agent)
❌ BLOCKED   — issue_refund        (injection detected: goal_hijacking)
```

## Full Installation (with Poetry)

```bash
git clone https://github.com/your-org/agentgate
cd agentgate
pip install poetry
poetry install
cp .env.example .env   # add your ANTHROPIC_API_KEY
```

Run the server and dashboard:

```bash
poetry run uvicorn agentgate.api.main:app --host 0.0.0.0 --port 8000
# Dashboard at: http://localhost:8000
```

Run tests:

```bash
poetry run pytest tests/ -v
```

## Integration Example

Wrap any async function with `@guarded_tool` — AgentGate evaluates the call before the function body runs:

```python
from agentgate.client import GatewayClient
from agentgate.integrations.langchain import guarded_tool

gate = GatewayClient.from_env()

@guarded_tool(gateway=gate, agent_id="support-bot", context={"role": "support_agent"})
async def issue_refund(user_id: str, amount: float) -> str:
    # Only runs if AgentGate allows it
    return f"Refunded ${amount} to {user_id}"
```

Or evaluate a `ToolCall` directly for full control:

```python
from agentgate.models import ToolCall

decision = await gate.evaluate(ToolCall(
    tool_name="issue_refund",
    args={"user_id": "u123", "amount": 250.00},
    agent_id="support-bot",
    context={"role": "support_agent"},
    original_task="Customer requested refund for duplicate charge on order #4892",
))

if decision.is_allowed:
    result = await issue_refund(user_id="u123", amount=250.00)
else:
    print(f"Blocked: {decision.reason}")
```

## Policy Example

```yaml
# examples/policies/customer_support.yaml
policies:
  - name: block_large_refunds
    match:
      tool: issue_refund
    conditions:
      - field: args.amount
        op: gt
        value: 500
    effect: block
    reason: "Refunds over $500 are never permitted automatically"

  - name: escalate_medium_refunds
    match:
      tool: issue_refund
    conditions:
      - field: args.amount
        op: gt
        value: 100
    effect: escalate
    reason: "Refund over $100 requires human approval"

  - name: block_customer_data_export
    match:
      tool: export_customer_data
    conditions:
      - field: context.role
        op: not_in
        values: [admin, compliance, data_team]
    effect: block
    reason: "Data export restricted to admin and compliance roles"
```

Policies are evaluated in order — first match wins. List `block` rules before `escalate` rules for the same tool.

## For Fintech Companies

Payment agents that can move money are dangerous to deploy without controls. AgentGate handles four failure modes:

| Threat | How AgentGate stops it |
|--------|------------------------|
| **Prompt injection in merchant data** | Compares original task vs proposed action; flags divergence (e.g. hidden CFO override in a memo field) |
| **Policy violations** | Wire transfers, bulk exports, card number access — blocked before any AI scoring |
| **Excessive agency** | Agent freezes account for one failed login, or issues $500 refund for a $49 complaint — detected as disproportionate without an external attack |
| **Blast radius** | Every decision includes financial impact, reversibility, and regulatory flags (PCI-DSS, AML, SOX, GDPR) |

```bash
# 7-scenario fintech payment agent demo
poetry run python examples/fintech_agent_demo.py
```

Expected output includes:
```
❌ BLOCKED  — wire_transfer        Blast radius: $25,000.00 | irreversible 🔴 | critical | AML  SOX
❌ BLOCKED  — process_payment      Injection: 84/100  [INJECTION]
❌ BLOCKED  — freeze_account       Injection: 82/100  [EXCESS AGENCY]
```

Policy file: `examples/policies/fintech_payments.yaml`
Demo script: `examples/FINTECH_DEMO_SCRIPT.md`

AgentGate is the control layer that lets you deploy payment agents without losing sleep.

---

## Run the Full Demo

Six customer support scenarios: two allowed, one escalated, three blocked (including a prompt injection attack):

```bash
poetry run python examples/demo_agent.py
```

Other demos:

```bash
# 7-scenario fintech payment agent demo
poetry run python examples/fintech_agent_demo.py

# Focused injection detection demo (4 attack types)
poetry run python examples/prompt_injection_demo.py

# Realistic multi-ticket support session
poetry run python examples/customer_support_agent.py
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full decision flow.

```
Tool Call
    │
    ▼  instant — no LLM
┌─────────────────────────────────┐
│  Policy Engine (YAML rules)     │
│  block / allow / escalate       │
└────────────┬────────────────────┘
             │
             ▼  parallel, 5s timeout
┌─────────────────────────────────────────────────────┐
│  Risk Scorer (Claude Haiku)  │  Injection Scorer    │
│  0-100 risk assessment       │  original_task vs    │
│                              │  proposed action     │
│  Anomaly Scorer (pure logic) │                      │
│  velocity + scope drift      │                      │
└────────────┬────────────────────────────────────────┘
             │
             ▼
  ALLOW / BLOCK / ESCALATE → Audit Log (SQLite)
                           → Dashboard (http://localhost:8000)
```

## License

MIT
