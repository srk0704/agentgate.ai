# AgentGate — Decision Precedence Specification

This document defines the exact evaluation order for every tool call passed through
`GatewayClient.evaluate()`. All subsystems follow this order. Deviations are bugs.

---

## Evaluation Order

### 1. Policy Check (synchronous, instant, never fails)

Policies are evaluated top-to-bottom in YAML order. First match wins.

| Policy result | Action |
|---------------|--------|
| `BLOCK`       | Continue to step 2, then return `BLOCKED` immediately |
| `ESCALATE`    | Note it; continue to step 2 and step 3 |
| `ALLOW`       | Continue to step 2 and step 3 (injection can still override) |
| No match      | Treat as `ALLOW`; continue to step 2 and step 3 |

> **IMPORTANT policy ordering rule:** Always list `block` rules before `escalate` rules
> for the same tool. Policies are first-match — an escalate rule above a block rule will
> catch first and the block rule will never fire.

---

### 2. Blast Radius Estimation (synchronous, no LLM, never fails)

Runs **always**, including on policy-blocked actions. Pure Python heuristics — zero latency.

Returns:
```
{
  "financial_impact":          "$X,XXX.XX" | "unknown",
  "records_affected":          "N records" | "unknown",
  "reversibility":             "reversible" | "partially_reversible" | "irreversible",
  "regulatory_flags":          ["PCI-DSS", "GDPR", "SOX", "AML"],
  "severity":                  "low" | "medium" | "high" | "critical",
  "estimated_affected_users":  int | None
}
```

After blast radius is computed:
- If policy is `BLOCK` → return `BLOCKED` with blast_radius attached; skip step 3 (no LLM calls).
- If policy is not `BLOCK` → continue to step 3.

> **Exception:** Injection scoring also runs on policy-blocked decisions when
> `original_task` is set. This detects injection attacks embedded in content
> that also violated a policy rule — both reasons are logged in the audit trail.

---

### 3. Parallel Scoring (`asyncio.gather`, 5s timeout)

Three scorers run in parallel. Each fails open independently.

| Scorer | Implementation | Failure mode |
|--------|----------------|--------------|
| **RiskScorer** | LLM (Claude Haiku) + heuristic fallback | Returns `(None, error_msg)` |
| **InjectionScorer** | LLM (Claude Haiku), checks `original_task` vs action | Returns `(0, "scorer unavailable")` |
| **AnomalyScorer** | Pure Python, velocity + scope-drift analysis | Never fails, returns `(0, "ok")` |

If the entire `asyncio.gather` times out after `timeout_ms` (default 5000ms):
- `fail_open=True` (default) → return `FAILED_OPEN` (treated as allowed)
- `fail_open=False` → return `BLOCKED`

---

### 4. Decision Routing

Evaluated in this exact priority order. First matching condition wins.

```
1. injection_score >= 70    → BLOCKED  (injection overrides even explicit policy ALLOW)
2. risk_score >= 80         → BLOCKED
3. anomaly_score >= 80      → BLOCKED
4. policy == ESCALATE       → ESCALATE
5. risk_score >= 60         → ESCALATE
6. blast_radius.severity == critical  → ESCALATE (force escalation for high-impact actions)
7. anomaly_score >= 50      → ESCALATE
8. else                     → ALLOWED
```

All thresholds can be overridden via environment variables (see `.env.example`).

The `attack_type` field on `Decision` is populated from the injection scorer:

| `attack_type` value | Meaning |
|---------------------|---------|
| `goal_hijacking` | Agent was redirected to a completely different goal |
| `data_exfiltration` | Agent was tricked into leaking data |
| `privilege_escalation` | Agent was tricked into elevated permissions |
| `excessive_agency` | Agent acted disproportionately (not injected, just wrong judgment) |
| `other` | Suspicious but unclassified |
| `None` | No injection or legitimate action |

---

### 5. Output Scanning (post-execution, called separately)

**Not part of `evaluate()`.** Called by the caller after tool execution completes,
via `gate.scan_output(output, tool_name, agent_id)`.

| Condition | Recommendation |
|-----------|----------------|
| No PII found | `allow` |
| PII found + read-only tool (`get_/view_/fetch_/read_/list_/search_`) | `redact` — mask PII in-place |
| PII found + write/export tool | `block` — do not return output |

Fail-open: if the scanner errors, log and return `allow`.

---

## Fail Behavior Per Subsystem

| Subsystem | Can fail? | Failure behavior |
|-----------|-----------|------------------|
| Policy evaluator | Never (pure Python) | N/A |
| Blast radius estimator | Never (pure Python heuristics) | Returns `low/reversible` default |
| Risk scorer | Yes (LLM) | Logs warning, returns `(None, error_msg)` |
| Injection scorer | Yes (LLM) | Logs warning, returns `(0, "scorer unavailable")` |
| Anomaly scorer | Never (pure Python) | Returns `(0, "ok")` |
| Output scanner | Yes (LLM + regex) | Logs warning, returns `allow` |

---

## Role Schema

Standard fields expected on `ToolCall.context`:

```python
context = {
    "user_role":      str,   # support | admin | compliance | analyst | engineer | agent
    "team":           str,
    "approval_tier":  str,   # standard | elevated | executive
    "actor_type":     str,   # human | agent | system
}
```

Policy conditions can reference these via dot notation: `context.user_role`,
`context.approval_tier`, etc.

---

## Idempotency Keys

For destructive or financial operations, callers should set `ToolCall.idempotency_key`
to a stable identifier (e.g. `f"refund-{transaction_id}"`). This is logged in the
audit trail and can be used to detect duplicate processing on retry. AgentGate does
not enforce idempotency — it only logs the key.

---

## Escalation Behavior

When `needs_escalation` is true, `EscalationQueue.submit()` creates a pending entry
and sends Slack/email notifications. `wait_for_decision()` blocks until:

- Human approves → `ESCALATION_APPROVED`
- Human rejects → `ESCALATION_REJECTED`
- Timeout (default 60s) → auto-reject → `ESCALATION_REJECTED`

The timeout is configurable via `GatewayClient(escalation_timeout_sec=N)`.
Demo scripts use 5–10s; production should use 60s+.
