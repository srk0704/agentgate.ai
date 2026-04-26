# AgentGate Architecture

## Failure Modes Detected

Source: Amazon AWS Blog (2026), Arize AI (2026), OWASP LLM Top 10 (2025).
See [docs/THRESHOLD_RESEARCH.md](docs/THRESHOLD_RESEARCH.md) for the full
research basis behind every default threshold.

| Failure Mode | Detector | Method | Source |
|---|---|---|---|
| Prompt injection | InjectionScorer | LLM semantic + heuristic | OWASP LLM01 |
| Excessive agency | InjectionScorer | LLM disproportionate action | Amazon taxonomy |
| Policy violation | PolicyEngine | YAML rule match | Industry standard |
| High risk action | RiskScorer | LLM 0-100 score | Pan 2025 |
| Session anomaly | AnomalyScorer | Velocity + scope drift | Stripe rate limits |
| Context/goal drift | DriftDetector | Structural + semantic | Amazon taxonomy |
| Retry storm | LoopDetector | Repeated calls + failures | Nygard / Replit |
| Sequence loop | LoopDetector | Tool sequence repetition | Circuit breaker |
| PII in output | PiiDetector | Regex + LLM confirm | OWASP / PCI-DSS |
| Financial impact | BlastRadiusEstimator | Heuristic pre-execution | Payment industry |

## What AgentGate Does

- Intercepts every AI agent tool call before execution and evaluates it against policy rules, LLM-based risk scoring, prompt injection detection, and session anomaly detection
- Logs every decision with full reasoning (why it was allowed/blocked/escalated) to a tamper-evident audit trail
- Exposes a human escalation inbox via REST API and web dashboard so reviewers can approve or reject high-risk actions in real time

## What AgentGate Does NOT Do

- Does not execute tool calls itself — it is a gate, not an orchestrator; your agent code still runs the tools
- Does not provide agent orchestration, memory, or LLM inference — it plugs into whatever framework you already use (LangChain, raw Anthropic SDK, custom)
- Does not guarantee 100% injection detection — LLM-based scoring is probabilistic; use defense-in-depth alongside AgentGate

## Component Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  AI Agent (LangChain / custom / any framework)                    │
│                                                                    │
│  agent.run("process refund for customer")                          │
│       │                                                            │
│       ▼                                                            │
│  @gate.guarded  ─────────────────────────────────────────────►   │
│  issue_refund(user_id=..., amount=250)                             │
└───────────────────────────┬──────────────────────────────────────┘
                            │  ToolCall
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  GatewayClient.evaluate()                                         │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 1. PolicyEngine (YAML)                          ~0ms         │ │
│  │    • match tool + conditions                                  │ │
│  │    • effect: block → immediate BLOCKED                        │ │
│  │    • effect: allow → immediate ALLOWED (skip LLM)            │ │
│  │    • effect: escalate → go to step 3                         │ │
│  └──────────────────────────────┬──────────────────────────────┘ │
│                                 │ not blocked                      │
│  ┌──────────────────────────────▼──────────────────────────────┐ │
│  │ 2. Parallel Scoring                             ~100-2000ms  │ │
│  │                                                               │ │
│  │  RiskScorer          InjectionScorer     AnomalyScorer       │ │
│  │  (Claude Haiku)      (Claude Haiku)      (pure logic)        │ │
│  │  0-100 score         0-100 score         0-100 score         │ │
│  │  + reason            + attack_type       + reason            │ │
│  │                                                               │ │
│  │  asyncio.gather() — all three run in parallel                 │ │
│  │  5s timeout → fail open (configurable)                       │ │
│  └──────────────────────────────┬──────────────────────────────┘ │
│                                 │                                   │
│  ┌──────────────────────────────▼──────────────────────────────┐ │
│  │ 3. Decision Logic                                             │ │
│  │    injection ≥ 70  → BLOCKED                                  │ │
│  │    risk ≥ 80       → BLOCKED                                  │ │
│  │    anomaly ≥ 80    → BLOCKED                                  │ │
│  │    risk/anomaly ≥ 60 or policy=escalate → ESCALATED          │ │
│  │    else            → ALLOWED                                  │ │
│  └──────────────────────────────┬──────────────────────────────┘ │
│                                 │                                   │
│  ┌──────────────────────────────▼──────────────────────────────┐ │
│  │ 4. EscalationQueue (if escalated)            up to 60s       │ │
│  │    • Stored in SQLite                                         │ │
│  │    • Human approves/rejects via dashboard or API             │ │
│  │    • Auto-rejects after 60s timeout                          │ │
│  └──────────────────────────────┬──────────────────────────────┘ │
│                                 │                                   │
│  ┌──────────────────────────────▼──────────────────────────────┐ │
│  │ 5. AuditLogger                                                │ │
│  │    Every decision → SQLite audit_log table                    │ │
│  │    Captures: outcome, reason, all scores + reasons,          │ │
│  │    human_decision, human_reason, latency_ms                  │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                            │
                            ▼  Decision (ALLOWED / BLOCKED / ESCALATED)
                  Returns to agent code
```

## Data Flow

```
tool call → policy check → risk + injection + anomaly (parallel) → decision → audit log
```

All five stages run on every call. Policy runs first (synchronous, no I/O). Scoring runs in parallel with a shared timeout. Audit always runs last, even on failures.

## Integration Options

**SDK (recommended)** — import `GatewayClient` directly into your Python agent:
```python
gate = GatewayClient.from_env()
decision = await gate.evaluate(tool_call)
```

**Decorator** — wrap tool functions:
```python
@gate.guarded
async def issue_refund(user_id: str, amount: float): ...
```

**LangChain** — use the `@guarded_tool` decorator from `agentgate.integrations.langchain`

**HTTP API** — any language/framework can call the REST API for escalation management and audit queries:
- `GET /dashboard/stats` — live metrics
- `GET /audit` — paginated audit log with filters
- `GET /audit/export` — CSV download for compliance
- `POST /escalations/{id}/approve|reject` — human review
- `WS /ws/feed` — real-time decision stream

## Key Design Decisions

**Policy-first, LLM-second**: Policy engine runs synchronously before any LLM call. Explicit block/allow policies never touch the LLM — makes common cases fast and predictable.

**Fail-open by default**: If scoring times out or the LLM is unavailable, calls are allowed through (logged as `failed_open`). Change `AGENTGATE_FAIL_OPEN=false` to invert.

**Audit as training data**: Every decision stores not just the outcome but the *reason* — risk_reason, injection_reason, anomaly_reason, human_reason. This is designed to feed back into model fine-tuning (Phase 3 roadmap).

**Shared SQLite**: All components (audit, escalation, sessions) share one SQLite file with WAL mode for concurrent async writes. Swap for Postgres in production.
