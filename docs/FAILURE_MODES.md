# How AgentGate behaves when things go wrong

## Fail-open vs fail-closed

AgentGate defaults to **FAIL-OPEN**. This means:
if the gateway encounters an error (LLM timeout, database write failure, unexpected exception),
the tool call is **ALLOWED** to proceed rather than blocking your agent entirely.

**Why fail-open is the default:**
- Your payment agent stays operational even if AgentGate has an issue
- All failures are logged with `FAILED_OPEN` outcome
- You can monitor for elevated `FAILED_OPEN` rates

**To change to fail-closed:**

```bash
export AGENTGATE_FAIL_OPEN=false
```

Any AgentGate error will block the tool call. Use this for highest-security environments.

---

## What happens in each failure scenario

| Failure | Default behavior | Logged? | Audited? |
|---------|-----------------|---------|---------|
| LLM timeout (>5s) | Fail-open, heuristics only | Yes | Yes |
| LLM API error | Fail-open, heuristics only | Yes | Yes |
| DB write failure | Decision proceeds, log retried | Yes | Best effort |
| Policy file corrupt | Fail-open, no policies applied | Yes | Yes |
| AgentGate crash | Depends on `fail_open` setting | No | No |

---

## Latency and timeout behavior

All LLM calls have a **5-second timeout** (configurable via `AGENTGATE_TIMEOUT_MS`).

| Component | Latency | Can time out? |
|-----------|---------|---------------|
| Policy evaluation | < 5ms (synchronous) | Never |
| Blast radius | < 1ms (synchronous) | Never |
| Anomaly detection | < 5ms (synchronous) | Never |
| Risk scorer (LLM) | 100ms–5s | Yes — 5s timeout |
| Injection scorer (LLM) | 100ms–5s | Yes — 5s timeout |
| Injection scorer (heuristic) | < 1ms | Never |

If ALL LLM calls time out, AgentGate still returns a decision based on
**policy + blast radius + anomaly alone**.
Your agent is never left waiting indefinitely.

---

## What is always guaranteed

Even in failure scenarios:

1. **Policy-based blocks are ALWAYS enforced**
   Wire transfers stay blocked even if the LLM is down. Policies are pure Python — they never fail.

2. **Blast radius is ALWAYS calculated**
   Financial impact, reversibility, and regulatory flags are computed synchronously — no LLM.

3. **Every decision attempt is logged**
   Outcome may be `FAILED_OPEN` if something went wrong, but the attempt is always recorded.

4. **The `/health` endpoint reflects current status**
   Check `/health/detailed` for component-level health.

---

## Monitoring for failures

The `/health/detailed` endpoint exposes real-time component health:

```json
{
  "status": "ok",
  "components": {
    "policy_engine": "ok",
    "database": "ok",
    "llm_api": "ok",
    "compliance_mode": false
  },
  "decisions_today": 1847,
  "failed_open_today": 2
}
```

**Recommended alert:** if `failed_open_today / decisions_today > 0.01` (1%), investigate.

---

## Configuring timeout

```bash
# Default: 5000ms (5 seconds)
export AGENTGATE_TIMEOUT_MS=3000   # tighter timeout for latency-sensitive paths
export AGENTGATE_TIMEOUT_MS=10000  # looser timeout for high-reliability paths
```

---

## Production recommendations

```
[ ] /health monitored every 60 seconds
[ ] Alert on failed_open_today > 1% of decisions_today
[ ] AGENTGATE_FAIL_OPEN documented in your runbook
[ ] Team knows: policy blocks work even if LLM is down
[ ] Tested fail-closed mode in staging before enabling in production
```
