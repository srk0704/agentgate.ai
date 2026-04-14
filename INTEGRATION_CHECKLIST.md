# AgentGate Integration Checklist

Use this checklist before going to production with AgentGate protecting your agent.

---

## Setup

- [ ] AgentGate installed (`pip install agentgate` or via Poetry)
- [ ] `ANTHROPIC_API_KEY` set in environment
- [ ] Policy file created and reviewed (`policies.yaml`)
- [ ] Database path set (`AGENTGATE_DB_PATH`) — not the default `./agentgate.db` in production
- [ ] `GatewayClient.from_env()` used (not hardcoded credentials)

---

## Policies

- [ ] At least one `block` rule defined for your highest-risk tool (e.g. wire transfers)
- [ ] `block` rules listed before `escalate` rules for the same tool (first-match wins)
- [ ] Policy file has been tested against a representative set of tool calls
- [ ] Policy file is version-controlled
- [ ] Policy changes go through code review before deployment

---

## Injection detection

- [ ] `original_task` is set on every `ToolCall` passed to `evaluate()`
  Without this, injection detection is skipped entirely.
- [ ] Injection threshold reviewed — default is 70/100 (env: `AGENTGATE_INJECTION_THRESHOLD_BLOCK`)
- [ ] At least one injection test scenario in your test suite

---

## Escalation

- [ ] Escalation notifications configured (Slack or email webhook)
- [ ] `escalation_timeout_sec` set appropriately for your team's response time
- [ ] Escalation fallback behavior tested: what happens if no human responds?
- [ ] On-call runbook includes AgentGate escalation handling

---

## Reliability

- [ ] `/health` endpoint monitored (suggest: every 60 seconds)
- [ ] `/health/detailed` checked before going live
- [ ] `AGENTGATE_FAIL_OPEN` setting documented and accepted
- [ ] Team knows: policy blocks work even if the LLM is down
- [ ] Alert configured if `FAILED_OPEN` rate exceeds 1%
- [ ] `fail_open=False` considered for highest-security environments

---

## Compliance mode (optional)

- [ ] `AGENTGATE_COMPLIANCE_MODE=true` set if no data may leave the network
- [ ] Team understands: compliance mode uses heuristic injection detection (less accurate)
- [ ] Compliance mode tested against your injection scenarios before enabling in production
- [ ] `/health/detailed` confirms `compliance_mode: true` in response

---

## Audit and logging

- [ ] Audit log database is backed up or on durable storage
- [ ] `/audit/export` tested — CSV export works
- [ ] Audit log retention policy defined
- [ ] Team knows audit log is append-only (no UPDATEs or DELETEs)
- [ ] PII scanning enabled for any tools that return customer data

---

## Security

- [ ] `AGENTGATE_API_KEY` set if using the REST API from external systems
- [ ] Dashboard not exposed to the public internet (it requires no auth by default)
- [ ] Database file permissions restricted (readable only by the application user)
- [ ] API key rotated before production launch

---

## Performance

- [ ] Latency measured end-to-end in staging — acceptable for your use case
- [ ] `AGENTGATE_TIMEOUT_MS` set appropriately (default 5000ms)
- [ ] Tested behavior when LLM times out (confirm fail-open/closed behavior is correct)

---

## Go/no-go

Before going to production, all items in **Setup**, **Policies**, **Injection detection**,
and **Reliability** must be checked. The other sections are strongly recommended but can
be deferred with documented acceptance of the risk.
