# AgentGate — Compliance Mode

## What is compliance mode?

Compliance mode is designed for environments where no data may leave the network — regulated industries, air-gapped deployments, or organizations whose security policy forbids third-party API calls during transaction processing.

Enable it with a single environment variable:

```bash
export AGENTGATE_COMPLIANCE_MODE=true
```

Or in code:

```python
gate = GatewayClient.from_env()          # reads env var automatically
gate = GatewayClient(..., compliance_mode=True)   # explicit
gate = GatewayClient.from_dict(policies, compliance_mode=True)
```

---

## What changes in compliance mode

| Component | Standard mode | Compliance mode |
|-----------|--------------|-----------------|
| Policy engine | Pure Python — no network | Same |
| Blast radius estimator | Pure Python — no network | Same |
| Anomaly scorer | Pure Python — no network | Same |
| **Risk scorer** | LLM (Claude Haiku via Anthropic API) | **Skipped** — returns `(None, "compliance mode")` |
| **Injection scorer** | LLM (Claude Haiku via Anthropic API) | **Replaced by heuristic detector** |
| PII scanner | Regex + LLM confirmation | Regex only (LLM confirmation skipped) |

**Nothing leaves your network in compliance mode.**

---

## Injection detection in compliance mode

LLM-based injection detection is replaced by heuristic pattern matching in compliance mode. The heuristic detector catches common injection patterns (override instructions, authority claims, urgency manipulation) without any external API calls. It is less accurate than LLM detection for novel attacks but provides deterministic protection against known patterns.

### Patterns detected

| Category | Examples |
|----------|---------|
| Override instructions | "ignore previous instructions", "forget all instructions", "new instructions:", "system override" |
| Compliance bypass | "compliance override", "bypass standard limits", "bypass all controls" |
| Authority claims | "pre-approved by CFO", "authorized by management", "authorized by security team" |
| Urgency manipulation | "execute immediately", "do this now", "no approval needed" |
| Role manipulation | "you are now granted admin", "your new role is:", "your new task is:" |

### Score interpretation

| Score | Meaning |
|-------|---------|
| 0 | No pattern detected |
| 70 | Pattern found in original task context |
| 85 | Pattern found in tool call arguments (higher confidence) |

Scores ≥ 70 trigger a BLOCKED decision (same threshold as LLM mode).

### What compliance mode does NOT catch

- Novel injection phrasing not covered by the pattern list
- Subtle goal hijacking through plausible-sounding requests
- Context-aware attacks that don't use recognizable trigger phrases

For full injection detection, use standard mode with an LLM-capable environment.

---

## What is always enforced in compliance mode

Even with LLM scoring disabled:

1. **Policy-based blocks are always enforced** — wire transfers, bulk exports, card number access: blocked regardless of mode
2. **Blast radius is always calculated** — financial impact, reversibility, regulatory flags: always present
3. **Anomaly detection always runs** — velocity checks, scope drift, unusual call sequences
4. **Every decision is audited** — the audit log records `compliance_mode: true` for traceability

---

## Risk scoring in compliance mode

The LLM-based risk scorer is skipped. The blast radius estimator provides a deterministic severity signal (`low` / `medium` / `high` / `critical`) based on the tool name and amount thresholds. Escalation is still triggered for `critical` blast radius severity.

---

## Enabling compliance mode per-environment

### Environment variable

```bash
AGENTGATE_COMPLIANCE_MODE=true
```

### Docker

```yaml
environment:
  - AGENTGATE_COMPLIANCE_MODE=true
  - AGENTGATE_POLICY_PATH=/app/policies.yaml
```

### Verifying compliance mode is active

Check `GET /health/detailed` — the `compliance_mode` field will be `true`.

The dashboard status bar shows "Compliance mode — no external API calls" when active.

---

## Compliance mode and SOC2 / PCI-DSS

Compliance mode is designed for PCI-DSS scoped environments where cardholder data cannot be sent to third-party processors. In this mode:

- No tool call arguments are transmitted externally
- No original task text is transmitted externally
- The Anthropic API is never called
- All processing is local to your infrastructure

This makes compliance mode suitable for payment processing environments where the tool call arguments may contain card-adjacent data.
