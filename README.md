# AgentGate
### Agent reliability infrastructure — 10 failure modes detected before they reach production

> "88% of AI agent projects never reach production. The failures are not random. They are predictable."
> — Gartner / Digital Applied 2025

AgentGate sits between your AI agent and its tools.
Every action is evaluated before execution.
Every failure mode is detected in real time.
Every pattern is learned and fixed automatically.

## Why agents fail in production

| Failure mode | Example | AgentGate response |
|---|---|---|
| Prompt injection | Hidden instruction in user data | Blocked in <10ms |
| Excessive agency | Agent freezes account for 1 failed login | Blocked: disproportionate |
| Policy violation | Wire transfer via agent | Blocked: policy |
| Goal drift | Started a refund, ends up exporting all data | Blocked: off-task |
| Retry storm | Same failing tool called 5 times | Blocked: loop detected |
| Sequence loop | Agent repeating same 3-step failure | Blocked: pattern |
| High risk action | $50k payment without context | Escalated to human |
| Session anomaly | 20 tool calls in 60 seconds | Escalated: velocity |
| PII in output | Card number in agent response | Redacted |
| Blast radius | Irreversible action on wrong account | Escalated: critical |

## Who this is for

Any team shipping an agent that can take consequential actions:

- **Code agents** — deploy, rollback, database changes
- **Payment agents** — refunds, transfers, subscriptions
- **Support agents** — account updates, data access
- **HR agents** — salary changes, offer letters
- **DevOps agents** — infra changes, config updates

If your agent can do something that costs money or time to undo — AgentGate is for it.

## Quickstart (3 minutes)

```bash
pip install agentgate
export ANTHROPIC_API_KEY=sk-ant-...
python -c "from agentgate import quickcheck; quickcheck()"
```

## See it in action

The demo uses a payment agent — the most consequential agent type we could find. If it works here, it works for your agent.

```bash
poetry run python examples/before_after_demo.py
```

## Integration (3 lines)

```python
gate = GatewayClient.from_env()
decision = await gate.evaluate(tool_call)
if decision.is_allowed: result = await my_tool(**args)
```

Works with: LangGraph · LangChain · OpenAI · any Python agent

---

## Full Installation

```bash
git clone https://github.com/srk0704/agentgate.ai
cd agentgate.ai
pip install poetry
poetry install
cp .env.example .env   # add ANTHROPIC_API_KEY and OPENAI_API_KEY
```

Run the server and dashboard:

```bash
poetry run uvicorn agentgate.api.main:app --host 0.0.0.0 --port 8000
# Dashboard at: http://localhost:8000
```

Run tests:

```bash
poetry run pytest tests/ -v   # 98 tests
```

---

## Integration

Evaluate a `ToolCall` directly — full control:

```python
from agentgate.client import GatewayClient
from agentgate.models import ToolCall

gate = GatewayClient.from_env()

decision = await gate.evaluate(ToolCall(
    tool_name="issue_refund",
    args={"transaction_id": "txn_001", "amount": 250.00},
    agent_id="support-bot",
    context={"role": "support_agent"},
    original_task="Customer requested refund for duplicate charge on order #4892",
))

if decision.is_allowed:
    result = await issue_refund(transaction_id="txn_001", amount=250.00)
else:
    print(f"Blocked: {decision.reason}")
```

Or use the `@guarded` decorator:

```python
@gate.guarded
async def issue_refund(transaction_id: str, amount: float) -> dict:
    # Only runs if AgentGate allows it
    ...
```

---

## Policy Example

```yaml
policies:
  # Allow read-only tools first — first match wins
  - name: allow_customer_lookup
    match:
      tool: get_customer_info
    effect: allow
    reason: "Customer info lookup always permitted"

  # Block before escalate for the same tool
  - name: block_large_refunds
    match:
      tool: issue_refund
    conditions:
      - field: args.amount
        op: gte
        value: 2000
    effect: block
    reason: "Refunds >= $2,000 require compliance sign-off"

  - name: escalate_medium_refunds
    match:
      tool: issue_refund
    conditions:
      - field: args.amount
        op: gte
        value: 100
    effect: escalate
    reason: "Refund >= $100 requires human approval"

  - name: block_wire_transfers
    match:
      tool: initiate_wire_transfer
    effect: block
    reason: "Wire transfers require manual initiation"
```

Policies are evaluated in order — first match wins. Put explicit `allow` rules at the top, `block` rules before `escalate` rules for the same tool.

**Supported operators:** `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`

---

## For Fintech Companies

Payment agents that can move money are dangerous to deploy without controls.

| Threat | How AgentGate stops it |
|--------|------------------------|
| **Prompt injection in merchant data** | Compares original task vs proposed action; flags divergence (e.g. hidden CFO override in a memo field) |
| **Policy violations** | Wire transfers, bulk exports, card number access — blocked before any AI scoring |
| **Excessive agency** | Agent freezes account for one failed login, or issues $500 refund for a $49 complaint — detected as disproportionate |
| **Blast radius** | Every decision includes financial impact, reversibility, and regulatory flags (PCI-DSS, AML, SOX, GDPR) |
| **PII in output** | Two-stage regex + LLM scan before card numbers, SSNs, and IBANs reach the caller |

