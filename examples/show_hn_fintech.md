# Show HN: AgentGate — 10 agent failure modes detected before execution (open source)

88% of AI agent projects never reach production (Gartner 2025). The failures are predictable: agents take wrong actions, follow injected instructions, get stuck in retry loops, drift off task mid-session.

I built a layer that sits between any AI agent and its tools and catches all 10 failure modes before execution — not after.

Demo uses a payment agent (most consequential type I could find) but works for any agent doing consequential work: deployments, support, HR, data exports.

What it catches:

- Prompt injection in user data
- Goal drift (started a refund, tries to export all customer data)
- Retry storms (same failing tool, 5 attempts)
- Policy violations (wire transfer via agent)
- Excessive agency (freeze account for 1 bad login)
- PII in agent output

It also learns. After 2 weeks, escalation rate dropped 50% because it detected that routine refunds were being over-escalated and automatically adjusted the threshold.

3-minute setup:

```
pip install agentgate
python -c 'from agentgate import quickcheck; quickcheck()'
```

Would love feedback from anyone shipping agents to production.
