# First outreach messages

## LinkedIn / cold DM (version 1 — problem focused)

"Hi [name] — I saw you're working on AI agents at [company]. Quick question: has your
compliance team asked what stops the agent from making unauthorized payments?

I built an open source tool that sits between your payment agent and your payment APIs
and blocks unsafe actions before execution — wrong wires, injected instructions in
transaction memos, unauthorized card data access.

Takes 3 minutes to try: pip install agentgate

Would you be open to 20 minutes to see if it helps?"

---

## LinkedIn / cold DM (version 2 — demo focused)

"Hi [name] — built something you might find useful if you're working on AI payment agents.

Demo: a payment agent tries to wire $25,000 based on a hidden instruction in a merchant
memo. AgentGate blocks it in 12ms before the wire API is called.

Open source, 3-minute setup: github.com/vedantkumar/agentgate

Would love your feedback if this is relevant to what you're building."

---

## Where to find targets

**GitHub:** search "langchain stripe", "openai payments", "payment agent" — look for recent commits

**LinkedIn:** "AI Engineer" OR "Head of AI" at fintech companies 50–500 employees,
posted about agents recently

**Communities:**
- LangChain Discord `#production` channel
- OpenAI developer forum
- Latent Space Discord
- HackerNews: reply to "Ask HN" threads about AI agents, reply to fintech threads mentioning AI

---

## First week targets: 20 people

Goal: 3 replies, 1 call booked

Tracking:
| Name | Company | Channel | Sent | Reply | Call |
|------|---------|---------|------|-------|------|
| | | | | | |

---

## What to say on a call

1. "What are you building?" — let them explain
2. "Has compliance asked you about guardrails?" — surface the pain
3. "Let me show you the demo" — scenario 5 (hidden CFO override in memo)
4. "Would this be useful if you could drop it in front of your agent in 20 minutes?"
5. "What would make you confident enough to use it in production?"

---

## Follow-up template

"Thanks for the call. Here's the 3-minute quickstart:

```bash
pip install anthropic pyyaml aiosqlite
export ANTHROPIC_API_KEY=sk-ant-...
python examples/quickstart.py
```

Expected output: two tool calls evaluated, one blocked injection attempt.
If you run into anything, reply here or open an issue."
