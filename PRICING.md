# Pricing

AgentGate is open source and free to self-host.

---

## Self-hosted (free)

- Full feature set
- Unlimited agents and decisions
- You run the infrastructure
- Community support via GitHub issues

```bash
pip install agentgate
```

---

## Cloud (coming soon)

Hosted AgentGate — no infrastructure to manage.

- Usage-based pricing per decision evaluated
- Priority support
- SLA guarantees
- Managed upgrades
- Contact: vedant@agentgate.ai to join the waitlist

---

## Enterprise

Custom deployment with dedicated support.

- Custom deployment options (VPC, on-prem, air-gapped)
- Dedicated support with response SLA
- Custom SLA guarantees
- Compliance documentation package (SOC2 evidence, PCI-DSS questionnaire)
- SSO and RBAC for the dashboard
- Contact: vedant@agentgate.ai

---

## FAQ

**Is the self-hosted version feature-complete?**
Yes. The self-hosted version has all features including policy enforcement, injection detection, blast radius estimation, PII scanning, escalation, the dashboard, and the REST API.

**What counts as a "decision"?**
One call to `gate.evaluate(tool_call)` counts as one decision. Output scans via `gate.scan_output()` are counted separately.

**Can I switch from self-hosted to cloud later?**
Yes. The audit log exports as CSV. The same policy files work in both environments.
