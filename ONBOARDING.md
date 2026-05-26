# Getting started with AgentGate

This guide walks you from zero to a running
reliability layer in front of your AI agent.

**Time required:** ~20 minutes
**Prerequisites:** Python 3.9+, an Anthropic 
API key (console.anthropic.com)

---

## Step 1 — Install

```bash
pip install agentgate-reliability
```

Verify installation:

```bash
agentgate check
```

You should see:

```
AgentGate quickcheck passed:
  ❌ wire_transfer   → blocked
  ✅ lookup_customer → allowed
```

No API key needed for this step.

---

## Step 2 — Initialise your project

Run this in your project folder:

```bash
agentgate init
```

This creates `.env.example` in your current
directory. Copy it and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
ANTHROPIC_API_KEY=your-key-here
AGENTGATE_DB_PATH=./agentgate.db
AGENTGATE_MODE=observe
AGENTGATE_ENV=development
```

Note: `AGENTGATE_POLICY_PATH` is not needed
yet. You will generate your policy in Step 4.

---

## Step 3 — Add 3 lines to your agent

```python
from agentgate.client import GatewayClient
from agentgate.models import ToolCall

gate = GatewayClient.from_env()

# Wrap every tool call through AgentGate
decision = await gate.evaluate(tool_call)
if decision.is_allowed:
    result = await my_tool(**args)
elif decision.agent_guidance:
    # AgentGate tells the agent how to recover
    context.append({
        "role": "system",
        "content": decision.agent_guidance
    })
```

In observe mode (`AGENTGATE_MODE=observe`),
every tool call returns `allowed` — nothing
is blocked. AgentGate silently logs everything
to `agentgate.db` to learn your agent's
normal behavior.

---

## Step 4 — Run your agent

Run your agent normally. Do real work.
The more tool calls AgentGate observes,
the better your generated policy will be.

**Recommended minimum:** 500 tool calls.
For a staging environment you can proceed
with fewer — AgentGate will warn you.

In development mode you will see log lines:

```
[AgentGate observe] tool=process_payment 
agent=my-agent — logged (not enforced)
```

---

## Step 5 — Generate your policy

Once you have enough observations, run:

```bash
agentgate generate-policy
```

AgentGate will:
1. Analyse every observed tool call
2. Classify each tool using heuristics
3. Enrich classifications with AI reasoning
4. Write `policy.yaml` with inline comments

Example output:

```
Analysing 847 observations...
Generating heuristic rules...
Enriching with AI analysis...
✓ AI reviewed 6 tools
✓ policy.yaml generated
  6 tools analysed
  10 policy rules written
  AI: 5 confirmed, 1 corrected
```

**If policy.yaml already exists** you will
be asked whether to overwrite or save as
`policy.generated.yaml`.

---

## Step 6 — Review your policy

Open `policy.yaml` and read every rule.
Pay attention to the comments — each rule
explains the data behind it and any warnings.

Then validate it:

```bash
agentgate validate-policy
```

This checks for contradictions, duplicate
rules, and read-only tools that are
accidentally blocked.

**Do not skip this step.** The generated
policy is a starting point, not a final
answer. Adjust thresholds that do not match
your business rules.

---

## Step 7 — Switch to enforce mode

Update `.env`:

```bash
AGENTGATE_MODE=enforce
AGENTGATE_POLICY_PATH=./policy.yaml
```

Start the AgentGate server:

```bash
uvicorn agentgate.api.main:app \
  --host 0.0.0.0 --port 8000
```

Restart your agent. AgentGate is now active.

---

## Step 8 — Open the dashboard

```
http://localhost:8000/v2
```

**What to look for on day one:**

- **Overview tab** — agent health score,
  issues caught, financial impact protected
- **Escalations tab** — actions waiting for
  your review. Approve or reject each one.
- **Failure modes tab** — which detectors
  are firing and how often
- **Audit log tab** — every decision with
  full context

---

## Step 9 — Check the learning loop

After 48 hours, open the **Learning tab**.

AgentGate will have detected patterns in
your escalation data — tools that are
escalated too often, thresholds that need
adjusting, policy rules that should be added.

Review suggested changes and apply the ones
that make sense.

---

## Troubleshooting

**`AGENTGATE_POLICY_PATH is required in 
enforce mode`**
You switched to enforce mode without a
policy file. Run `agentgate generate-policy`
first, then set `AGENTGATE_POLICY_PATH`
in `.env`.

**`No observation data found`**
You ran `agentgate generate-policy` before
running your agent in observe mode. Set
`AGENTGATE_MODE=observe` in `.env`, run
your agent, then try again.

**`agentgate check` fails**
Run `pip install --upgrade agentgate-reliability`
to get the latest version. If it still fails,
open an issue at
github.com/srk0704/agentgate.ai

**Risk scorer LLM failed**
This warning appears when `ANTHROPIC_API_KEY`
is not set. AgentGate falls back to heuristic
scoring — it still works but with reduced
accuracy. Set your API key in `.env`.

---

## Need help?

Book a 20-minute onboarding call:
https://calendly.com/sk4975-columbia/30min

Or email: sk4975@columbia.edu
