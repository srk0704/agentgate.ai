# Trust and Security

## What data does AgentGate see?

AgentGate sees:
- Tool names your agent calls
- Arguments to those tools
- The original user task (if you pass it)
- Context you provide (user role, session ID, approval tier)

AgentGate never sees:
- Your API keys or payment credentials
- Full card numbers (detected and blocked before logging)
- Passwords or authentication tokens

---

## What data leaves your environment?

**Standard mode:** tool name, args, and task are sent to the Anthropic API for scoring.
Anthropic's privacy policy applies. No payment credentials are transmitted.

**Compliance mode:** nothing leaves your environment.
Set `AGENTGATE_COMPLIANCE_MODE=true`.

**Self-hosted:** you control everything. Run the Docker container on your own infrastructure.

---

## Audit trail integrity

The `audit_log` table uses append-only writes.
No `UPDATE` or `DELETE` statements are ever executed on this table.
Every decision is permanent.

You can verify this by inspecting the source:

```bash
grep -n "UPDATE\|DELETE" agentgate/audit.py
# Should return no results on the audit_log table
```

---

## PII protection

Before any tool call arguments are sent to the Anthropic API for scoring, the PII
detector checks for credit card numbers, SSNs, IBANs, and routing numbers.

If PII is detected in tool call arguments, AgentGate:
1. Blocks the scoring call
2. Returns BLOCKED with reason `pii_in_args`
3. Logs the detection without logging the PII itself

The full card number or SSN is never written to the audit log.

---

## Responsible disclosure

Found a security issue?

Email: sk4975@columbia.edu
We will respond within 24 hours.

Please do not open a public GitHub issue for security vulnerabilities.
Use GitHub's private security advisory feature or email directly.

---

## Open source

AgentGate is MIT licensed. You can read every line of code that runs on your data:

- `agentgate/policy.py` — policy evaluation (no network calls)
- `agentgate/blast_radius.py` — blast radius estimation (no network calls)
- `agentgate/injection.py` — injection detection (LLM in standard mode, heuristic in compliance mode)
- `agentgate/audit.py` — audit logging (SQLite, append-only)
- `agentgate/pii_detector.py` — PII detection (regex + optional LLM)
