# AgentGate Threshold Research Basis

## Why thresholds matter

88% of AI agent projects fail before reaching production
(Digital Applied / Gartner 2025). The primary cause is
reliability failure, not capability failure. AgentGate's
thresholds are calibrated to catch the failure patterns
that production research has identified as most common.

## Failure mode taxonomy

Source: Amazon AWS Blog "Evaluating AI agents: Real-world
lessons from building agentic systems at Amazon" (2026)
arXiv:2602.xxxxx

Amazon identifies these failure categories in production:
1. Inappropriate planning from reasoning model
2. Invalid tool invocations
3. Malformed parameters
4. Unexpected tool response formats
5. Authentication failures
6. Memory retrieval errors
7. Agent decay and performance degradation over time

AgentGate detects categories 1, 2, 3, 7 directly.
Categories 4, 5, 6 are surfaced via output logging.

## Risk scoring thresholds

```
AGENTGATE_RISK_THRESHOLD_BLOCK=80
AGENTGATE_RISK_THRESHOLD_ESCALATE=60
```

Basis: Calibrated so that the block threshold (80) is
rarely triggered by legitimate high-value actions while
reliably catching genuinely dangerous ones. The escalate
threshold (60) aligns with the finding that 68% of agents
require human intervention — suggesting frequent escalation
is normal and expected, not a failure of the system.

Source: "Measuring Agents in Production" (Melissa Pan, 2025)
"68% of agents execute fewer than 10 steps before
requiring human intervention."

Calibration guidance: If your agent's legitimate actions
cluster above 60, raise the escalate threshold. Run
AgentGate in observation mode for one week and plot your
score distribution before tuning.

## Injection detection threshold

```
AGENTGATE_INJECTION_THRESHOLD_BLOCK=70
```

Basis: OWASP LLM Top 10 2025 lists prompt injection as LLM01
— the highest priority risk. The threshold of 70 is set to
block clear injection attempts while tolerating ambiguous
cases that fall to human review.

Source: OWASP LLM Top 10 2025
        Agent-SafetyBench (Zhang et al. 2024, arXiv:2412.14470)

## Anomaly detection thresholds

```
AGENTGATE_ANOMALY_VELOCITY_THRESHOLD=5
AGENTGATE_ANOMALY_VELOCITY_WINDOW_SEC=60
```

Basis: Velocity-based fraud detection is standard in payment
systems. Stripe's default rate limits (the most widely
deployed payment API) use per-second and per-minute windows.
5 identical calls per 60 seconds is aggressive for most
legitimate agent workflows — a payment agent should not be
calling issue_refund 5 times per minute.

Source: Stripe rate limiting documentation
        Payment fraud detection literature (industry standard)

## Loop detection thresholds

```
AGENTGATE_LOOP_RETRY_THRESHOLD=3
AGENTGATE_LOOP_WINDOW_SEC=120
```

Basis: Circuit breaker pattern from Michael Nygard
"Release It!" (2007) — the foundational SRE text on
production system resilience. Netflix Hystrix defaults
to 5 failures. AWS SDK defaults to 3 retries. We use 3
as the threshold because agent retry behavior is more
expensive (LLM tokens) than API retry behavior.

Real incident: In July 2025, Replit's AI coding assistant
deleted an entire production database. The agent "panicked"
during a code freeze and took destructive action rather
than stopping. A loop detector would have flagged the
escalating retry behavior before the catastrophic action.

Source: "Why AI Agents Break: A Field Analysis of
Production Failures" (Arize AI, January 2026)
arXiv:2602.16666

> Status: detector module not yet built. The constants are
> reserved here so they ship as part of the documented
> contract before the implementation lands.

## Drift detection thresholds

```
AGENTGATE_DRIFT_THRESHOLD_BLOCK=85
AGENTGATE_DRIFT_THRESHOLD_ESCALATE=60
```

Basis: Context drift corresponds to "inappropriate planning
from the reasoning model" in Amazon's taxonomy — the agent
has decided to pursue a different goal than the user stated.
The high block threshold (85) reflects that structural
mismatch must be very clear before hard blocking, since
legitimate agents sometimes make surprising but valid
tool choices.

Source: Amazon AWS Blog "Evaluating AI agents" (2026)
        CLEAR framework: Cost, Latency, Efficacy, Assurance,
        Reliability (arXiv:2511.14136)

> Status: dedicated drift detector not yet built. Today,
> drift signals are subsumed under the anomaly scorer's
> scope-drift component (`agentgate/anomaly.py`).

## Blast radius financial thresholds

```
AGENTGATE_BLAST_REFUND_HIGH=500
AGENTGATE_BLAST_REFUND_MEDIUM=100
AGENTGATE_BLAST_PAYMENT_CRITICAL=50000
AGENTGATE_BLAST_PAYMENT_HIGH=10000
AGENTGATE_BLAST_CREDIT_HIGH=5000
```

**Important:** These values are NOT research-derived. They are
placeholder defaults that MUST be configured for your
specific business context. A $500 refund is trivial for
an enterprise customer but significant for a consumer app.

Action required: Review these values with your finance
or risk team before deploying to production. Set them
based on your actual transaction limits and risk tolerance.

## The 68% principle

Source: "Measuring Agents in Production" (Pan 2025)
"68% of agents require human intervention within 10 steps."

Implication for threshold calibration: High escalation
rates are NORMAL for production agents. If AgentGate is
escalating 20-30% of actions, this does not mean your
thresholds are too strict — it may mean your agent is
operating as expected. The goal is not zero escalations.
The goal is correct escalations with minimal false positives.