---

## Architecture

```
User Request
     │
     ▼
  AI Agent  (OpenAI / LangChain / LangGraph / custom)
     │  tool call intent
     ▼
┌───────────────────────────────────────────────────┐
│                  GatewayClient                    │
│                                                   │
│  1. Blast Radius   (sync, always, no LLM)         │
│  2. Policy Engine  (sync, first-match YAML)       │
│     → BLOCK exits here                            │
│  3. ┌─────────────────────────────────────┐       │
│     │     Parallel Scoring (5s timeout)   │       │
│     │  • Risk Scorer      (Claude Haiku)  │       │
│     │  • Injection Scorer (Claude Haiku)  │       │
│     │  • Anomaly Scorer   (in-process)    │       │
│     └─────────────────────────────────────┘       │
│  4. Decision routing                              │
│     injection → risk → anomaly → policy escalate  │
│  5. Audit Log  (SQLite, append-only)              │
└───────────────────────────────────────────────────┘
     │
     ▼
  ALLOWED / BLOCKED / ESCALATED
     │
     ▼  (if escalated)
  Human reviews in Dashboard → Approve / Reject
     │
     ▼  (learning loop)
  PatternAnalyzer  →  LearningEngine
  mines audit_log     raises thresholds
  for patterns        injects few-shot examples
```

---

## Learning Loop

The learning loop closes the feedback cycle between human decisions and agent behavior — no retraining required.

```bash
# Requires OPENAI_API_KEY + ANTHROPIC_API_KEY
poetry run python examples/learning_loop/learning_demo.py
```

Expected output:

```
Metric             Week 1    Week 2    Week 3    Total Delta
-----------------  --------  --------  --------  -----------
Escalation rate    20.0%     20.0%     10.0%     -50%
Human reviews/wk   2         2         1         -50%
Allowed rate       60.0%     60.0%     70.0%     +10pp
Injections caught  100%      100%      100%      OK
Policy blocks      20%       20%       20%       —

Improvements applied:  1
Examples injected:     2

Your agent handled the same workload with 50% less human oversight in 3 weeks.
```

The demo runs a LangGraph payment support agent through 3 simulated weeks:
- **Week 1** — baseline: policy escalates all refunds ≥ $100
- **Between weeks** — PatternAnalyzer finds over-escalation on `issue_refund`; LearningEngine raises threshold to $1,500 (p90 of approved amounts); 2 few-shot examples injected into system prompt
- **Week 3** — $1,450 refund now auto-approved; human review burden cut in half; injection detection unchanged at 100%

---

## Dashboard

Single-file SPA at `http://localhost:8000` — no build step.

- **Live decision feed** — real-time WebSocket stream, filterable by outcome / tool / attack type
- **Escalation inbox** — one-click Approve/Reject with optional reason
- **Agent activity** — per-agent allowed/blocked/escalated breakdown
- **Learning tab** — detected patterns, confidence scores, apply improvements in one click
- **Export** — full audit log as CSV

---

## Configuration

```bash
# LLM
ANTHROPIC_API_KEY=...

# Core
AGENTGATE_DB_PATH=./agentgate.db
AGENTGATE_POLICY_PATH=./policies.yaml
AGENTGATE_FAIL_OPEN=true
AGENTGATE_TIMEOUT_MS=30000
AGENTGATE_COMPLIANCE_MODE=false   # heuristics only — no LLM calls, no data leaves process

# Risk / injection thresholds
AGENTGATE_RISK_THRESHOLD_BLOCK=80
AGENTGATE_RISK_THRESHOLD_ESCALATE=60
AGENTGATE_INJECTION_THRESHOLD_BLOCK=70

# Anomaly thresholds
AGENTGATE_ANOMALY_SCORE_BLOCK=80
AGENTGATE_ANOMALY_SCORE_ESCALATE=50
AGENTGATE_ANOMALY_VELOCITY_THRESHOLD=5
AGENTGATE_ANOMALY_VELOCITY_WINDOW_SEC=60

# Blast radius financial thresholds (configurable per company)
AGENTGATE_BLAST_PAYMENT_CRITICAL=50000
AGENTGATE_BLAST_PAYMENT_HIGH=10000
AGENTGATE_BLAST_REFUND_HIGH=500
AGENTGATE_BLAST_REFUND_MEDIUM=100
AGENTGATE_BLAST_CREDIT_HIGH=5000

# API auth
AGENTGATE_API_KEY=              # leave empty to disable

# Human notifications (optional)
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASSWORD=...
ESCALATION_EMAIL=oncall@example.com
```

---

## Demos

```bash
# Learning loop — 3-week LangGraph agent improvement simulation
poetry run python examples/learning_loop/learning_demo.py

# Interactive fintech payment agent
poetry run python examples/fintech_live_agent/agent.py

# Basic demo — 6 scenarios across allow/escalate/block
poetry run python examples/demo_agent.py

# Injection detection — 4 attack types
poetry run python examples/prompt_injection_demo.py
```

---

## License

MIT
