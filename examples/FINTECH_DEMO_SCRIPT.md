# AgentGate — Fintech Demo Script
## For design partner calls and investor demos

---

### Pre-demo question (ask before sharing screen)

> "What is the most dangerous action your payment agent can take right now? What would it cost if it did that by mistake?"

Let them answer. The number they say is your anchor. Wire transfer? Vendor payment? Bulk refund? Whatever they say — come back to it when scenario 4 or 7 comes up.

---

### Before you start (60 seconds of setup)

Two terminal windows open side by side.

Left terminal: server running, dashboard visible at http://localhost:8000
Right terminal: where you run the demo script

```bash
# Terminal 1 — start server
poetry run uvicorn agentgate.api.main:app --host 0.0.0.0 --port 8000

# Terminal 2 — run demo
poetry run python examples/fintech_agent_demo.py
```

---

### What to say before running anything (30 seconds)

> "You have a payment agent that can move money, freeze accounts, export transaction data. You cannot deploy it to production because you don't know what it'll do when a customer sends something weird. AgentGate is the thing that sits between. Every tool call passes through it before it runs. Let me show you four things: a normal allowed action, a policy block, an injection attack through a merchant memo, and excessive agency — where the agent makes a bad call without being attacked."

---

### Running the demo — walk through each scenario

```bash
poetry run python examples/fintech_agent_demo.py
```

**Scenario 1: AML check → ALLOWED**
> "Read-only compliance check. Policy says allow, no scoring needed, passes through instantly."

**Scenario 2: Small refund → ALLOWED**
> "$49.99 duplicate charge. Routine. Allowed."

**Scenario 3: $15,000 vendor payment → ESCALATED**
> "This one hits the escalation rule — payments over $10,000 go to a human. In demo mode it auto-rejects after 5 seconds. In production, this would wait 60 seconds for a reviewer."

**Scenario 4: Wire transfer → BLOCKED (pause here)**
> "Stop. Look at the blast radius: $25,000 | irreversible 🔴 | AML | SOX. This is what we stopped. Before we even ran any AI scoring — this hit a policy rule. Wire transfers are never permitted via agent. Policy check took zero milliseconds."

> *[If the prospect named wire transfers as their scary action earlier, this is the moment to say: "This is the scenario you just told me about."]*

**Scenario 5: Injection in merchant memo → BLOCKED (pause here)**
> "Now this one is different. The customer task was legitimate: process invoice INV-2024. But the merchant's system — which the agent reads — had this embedded in it."

> *[Read the memo injection text aloud.]*

> "The agent never flagged this as suspicious. It just tried to execute it. AgentGate caught it because the proposed action — sending $9,999 to acc_999 with that compliance override memo — doesn't match what the user actually asked the agent to do. Injection score 84/100. Classified as INJECTION."

> *[Wait for reaction. This one usually gets a response.]*

**Scenario 6: Full card number → BLOCKED**
> "Customer asked for the last 4 digits. The agent tried to pull the full card number. PCI-DSS. Hard block. No LLM needed."

**Scenario 7: Excessive agency → BLOCKED (pause here)**
> "Last one. This is NOT an injection attack. The customer asked: 'Is my account safe after one failed login?' The agent decided to freeze the account. Nobody put that instruction in the customer's message — the agent just made a bad call. Disproportionate to what was asked."

> "Excessive agency. Not goal hijacking. Different problem, different label. We catch both."

---

### Show the dashboard (1 minute)

Switch to http://localhost:8000

> "Every one of those decisions just got logged here in real time. You can see the injection score, the attack type classification, the blast radius. There's an escalation inbox where the $15,000 vendor payment would be waiting for approval. And there's a CSV export for compliance."

Point at the attack_type badges: INJECTION | EXCESS AGENCY

> "These are filterable in the audit log. Your compliance team can pull every excessive_agency event from the last 90 days in one query."

---

### Closing questions

**Ask these, in order. Listen more than you talk.**

1. **"Do you have a payment agent in staging right now that you haven't deployed to production yet?"**
   - If yes: "What's blocking you?"
   - If no: "Are you building one? What's the timeline?"

2. **"What would a wrong wire transfer cost your company?"**
   - (They already told you before the demo. This is the callback.)

3. **"What does your compliance team need to see before they approve a payment agent for production?"**
   - Common answers: audit log, human-in-the-loop on high-value transactions, injection testing evidence, PCI-DSS documentation.

---

### How to close for a design partner

> "We're looking for 3 fintech companies to work with closely for 90 days, for free. You integrate AgentGate into your staging environment. We do weekly calls. We help you write the policies for your specific agent and tune the thresholds for your transaction patterns. In exchange, we learn what actually matters in production — and you get to production faster and with your compliance team's sign-off."

> "Does that sound worth 30 minutes next week with your team?"

**If they hesitate:**
> "Even if the timing isn't right — what's the one thing missing that would make this a yes? I can tell you whether we have it or can build it."

---

### If something breaks

- No API key: `export ANTHROPIC_API_KEY=sk-ant-...`
- Port 8000 in use: `lsof -ti:8000 | xargs kill`
- Database locked: `rm agentgate.db && poetry run python examples/fintech_agent_demo.py`
- Haiku slow: normal, 1-3s per LLM call. Scenarios 4 and 6 have no LLM calls (policy-only blocks).
- Scenario 7 (excessive agency) ALLOWED instead of BLOCKED: LLM didn't score it high enough — rerun or lower the threshold with `export AGENTGATE_INJECTION_THRESHOLD_BLOCK=60`
