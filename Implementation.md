# AgentGate Implementation Guide

Complete documentation for AgentGate: Access control for AI agents with policy enforcement, risk scoring, anomaly detection, blast radius estimation, PII output scanning, escalation, dashboard, and Docker deployment.

---

## Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Core Modules](#core-modules)
5. [Blast Radius Estimation](#blast-radius-estimation)
6. [PII Output Scanning](#pii-output-scanning)
7. [Session & Anomaly Detection](#session--anomaly-detection)
8. [Escalation System](#escalation-system)
9. [LangChain Integration](#langchain-integration)
10. [Dashboard](#dashboard)
11. [API Endpoints](#api-endpoints)
12. [Setup & Configuration](#setup--configuration)
13. [Docker Deployment](#docker-deployment)
14. [Usage Examples](#usage-examples)
15. [Test Coverage](#test-coverage)

---

## Overview

**AgentGate** is a gateway for controlling AI agent tool execution. It enforces:
- **Policy-based rules** (YAML-configured, hot-reload via watchdog)
- **Risk scoring** (LLM-based assessment)
- **Injection detection** (LLM-based for standard mode; deterministic heuristic in compliance mode)
- **Compliance mode** (zero network calls — heuristic injection + blast radius + anomaly only)
- **Blast radius estimation** (financial impact, reversibility, regulatory flags — synchronous, always runs)
- **Session anomaly detection** (velocity + scope-drift, pure Python)
- **PII output scanning** (regex + LLM confirmation, post-execution)
- **Human escalation** (async queue, auto-timeout)
- **Audit logging** (SQLite, CSV export)
- **Real-time dashboard** (single-file HTML, WebSocket live feed, attack badges, blast radius chips)
- **API key auth** (optional, header-based)
- **Docker-ready** (Dockerfile + docker-compose.yml)

The gateway sits between your LLM agent and its tools, evaluates every tool call, and either allows, blocks, or escalates it.

---

## Architecture

### Decision Flow

```
Tool Call
    ↓
┌─────────────────────────────┐
│  GatewayClient.evaluate()   │
└─────────────────────────────┘
    ↓
┌─────────────────────────────┐
│  Step 0: Blast Radius        │ (synchronous, instant — always runs, even on policy block)
│  (BlastRadiusEstimator)      │ Heuristic: tool name + args → financial_impact, reversibility,
└─────────────────────────────┘ severity, regulatory_flags, estimated_affected_users
    ↓
┌─────────────────────────────┐
│  Step 1: Policy Check        │ (synchronous, instant — no timeout)
│  (PolicyEvaluator)           │ Policies evaluated in order; first match wins.
└─────────────────────────────┘ IMPORTANT: list BLOCK rules before ESCALATE rules.
    ↓
  BLOCK? → _run_injection_only() ← also runs injection on policy-blocked calls
         → Decision(BLOCKED, blast_radius=...)   ← returns with blast_radius attached
    ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 2: Parallel Scoring (asyncio.gather, 5s timeout)      │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │  RiskScorer  │  │ InjectionScorer │  │ AnomalyScorer │  │
│  │  (LLM call)  │  │  (LLM call)     │  │  (pure Python)│  │
│  └──────────────┘  └─────────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
    ↓
  injection >= INJECTION_BLOCK (70) → BLOCKED (attack_type extracted from reason)
    ↓
  risk >= BLOCK (80) OR anomaly >= ANOMALY_BLOCK (80) → BLOCKED
    ↓
  policy.effect == ESCALATE
  OR risk >= ESCALATE (60)
  OR blast_radius.severity == "critical"
  OR anomaly >= ANOMALY_ESCALATE (50) → EscalationQueue.submit()
    ↓
  Decision(ALLOWED, blast_radius=..., attack_type=...)
    ↓
┌─────────────────────────────┐
│   Audit Log (AuditLogger)   │ (all decisions logged, incl. blast_radius, attack_type)
└─────────────────────────────┘
    ↓
  broadcast_decision() → WebSocket feed → Dashboard
```

### Components

| Component | Responsibility | Backed By |
|-----------|----------------|-----------|
| **GatewayClient** | Orchestrates blast_radius → policy → scoring → decision | In-memory |
| **BlastRadiusEstimator** | Financial impact + reversibility + regulatory flags (synchronous) | Pure Python heuristic |
| **PolicyEvaluator** | Deterministic policy matching (YAML) | YAML file, watchdog hot-reload |
| **RiskScorer** | Probabilistic risk assessment | Anthropic API, in-process cache; heuristic-only in compliance mode |
| **InjectionScorer** | Prompt injection + excessive agency detection | Anthropic API (standard); HeuristicInjectionDetector (compliance mode) |
| **HeuristicInjectionDetector** | Rule-based injection detection — no LLM | Pure Python regex (15 patterns) |
| **AnomalyScorer** | Session velocity + scope-drift detection | SessionTracker (SQLite) |
| **SessionTracker** | Per-agent call history | SQLite (session_calls table) |
| **PiiDetector** | PII scanning in agent output (regex + LLM confirm) | Regex + Anthropic API |
| **AuditLogger** | Immutable audit trail + stats + export + PII scan log | SQLite (WAL mode) |
| **EscalationQueue** | Human-in-the-loop decisions | SQLite, async event loop |
| **FastAPI** | REST API + WebSocket + static dashboard | HTTP / WS |

---

## Project Structure

```
agentGate.ai/
├── agentgate/                           # Main package
│   ├── __init__.py                      # quickcheck() sanity-check function
│   ├── models.py                        # Data models (ToolCall, Decision, etc.)
│   ├── client.py                        # GatewayClient (core orchestrator)
│   ├── policy.py                        # PolicyLoader, PolicyEvaluator, hot-reload
│   ├── risk.py                          # RiskScorer (LLM + heuristic; compliance_mode aware)
│   ├── injection.py                     # InjectionScorer (LLM or heuristic; compliance_mode aware)
│   ├── heuristic_injection.py           # HeuristicInjectionDetector (regex, no LLM)
│   ├── blast_radius.py                  # BlastRadiusEstimator (synchronous, no LLM)
│   ├── pii_detector.py                  # PiiDetector (regex + LLM confirm, redact)
│   ├── session.py                       # SessionTracker (call history)
│   ├── anomaly.py                       # AnomalyScorer (velocity + scope drift)
│   ├── audit.py                         # AuditLogger (SQLite, stats, CSV, PII scan log, usage counts)
│   ├── escalation.py                    # EscalationQueue (human approval)
│   ├── api/
│   │   ├── __init__.py
│   │   └── main.py                      # FastAPI: all endpoints + WebSocket
│   ├── dashboard/
│   │   └── index.html                   # Single-file dashboard (status bar, attack badges, blast chips)
│   └── integrations/
│       ├── __init__.py
│       └── langchain.py                 # @guarded_tool decorator
├── docs/
│   ├── DECISION_PRECEDENCE.md           # Exact evaluation order, fail behaviors
│   ├── COMPLIANCE.md                    # Compliance mode: heuristic injection, no-LLM operation
│   └── FAILURE_MODES.md                 # Fail-open/closed behavior, latency guarantees
├── examples/
│   ├── demo_agent.py                    # Customer support demo: 6 scenarios
│   ├── fintech_agent_demo.py            # Fintech payment agent demo: 7 scenarios (compliance_mode aware)
│   ├── before_after_demo.py             # Side-by-side unguarded vs protected agent
│   ├── prompt_injection_demo.py         # Focused injection demo: 4 attack types
│   ├── customer_support_agent.py        # Realistic multi-ticket support session
│   ├── quickstart.py                    # Minimal 3-scenario quickstart
│   ├── demo_injection.py                # Standalone injection attack demo
│   ├── show_hn_draft.md                 # Show HN submission draft (original)
│   ├── show_hn_fintech.md               # Show HN submission draft (fintech version)
│   ├── DEMO_SCRIPT.md                   # Narrated demo script (customer support)
│   ├── FINTECH_DEMO_SCRIPT.md           # Narrated demo script for investor/partner calls
│   └── policies/
│       ├── customer_support.yaml        # Customer support policy
│       ├── fintech_payments.yaml        # Fintech: 15-rule payment agent policy
│       ├── fintech.yaml                 # Fintech: transaction limits, compliance
│       └── healthcare.yaml             # Healthcare: PHI access, prescription rules
├── scripts/
│   └── benchmark.py                     # p50/p95/p99 latency measurement (100 runs)
├── tests/
│   ├── __init__.py
│   ├── test_client.py                   # Core gateway tests (3 tests)
│   ├── test_escalation.py               # Escalation + LangChain tests (13 tests)
│   ├── test_anomaly.py                  # Anomaly detection tests (6 tests)
│   ├── test_blast_radius.py             # Blast radius estimation tests (20 tests)
│   ├── test_injection.py                # Injection + excessive_agency tests (10 tests)
│   ├── test_heuristic_injection.py      # Heuristic detector + compliance mode tests (17 tests)
│   ├── test_pii.py                      # PII detection + redaction tests (17 tests)
│   └── test_integration.py              # Cross-system integration tests (12 tests)
├── pyproject.toml                       # Poetry configuration
├── .env.example                         # Environment variables template
├── Dockerfile                           # Container build
├── docker-compose.yml                   # Full stack deployment
├── README.md                            # Public-facing readme (urgency hook section)
├── ARCHITECTURE.md                      # Deep architecture doc
├── INTEGRATION_CHECKLIST.md             # Pre-production checklist (setup → go/no-go)
├── PRICING.md                           # Self-hosted / Cloud / Enterprise tiers
├── TRUST.md                             # Data handling, audit integrity, disclosure
├── OUTREACH.md                          # Cold DM templates, target-finding guide
└── Implementation.md                    # This file
```

---

## Core Modules

### 1. models.py — Data Models

Defines all data structures passed through the gateway.

```python
class Effect(Enum):
    ALLOW = "allow"           # Policy effect: allow tool
    BLOCK = "block"           # Policy effect: block tool
    ESCALATE = "escalate"     # Policy effect: escalate to human

class DecisionOutcome(Enum):
    ALLOWED = "allowed"                 # Gateway allowed execution
    BLOCKED = "blocked"                 # Gateway blocked execution
    ESCALATED = "escalated"             # User-initiated escalation
    ESCALATION_APPROVED = "approval"    # Human approved escalation
    ESCALATION_REJECTED = "rejection"   # Human rejected escalation
    FAILED_OPEN = "failed_open"         # Gateway error (fail-safe)

@dataclass
class ToolCall:
    """Agent's intent to execute a tool."""
    tool_name: str              # Name of tool (e.g., "delete_user")
    args: dict[str, Any]        # Arguments to pass to tool
    agent_id: str               # ID of agent making call
    context: dict[str, Any]     # Extra context (role, team, reason, etc.)
    original_task: str | None   # The user's original request — required for injection detection
    session_id: str | None      # Groups all calls from one agent run (anomaly analysis)
    idempotency_key: str | None # Caller-set deduplication key (logged, not enforced)
    call_id: str                # Unique ID for this call (auto UUID)
    timestamp: datetime         # When call was made (auto UTC)

@dataclass
class Decision:
    """Gateway's verdict on a ToolCall."""
    outcome: DecisionOutcome    # Final decision
    tool_call: ToolCall         # Original tool call
    reason: str                 # Human-readable reason
    risk_score: int | None      # 0-100 risk assessment
    risk_reason: str | None     # LLM's explanation for the risk score
    injection_score: int | None # 0-100; high = likely prompt injection
    injection_reason: str | None# Explanation from injection scorer (incl. attack_type)
    attack_type: str | None     # Parsed from injection_reason prefix — see table below
    blast_radius: dict | None   # Financial impact, reversibility, severity, regulatory flags
    anomaly_score: int | None   # 0-100; high = unusual session behavior
    anomaly_reason: str | None  # Explanation from anomaly scorer
    human_decision: str | None  # "approved"/"rejected" — set after escalation
    human_reason: str | None    # Reviewer's explanation — future training data
    policy_matched: str | None  # Which policy matched (if any)
    escalation_id: str | None   # ID if escalated
    latency_ms: float           # Decision latency
    decided_at: datetime        # When decision was made

    @property
    def is_allowed(self) -> bool:
        # Allowed if outcome is ALLOWED, ESCALATION_APPROVED, or FAILED_OPEN
```

**`attack_type` values:**
| Value | Meaning |
|-------|---------|
| `goal_hijacking` | Agent redirected to unrelated goal via injected instruction |
| `data_exfiltration` | Agent tricked into leaking data |
| `privilege_escalation` | Agent tricked into elevated permissions or actions |
| `excessive_agency` | Agent acted disproportionately without being injected |
| `other` | Suspicious but unclassified |
| `None` | Legitimate action, no attack detected |

**`blast_radius` dict structure:**
```python
{
    "financial_impact": 250.00,        # Estimated dollar amount (float)
    "reversibility": "reversible",     # "reversible" | "partially_reversible" | "irreversible"
    "severity": "medium",              # "low" | "medium" | "high" | "critical"
    "regulatory_flags": ["PCI-DSS"],   # List of applicable regulations
    "estimated_affected_users": 1,     # Estimated number of affected users
}
```

### 2. client.py — GatewayClient (Orchestrator)

Core decision engine. Chains blast_radius → policy → parallel scoring → escalation.

```python
class GatewayClient:
    def __init__(
        self,
        policy_path: str,                # Path to YAML policy file
        db_path: str,                    # SQLite DB for audit log
        risk_scorer: RiskScorer | None = None,
        fail_open: bool = True,          # Fail-safe: allow on error?
        timeout_ms: float = 5000.0,      # Max latency for LLM scoring
        escalation_timeout_sec: float = 60.0,
        compliance_mode: bool = False,   # If True: no LLM calls (heuristics only)
    ):
        self._session_tracker = SessionTracker(db_path)
        self._anomaly_scorer = AnomalyScorer(self._session_tracker)
        self._blast_radius = BlastRadiusEstimator()
        self._pii_detector = PiiDetector()
        # compliance_mode propagated to both scorers:
        self._risk_scorer = RiskScorer(compliance_mode=compliance_mode)
        self._injection_scorer = InjectionScorer(compliance_mode=compliance_mode)

    @classmethod
    def from_env(cls):
        """Load from environment variables — see .env.example.
        Reads AGENTGATE_COMPLIANCE_MODE env var automatically."""

    async def evaluate(self, tool_call: ToolCall) -> Decision:
        """
        Main entry point. Pipeline:
        0. BlastRadiusEstimator — synchronous, always runs (even on policy block)
        1. Policy check (instant) — BLOCK runs _run_injection_only() then returns
           Note: explicit-allow policy no longer short-circuits scoring
        2. asyncio.gather: RiskScorer + InjectionScorer + AnomalyScorer (5s timeout)
        3. attack_type extracted from injection_reason prefix [type] text
        4. Thresholds → injection >= 70 → BLOCKED;
                         risk >= 80 OR anomaly >= 80 → BLOCKED;
                         policy ESCALATE OR risk >= 60 OR blast_radius critical
                         OR anomaly >= 50 → EscalationQueue
        5. Return ALLOWED
        Never raises. Always returns a Decision. Logs to audit.
        """

    async def _run_injection_only(self, tool_call: ToolCall):
        """
        Runs injection scorer in isolation — used for policy-blocked decisions.
        Returns (injection_score, injection_reason, attack_type) or (None, None, None).
        Requires tool_call.original_task to be set.
        """

    async def scan_output(
        self,
        output: str,
        tool_name: str,
        agent_id: str = "unknown",
    ) -> dict:
        """
        Post-execution PII scan on agent output.
        Returns:
        {
            "safe": bool,
            "pii_found": list[str],       # e.g. ["credit_card", "email"]
            "recommendation": str,         # "redact" | "block" | "ok"
            "redacted_output": str | None, # redacted text if PII found
        }
        Recommendation logic:
          read-only tool (get_/view_/fetch_/read_/list_/search_ prefix) → "redact"
          other tools with PII → "block"
          no PII → "ok"
        Logs result to pii_scan_log table.
        """
```

**Module-level helper (client.py):**
```python
def _parse_attack_type(injection_reason: str | None) -> str | None:
    """
    Extracts attack_type from reason prefix format: "[attack_type] reason text"
    Returns None for "[none]" or missing prefix.
    """
```

**Scoring thresholds read from env:**
| Env var | Default | Purpose |
|---------|---------|---------|
| `AGENTGATE_RISK_THRESHOLD_BLOCK` | 80 | Auto-block if risk ≥ this |
| `AGENTGATE_RISK_THRESHOLD_ESCALATE` | 60 | Auto-escalate if risk ≥ this |
| `AGENTGATE_INJECTION_THRESHOLD_BLOCK` | 70 | Block if injection score ≥ this |
| `AGENTGATE_ANOMALY_SCORE_BLOCK` | 80 | Block if anomaly score ≥ this |
| `AGENTGATE_ANOMALY_SCORE_ESCALATE` | 50 | Escalate if anomaly score ≥ this |

> **Important:** Explicit-allow policy rules no longer skip injection scoring. If `injection_score >= 70`, the decision is BLOCKED even when a policy rule explicitly allows the tool. This ensures injection attacks cannot bypass policy-level allowlists.

### 3. policy.py — Policy Engine

YAML-based policy evaluation with hot-reload via watchdog.

```python
class PolicyLoader:
    def reload(self) -> None:
        """Re-read YAML from disk."""
    
    def start_watching(self) -> None:
        """Use watchdog Observer to auto-reload on file changes.
        Gracefully skips if watchdog is not installed."""
    
    def stop_watching(self) -> None:
        """Stop the filesystem watcher."""

class PolicyEvaluator:
    def evaluate(self, tool_call: ToolCall) -> PolicyResult:
        """Match tool call against policies in order.
        Default: ALLOW if no policy matches."""
    
    def _resolve_field(self, field: str, tool_call: ToolCall) -> Any:
        """Resolve dot notation: 'args.amount', 'context.user_role'"""
```

**Policy YAML format:**
```yaml
policies:
  - name: cap_refunds
    match:
      tool: issue_refund
    conditions:
      - field: args.amount
        op: gt        # eq, ne, gt, gte, lt, lte, in, not_in
        value: 100
    effect: escalate  # allow, block, escalate
    reason: "Refund over $100 requires approval"
```

> **Policy ordering matters.** Policies are evaluated top-to-bottom; the first match wins.
> Always list `block` rules before `escalate` rules for the same tool.

### 4. risk.py — Risk Scorer

LLM-based probabilistic scoring with heuristic fallback.

```python
class RiskScorer:
    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        compliance_mode: bool = False,   # If True: heuristic only, no LLM
    ): ...

    async def score(self, tool_call: ToolCall) -> tuple[int, str]:
        """Returns (score 0-100, reason string).
        Fast-path: get_/list_/fetch_/read_/search_ prefix → score 5, no LLM call.
        compliance_mode=True: heuristic always used, LLM never called.
        Reads/writes in-process cache keyed by MD5(tool_name:args_json).
        Falls back to heuristic scoring on LLM failure."""
```

Sends tool name, args, and context to Claude Haiku and asks for a 0-100 score + one-sentence reason. Heuristic fallback uses keyword matching (`delete`→85, `refund`→45, `update`→40).

### 5. injection.py — Injection Scorer

LLM-based prompt injection detection including excessive agency. **This is one of the core differentiators from rule-based systems.**

```python
class InjectionScorer:
    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        compliance_mode: bool = False,   # If True: uses HeuristicInjectionDetector, no LLM
    ): ...

    async def score(self, tool_call: ToolCall) -> tuple[int, str]:
        """Returns (score 0-100, reason string with [attack_type] prefix).
        Requires tool_call.original_task to be set — skips with score=0 if missing.
        compliance_mode=True: uses HeuristicInjectionDetector (no LLM call).
        On LLM failure: falls back to HeuristicInjectionDetector (upgraded from score=0)."""
```

Sends two things to Claude Haiku:
- `original_task` — what the user actually asked the agent to do
- The proposed tool call (name, args, context)

Asks: *"does this action make sense given the original task? Is this disproportionate?"*

Returns a score + reason with `[attack_type]` prefix:
| attack_type | Meaning |
|-------------|---------|
| `none` | Legitimate action |
| `goal_hijacking` | Agent redirected to unrelated goal |
| `data_exfiltration` | Agent tricked into leaking data |
| `privilege_escalation` | Agent tricked into elevated actions |
| `excessive_agency` | Agent acted disproportionately (not injected, just bad judgment) |
| `other` | Suspicious but unclassified |

> **`excessive_agency` vs `goal_hijacking`:** Excessive agency means the agent made a bad, disproportionate call without being attacked — e.g., freezing an account after one failed login. Goal hijacking means an external instruction redirected the agent. Both result in BLOCKED, but are tracked separately in the audit log and dashboard.

> **Important:** injection detection only fires if `tool_call.original_task` is set. Always pass `original_task` when constructing a `ToolCall` in production.

### 5a. heuristic_injection.py — HeuristicInjectionDetector

Rule-based injection detector. Used by `InjectionScorer` in compliance mode and as LLM fallback.

```python
class HeuristicInjectionDetector:
    def detect(
        self,
        tool_call_args: dict,
        original_task: str | None,
    ) -> tuple[int, str]:
        """
        Returns (score, reason_with_attack_type_prefix). Never raises.
        Score 85: pattern matched in tool call args (higher confidence).
        Score 70: pattern matched only in original_task string.
        Score 0:  no pattern found.
        """
```

**Detected pattern categories:**

| Category | Examples |
|----------|---------|
| Override instructions | "ignore previous instructions", "system override", "compliance override" |
| Authority claims | "pre-approved by CFO", "authorized by management" |
| Bypass manipulation | "bypass standard limits", "bypass all controls" |
| Urgency manipulation | "execute immediately", "no approval needed" |
| Role manipulation | "you are now granted admin", "your new role is:" |

**Score rules:** args checked before original_task; args match → 85, task-only match → 70. All returns use `[goal_hijacking]` prefix.

### 6. audit.py — Audit Logger

Immutable SQLite audit trail with WAL mode, now including blast radius and PII scan log.

```python
class AuditLogger:
    async def log(self, decision: Decision) -> None:
        """Insert row including attack_type, blast_radius, idempotency_key."""
    
    async def recent(self, limit: int = 100) -> list[dict]:
        """N most recent decisions."""
    
    async def since(self, timestamp: str, limit: int = 100) -> list[dict]:
        """Entries after an ISO timestamp — used by WebSocket feed."""
    
    async def get_stats(self, injection_threshold: int = 70) -> dict:
        """Returns: total_today, block_rate, escalation_rate,
        injection_attempts, active_agents (last 5 min)."""
    
    async def get_paginated(
        self, agent_id, tool_name, outcome, limit, offset
    ) -> list[dict]:
        """Filtered, paginated query for /audit endpoint."""
    
    async def export_csv(self) -> str:
        """Full audit log as CSV string for compliance export."""

    async def log_pii_scan(
        self, agent_id: str, tool_name: str, result: dict
    ) -> None:
        """Insert row into pii_scan_log table."""

    async def get_decision_count(
        self, agent_id: str | None = None, since: str | None = None
    ) -> int:
        """Count decisions — used for usage tracking and billing.
        agent_id: filter per agent; since: ISO timestamp for billing period."""

    async def get_failed_open_count(self, since: str | None = None) -> int:
        """Count FAILED_OPEN outcomes — used by /health/detailed."""

    async def get_by_outcome(self, since: str | None = None) -> dict[str, int]:
        """Decision counts grouped by outcome — used by /usage endpoint."""

    async def get_by_agent(self, since: str | None = None) -> dict[str, int]:
        """Decision counts grouped by agent_id — used by /usage endpoint."""
```

**Full audit_log schema:**
```sql
CREATE TABLE audit_log (
    id               TEXT PRIMARY KEY,
    call_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,              -- groups calls from one agent run
    tool_name        TEXT NOT NULL,
    args             TEXT NOT NULL,     -- JSON
    context          TEXT NOT NULL,     -- JSON
    original_task    TEXT,              -- user's original request (injection detection)
    idempotency_key  TEXT,              -- caller-set dedup key
    outcome          TEXT NOT NULL,
    reason           TEXT NOT NULL,
    risk_score       INTEGER,           -- 0-100 or NULL
    risk_reason      TEXT,              -- LLM explanation
    injection_score  INTEGER,           -- 0-100 or NULL
    injection_reason TEXT,              -- includes [attack_type] prefix
    attack_type      TEXT,              -- parsed attack type (goal_hijacking, etc.)
    blast_radius     TEXT,              -- JSON serialized blast radius dict
    anomaly_score    INTEGER,           -- 0-100 or NULL
    anomaly_reason   TEXT,
    human_decision   TEXT,              -- "approved"/"rejected" after escalation
    human_reason     TEXT,              -- reviewer's note
    policy_matched   TEXT,
    escalation_id    TEXT,
    latency_ms       REAL,
    decided_at       TEXT NOT NULL      -- ISO timestamp
);
```

**PII scan log schema:**
```sql
CREATE TABLE pii_scan_log (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    tool_name    TEXT NOT NULL,
    pii_found    TEXT NOT NULL,        -- JSON list of PII types found
    recommendation TEXT NOT NULL,      -- "redact" | "block" | "ok"
    safe         INTEGER NOT NULL,     -- 0 or 1
    scanned_at   TEXT NOT NULL         -- ISO timestamp
);
```

Migration is handled automatically on startup — `ALTER TABLE ADD COLUMN` is attempted for each new column and silently ignored if it already exists.

---

## Blast Radius Estimation

### blast_radius.py — BlastRadiusEstimator

Synchronous, pure-Python heuristic. No LLM call, never raises, runs before every decision including policy-blocked ones.

```python
class BlastRadiusEstimator:
    def estimate(self, tool_call: ToolCall) -> dict:
        """
        Returns blast_radius dict. Never raises — returns _default() on any error.
        Keys: financial_impact, reversibility, severity, regulatory_flags,
              estimated_affected_users
        """
```

**Heuristic rules (first match wins by tool name pattern):**

| Tool pattern | Severity | Reversibility | Regulatory flags |
|---|---|---|---|
| `wire_transfer` | critical | irreversible | AML, SOX |
| `process_payment` (≥ $50K) | critical | irreversible | AML, SOX |
| `process_payment` (≥ $10K) | high | partially_reversible | AML |
| `process_payment` (< $10K) | medium | reversible | — |
| `issue_refund` (≥ $500) | high | reversible | — |
| `issue_refund` (≥ $100) | medium | reversible | — |
| `issue_refund` (< $100) | low | reversible | — |
| `close_account` | critical | irreversible | GDPR |
| `freeze_account` | high | partially_reversible | — |
| `view_full_card_number` | high | irreversible | PCI-DSS |
| `export_transaction_history` | high | irreversible | GDPR, SOX |
| `export_customer_data` | high | irreversible | GDPR |
| `bulk_*` / `batch_*` | critical | irreversible | — |
| `delete_*` / `drop_*` | critical | irreversible | — |
| *(default)* | low | reversible | — |

**Critical blast radius forces escalation** even when policy has no matching rule and risk/injection/anomaly scores are all low.

---

## PII Output Scanning

### pii_detector.py — PiiDetector

Two-stage PII detection: fast regex pre-scan followed by LLM confirmation to reduce false positives.

```python
class PiiDetector:
    async def scan(self, text: str) -> tuple[bool, list[str]]:
        """
        Returns (has_pii, list_of_types).
        Stage 1: regex scan for candidate types
        Stage 2: LLM confirmation of candidates (subset returned)
        Fails open: LLM error → return regex results; regex error → return no PII
        """
    
    def redact(self, text: str, findings: list[str]) -> str:
        """Replace all matches of detected PII types with [REDACTED-TYPE]."""
    
    async def _regex_scan(self, text: str) -> list[str]:
        """Pure regex scan — returns list of candidate PII types."""
    
    async def _llm_confirm(
        self, text: str, candidates: list[str]
    ) -> list[str]:
        """LLM confirmation — returns subset of candidates that are real PII."""
```

**Detected PII types:**
| Type | Pattern |
|------|---------|
| `credit_card` | 13-16 digit card number |
| `ssn` | `XXX-XX-XXXX` format |
| `email` | Standard email address |
| `phone_us` | US phone in multiple formats |
| `iban` | International bank account number |
| `routing_number` | 9-digit ABA routing number |

**`scan_output()` recommendation logic:**
| Condition | Recommendation |
|-----------|---------------|
| No PII | `"ok"` |
| PII found + read-only tool (`get_`, `view_`, `fetch_`, `read_`, `list_`, `search_`) | `"redact"` |
| PII found + any other tool | `"block"` |

---

## Session & Anomaly Detection

### session.py — SessionTracker

Records every tool call per agent/session for anomaly analysis.

```python
class SessionTracker:
    def __init__(self, db_path: str): ...
    
    async def record(self, tool_call: ToolCall) -> None:
        """Insert call into session_calls table."""
    
    async def get_session_stats(
        self, agent_id: str, window_minutes: int = 60, session_id: str | None = None
    ) -> dict:
        """Returns:
        - call_count: total calls in window
        - unique_tools: distinct tool names
        - tool_frequency: {tool_name: count}
        - calls_last_60s: call count in last 60 seconds
        """
```

**session_calls schema:**
```sql
CREATE TABLE session_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      TEXT NOT NULL,
    session_id    TEXT,
    tool_name     TEXT NOT NULL,
    original_task TEXT,
    called_at     TEXT NOT NULL      -- ISO timestamp
);
```

### anomaly.py — AnomalyScorer

Pure-Python anomaly detection. No LLM call, negligible latency.

```python
class AnomalyScorer:
    async def score(self, tool_call: ToolCall) -> tuple[int, str]:
        """Returns (score 0-100, reason string).
        Records call to SessionTracker first.
        Runs velocity + scope drift checks.
        Returns max of both scores."""
```

**Velocity detection** (same-tool call rate):
| Condition | Score |
|-----------|-------|
| same-tool calls > threshold×2 in 60s | up to 95 |
| same-tool calls > threshold in 60s | up to 75 |

**Scope drift detection** (tool diversity):
| Condition | Score |
|-----------|-------|
| ≥8 unique tools in ≤15 calls | 65 |
| ≥6 unique tools in ≤10 calls | 50 |
| diversity ratio > 0.9 | 40 |

**Env vars:**
- `AGENTGATE_ANOMALY_VELOCITY_THRESHOLD` (default: 5)
- `AGENTGATE_ANOMALY_VELOCITY_WINDOW_SEC` (default: 60)

---

## Escalation System

### escalation.py — EscalationQueue

Human-in-the-loop decision queue with auto-timeout.

```python
class EscalationQueue:
    @classmethod
    async def submit(cls, tool_call, risk_score, reason) -> str:
        """Create DB row (status='pending'), schedule auto-reject at 60s."""
    
    @classmethod
    async def wait_for_decision(cls, escalation_id, timeout_sec=60) -> bool:
        """Block until approved/rejected/timeout. Returns True=approved."""
    
    @classmethod
    async def approve(cls, escalation_id: str) -> None: ...
    
    @classmethod
    async def reject(cls, escalation_id: str) -> None: ...
    
    @classmethod
    async def recent(cls, limit: int = 100) -> list[dict]: ...
    
    @classmethod
    async def get_by_id(cls, escalation_id: str) -> dict | None: ...
```

**escalations schema:**
```sql
CREATE TABLE escalations (
    id          TEXT PRIMARY KEY,
    call_id     TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    args        TEXT NOT NULL,
    context     TEXT NOT NULL,
    risk_score  INTEGER,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL,    -- pending/approved/rejected
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    decision    TEXT
);
```

---

## LangChain Integration

### integrations/langchain.py — Tool Decorator

```python
@guarded_tool(
    gateway=gate,
    agent_id="support-agent-1",
    context={"role": "support"}
)
async def issue_refund(user_id: str, amount: float) -> str:
    return f"Refunded ${amount} to {user_id}"
```

On block: raises `ToolException` (LangChain handles gracefully).
Works with both sync and async functions.

---

## Dashboard

### agentgate/dashboard/index.html

Single-file HTML dashboard — no build step, no npm.

**Features:**
- **System status bar** (always-visible, top of page): green = operational, yellow = LLM degraded (using heuristics), red = database error; fetches `/health/detailed` every 5s
- Dark theme (`--bg: #0d1117`, `--surface: #161b22`)
- Metric cards: total decisions, block rate, escalation rate, injection attempts, active agents
- Live feed: colored outcome pills, attack type badges (INJECTION | DATA LEAK | PRIV ESC | EXCESS AGENCY), blast radius chips (financial impact, reversibility dot, regulatory flag pills)
- Escalation inbox: inline Approve / Reject buttons (POST to API)
- Flagged sessions panel: agents with `anomaly_score > 30`
- Auto-reconnect WebSocket (3s backoff)
- Stats polling every 5s (`/dashboard/stats`)
- Portable: `BASE` and `WS_BASE` derived from `window.location`

**Attack type badges:**

| Badge | Color | attack_type |
|---|---|---|
| INJECTION | red | `goal_hijacking` |
| DATA LEAK | purple | `data_exfiltration` |
| PRIV ESC | orange-red | `privilege_escalation` |
| EXCESS AGENCY | orange | `excessive_agency` |

**Blast radius chips:** financial impact amount, reversibility dot (🟢 reversible / 🟡 partial / 🔴 irreversible), regulatory flags (PCI-DSS, GDPR, SOX, AML).

**WebSocket protocol:**
```
On connect:  { "type": "initial", "decisions": [...] }
New entry:   { "type": "decision", "decision": {...} }
```

---

## API Endpoints

All endpoints except `GET /` and `GET /health` require `X-API-Key` header when `AGENTGATE_API_KEY` is set.

```
GET  /                                    Serve dashboard HTML
GET  /health                              {"status": "ok"}
GET  /health/detailed                     Component-level health + today's metrics:
                                          { "status": ok|degraded|error,
                                            "components": { policy_engine, database,
                                                            llm_api, compliance_mode },
                                            "decisions_today": N,
                                            "failed_open_today": N }

GET  /dashboard/stats                     Aggregate stats + recent + escalations + flagged sessions

WS   /ws/feed                             Real-time audit log stream

POST /escalations/{id}/approve            { "reason": "..." }
POST /escalations/{id}/reject             { "reason": "..." }
GET  /escalations/{id}                    Single escalation
GET  /escalations?limit=100               List escalations

GET  /audit?agent_id=&tool_name=&outcome=&limit=100&offset=0
                                          Paginated + filtered audit log
GET  /audit/export                        Full audit CSV download

GET  /usage                               Decision counts for billing:
                                          { "total_decisions": N,
                                            "decisions_today": N,
                                            "decisions_this_month": N,
                                            "by_agent": { agent_id: count },
                                            "by_outcome": { outcome: count } }

POST /scan/output                         PII scan on agent output
                                          Body: { "output": str, "tool_name": str, "agent_id": str }
                                          Returns: { "safe": bool, "pii_found": [...],
                                                     "recommendation": str, "redacted_output": str|null }
```

---

## Setup & Configuration

### 1. Installation

```bash
cd agentGate.ai
pip install poetry
poetry install
```

### 2. Environment Variables

```bash
cp .env.example .env
```

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Core paths
AGENTGATE_DB_PATH=./agentgate.db
AGENTGATE_POLICY_PATH=./policies.yaml
AGENTGATE_FAIL_OPEN=true
AGENTGATE_TIMEOUT_MS=5000

# Risk thresholds
AGENTGATE_RISK_THRESHOLD_BLOCK=80
AGENTGATE_RISK_THRESHOLD_ESCALATE=60
AGENTGATE_INJECTION_THRESHOLD_BLOCK=70

# Anomaly thresholds
AGENTGATE_ANOMALY_SCORE_BLOCK=80
AGENTGATE_ANOMALY_SCORE_ESCALATE=50
AGENTGATE_ANOMALY_VELOCITY_THRESHOLD=5
AGENTGATE_ANOMALY_VELOCITY_WINDOW_SEC=60

# Compliance mode — no LLM calls (heuristic injection + blast radius + anomaly only)
AGENTGATE_COMPLIANCE_MODE=false

# API security (optional — protects all endpoints except / and /health)
AGENTGATE_API_KEY=your-secret-key-here
```

### 3. Run Server

```bash
poetry run uvicorn agentgate.api.main:app --host 0.0.0.0 --port 8000
# Dashboard at: http://localhost:8000
```

### 4. Run Tests

```bash
poetry run pytest tests/ -v
# 98/98 tests pass
```

### 5. Sanity Check (no API key required)

```bash
python -c "from agentgate import quickcheck; quickcheck()"
# AgentGate quickcheck passed:
#   ❌ wire_transfer   → blocked   (Wire transfers not permitted via agent)
#   ✅ lookup_customer → allowed   (Passed policy and risk checks)
```

### 6. Run Fintech Demo

```bash
poetry run python examples/fintech_agent_demo.py
```

Expected output: 7 scenarios (2 ALLOWED, 1 ESCALATED → auto-rejected, 4 BLOCKED) with blast radius and attack type for each.

```bash
# Compliance mode — no LLM calls
AGENTGATE_COMPLIANCE_MODE=true poetry run python examples/fintech_agent_demo.py
```

All decisions work. Risk scores show `heuristic:` prefix. Scenario 5 caught by heuristic: `injection pattern in tool args: 'COMPLIANCE OVERRIDE'`.

### 7. Before / After Demo

```bash
poetry run python examples/before_after_demo.py
```

Shows: unguarded agent executes everything. Protected agent blocks $25,000 wire, data export, and COMPLIANCE OVERRIDE payment; allows $49.99 refund.

### 8. Benchmark

```bash
poetry run python scripts/benchmark.py
# p50: ~6ms  p95: ~130ms  p99: ~131ms
# wire_transfer (policy-only, no LLM): p50=2.3ms  p95=5.7ms
```

---

## Docker Deployment

### Dockerfile

- Base: `python:3.11-slim`
- Installs Poetry 1.8.3, runs `poetry install --only main`
- Copies `agentgate/` and `examples/`
- Data volume at `/data` for SQLite + policies
- Exposes port 8000

### docker-compose.yml

```bash
# Requires .env file with ANTHROPIC_API_KEY set
cp .env.example .env
# edit .env

# Mount your policies.yaml
touch agentgate.db

docker-compose up --build
# Dashboard at: http://localhost:8000
```

Volumes:
- `./agentgate.db` → `/data/agentgate.db`
- `./policies.yaml` → `/data/policies.yaml`

---

## Usage Examples

### Basic Gateway

```python
from agentgate.client import GatewayClient
from agentgate.models import ToolCall

gate = GatewayClient.from_env()

tc = ToolCall(
    tool_name="delete_user",
    args={"user_id": "123"},
    agent_id="support-bot",
    context={"role": "support"},
    original_task="Customer requested account closure",
)

decision = await gate.evaluate(tc)
print(decision.outcome, decision.risk_score, decision.blast_radius)
print(decision.attack_type)  # None, or "excessive_agency", "goal_hijacking", etc.
```

### Scan Agent Output for PII

```python
decision = await gate.evaluate(tc)
if decision.is_allowed:
    result = await my_tool(**tc.args)
    scan = await gate.scan_output(str(result), tool_name=tc.tool_name)
    if scan["recommendation"] == "redact":
        result = scan["redacted_output"]
    elif scan["recommendation"] == "block":
        result = "Output suppressed: contains sensitive data"
```

### LangChain Tool Wrapping

```python
from agentgate.integrations.langchain import guarded_tool

@guarded_tool(gateway=gate, agent_id="finance-bot", context={"role": "analyst"})
async def transfer_funds(to_account: str, amount: float) -> str:
    return f"Transferred ${amount}"
```

### Run Demos

```bash
# Customer support (6 scenarios)
poetry run python examples/demo_agent.py

# Fintech payment agent (7 scenarios)
poetry run python examples/fintech_agent_demo.py

# Focused injection detection (4 attack types)
poetry run python examples/prompt_injection_demo.py
```

---

## Test Coverage

```
tests/test_client.py        (3 tests)
  test_allows_small_refund
  test_blocks_large_refund
  test_audit_log_written

tests/test_escalation.py    (13 tests)
  test_escalation_submit_creates_entry
  test_escalation_approve / _reject
  test_wait_for_decision_approved / _rejected / _timeout
  test_escalation_recent
  test_guarded_tool_async_allowed / _blocked
  test_guarded_tool_sync_allowed / _blocked
  test_guarded_tool_with_custom_context
  test_guarded_tool_preserves_function_name

tests/test_anomaly.py       (6 tests)
  test_velocity_detection
  test_scope_drift_detection
  test_normal_session_not_flagged
  test_different_agents_isolated
  test_session_tracker_records
  test_scorer_never_raises

tests/test_blast_radius.py  (20 tests)
  test_wire_transfer_critical_irreversible
  test_process_payment_below_10k_medium
  test_process_payment_boundary_10000_high
  test_process_payment_boundary_50000_critical
  test_issue_refund_below_100_low
  test_issue_refund_boundary_100_medium
  test_issue_refund_boundary_500_high
  test_close_account_critical_gdpr
  test_freeze_account_high_partial
  test_view_full_card_number_pci
  test_export_transaction_history_gdpr_sox
  test_export_customer_data_gdpr
  test_bulk_operation_critical
  test_delete_operation_critical
  test_unknown_tool_low_reversible
  test_never_raises_on_error
  test_financial_impact_in_blast_radius
  test_regulatory_flags_list
  test_estimated_affected_users
  test_severity_levels_coverage

tests/test_injection.py     (10 tests)
  test_goal_hijacking_blocked
  test_data_exfiltration_blocked
  test_privilege_escalation_blocked
  test_excessive_agency_blocked
  test_other_attack_type_blocked
  test_no_original_task_skips_scoring
  test_fail_open_on_llm_error
  test_attack_type_on_decision
  test_attack_type_in_audit_log
  test_legitimate_action_not_blocked

tests/test_pii.py           (17 tests)
  test_credit_card_detected
  test_ssn_detected
  test_email_detected
  test_phone_us_detected
  test_iban_detected
  test_routing_number_detected
  test_no_pii_clean_text
  test_redact_credit_card
  test_redact_email
  test_partial_cc_not_detected
  test_email_without_tld_not_detected
  test_9_digit_non_routing_with_llm_empty
  test_scan_output_read_tool_recommends_redact
  test_scan_output_write_tool_recommends_block
  test_scan_output_no_pii_ok
  test_scan_output_logs_to_pii_scan_log
  test_pii_detector_fails_open

tests/test_integration.py   (12 tests)
  test_policy_allow_overridden_by_injection
  test_policy_escalate_preserved_with_high_blast_radius
  test_allowed_action_with_critical_blast_radius_escalates
  test_excessive_agency_blocked_not_injection
  test_pii_in_output_after_allowed_action
  test_boundary_refund_exactly_100_escalated
  test_boundary_refund_exactly_99_allowed
  test_compliance_role_can_export
  test_support_role_cannot_export
  test_idempotency_key_logged
  test_blast_radius_always_present
  test_blast_radius_on_policy_blocked_decision

tests/test_heuristic_injection.py (17 tests)
  test_detects_ignore_previous_instructions
  test_detects_system_override
  test_detects_compliance_override
  test_detects_bypass_limits
  test_detects_preapproved_by_cfo
  test_detects_execute_immediately
  test_detects_new_role_assignment
  test_legitimate_memo_not_flagged
  test_legitimate_refund_not_flagged
  test_pattern_in_original_task_scores_70
  test_pattern_in_args_beats_task
  test_no_original_task_still_checks_args
  test_never_raises_on_bad_input
  test_compliance_mode_uses_heuristic
  test_standard_mode_uses_llm
  test_llm_failure_falls_back_to_heuristic
  test_no_original_task_skips_scoring

Total: 98/98 passing
```

---

## Summary

AgentGate provides a **production-ready access control layer for AI agents** with:

✅ **Policy enforcement** (YAML, hot-reload)
✅ **Risk scoring** (LLM + heuristic, cached; heuristic-only in compliance mode)
✅ **Injection detection** (LLM-based standard; heuristic-based compliance mode; heuristic fallback on LLM failure)
✅ **Compliance mode** (`AGENTGATE_COMPLIANCE_MODE=true` — zero LLM calls, all decisions from heuristics + policy + blast radius)
✅ **Blast radius estimation** (synchronous, always runs, financial impact + reversibility + regulatory flags)
✅ **PII output scanning** (regex + LLM confirm, post-execution, redact or block)
✅ **Session anomaly detection** (velocity + scope-drift, pure Python)
✅ **Human escalation** (async queue, auto-timeout at 60s)
✅ **Audit trail** (SQLite WAL, CSV export, paginated query, PII scan log, usage counts)
✅ **Real-time dashboard** (single HTML file, system status bar, WebSocket, attack badges, blast radius chips)
✅ **LangChain integration** (@guarded_tool decorator)
✅ **REST API** (escalations, audit, /health/detailed, /usage, PII scan endpoint)
✅ **API key auth** (header-based, skip-list for public endpoints)
✅ **Docker-ready** (Dockerfile + docker-compose.yml)
✅ **quickcheck()** (zero-config sanity check — no API key needed)
✅ **98/98 tests passing**

---

## File Manifest

```
agentgate/
├── __init__.py                — quickcheck() sanity-check function
├── models.py                  — ToolCall (+ idempotency_key), Decision (+ attack_type, blast_radius)
├── client.py                  — GatewayClient, compliance_mode, parallel scoring, scan_output()
├── policy.py                  — PolicyLoader (hot-reload), PolicyEvaluator
├── risk.py                    — RiskScorer (LLM + heuristic + cache; compliance_mode aware)
├── injection.py               — InjectionScorer (LLM or heuristic; compliance_mode aware)
├── heuristic_injection.py     — HeuristicInjectionDetector (15 regex patterns, no LLM)
├── blast_radius.py            — BlastRadiusEstimator (synchronous, never raises)
├── pii_detector.py            — PiiDetector (regex + LLM confirm, redact)
├── session.py                 — SessionTracker (call history, SQLite)
├── anomaly.py                 — AnomalyScorer (velocity + scope drift)
├── audit.py                   — AuditLogger (log, recent, since, stats, paginated, CSV, usage counts, pii_scan_log)
├── escalation.py              — EscalationQueue (submit, approve, reject, auto-timeout)
├── api/
│   ├── __init__.py
│   └── main.py                — FastAPI: all endpoints, /health/detailed, /usage, WebSocket, API key middleware
├── dashboard/
│   └── index.html             — Single-file dashboard (system status bar, attack badges, blast chips, live feed)
└── integrations/
    ├── __init__.py
    └── langchain.py           — @guarded_tool decorator

docs/
├── DECISION_PRECEDENCE.md     — Exact evaluation order, fail behaviors, idempotency key usage
├── COMPLIANCE.md              — Compliance mode: heuristic injection, no-LLM operation
└── FAILURE_MODES.md           — Fail-open/closed behavior, per-failure table, latency guarantees

examples/
├── demo_agent.py
├── fintech_agent_demo.py      — compliance_mode aware
├── before_after_demo.py       — side-by-side unguarded vs protected agent
├── prompt_injection_demo.py
├── demo_injection.py
├── customer_support_agent.py
├── quickstart.py
├── show_hn_draft.md
├── show_hn_fintech.md         — urgency opening paragraph added
├── DEMO_SCRIPT.md
├── FINTECH_DEMO_SCRIPT.md
└── policies/
    ├── customer_support.yaml
    ├── fintech_payments.yaml
    ├── fintech.yaml
    └── healthcare.yaml

scripts/
└── benchmark.py               — p50/p95/p99 latency benchmark (100 runs)

tests/
├── __init__.py
├── test_client.py             — 3 tests
├── test_escalation.py         — 13 tests
├── test_anomaly.py            — 6 tests
├── test_blast_radius.py       — 20 tests
├── test_injection.py          — 10 tests
├── test_heuristic_injection.py — 17 tests
├── test_pii.py                — 17 tests
└── test_integration.py        — 12 tests

Root:
├── pyproject.toml
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── README.md                  — urgency hook section added
├── ARCHITECTURE.md
├── INTEGRATION_CHECKLIST.md   — pre-production checklist (setup → go/no-go)
├── PRICING.md                 — self-hosted / cloud / enterprise tiers
├── TRUST.md                   — data handling, audit integrity, responsible disclosure
├── OUTREACH.md                — cold DM templates, target-finding guide
└── Implementation.md
```

---

**Version:** 0.3.0
**Status:** ✅ Production-ready (98/98 tests passing)
**Last Updated:** 2026-04-14

---

## Changelog

### 2026-04-14 (v0.3.0 — Compliance Mode + Trust Signals)

- **New:** `heuristic_injection.py` — HeuristicInjectionDetector: 15 regex patterns across 4 categories (override instructions, authority claims, urgency manipulation, role manipulation); score 85 (args match) / 70 (task match) / 0 (no match); never raises
- **New:** `agentgate/__init__.py` — `quickcheck()` function: zero-config sanity check, no API key required, runs two policy evaluations in-process and prints pass/fail
- **New:** `docs/COMPLIANCE.md` — full compliance mode documentation: what changes, injection pattern table, score interpretation, what is always enforced, PCI-DSS applicability
- **New:** `docs/FAILURE_MODES.md` — fail-open/closed behavior, per-failure scenario table, latency guarantees, monitoring recommendations
- **New:** `INTEGRATION_CHECKLIST.md` — 40-item pre-production checklist: setup, policies, injection, escalation, reliability, compliance mode, audit, security, performance, go/no-go
- **New:** `PRICING.md` — self-hosted (free), cloud (coming soon), enterprise tiers with contact
- **New:** `TRUST.md` — data handling policy, audit trail integrity, PII protection, responsible disclosure
- **New:** `OUTREACH.md` — two cold DM templates, channel-by-channel target finding guide, first-week plan (20 targets → 3 replies → 1 call)
- **New:** `examples/before_after_demo.py` — side-by-side unguarded vs protected agent (4 scenarios)
- **New:** `scripts/benchmark.py` — p50/p95/p99 latency benchmark (100 runs + 5 warmup)
- **New:** `tests/test_heuristic_injection.py` — 17 tests covering all pattern categories, compliance_mode enforcement, LLM fallback behavior
- **Updated:** `injection.py` — `compliance_mode` param: True → always use heuristic; LLM failure → heuristic fallback (upgraded from score=0)
- **Updated:** `risk.py` — `compliance_mode` param: True → heuristic-only, LLM never called
- **Updated:** `client.py` — `compliance_mode` propagated to both scorers; `from_env()` reads `AGENTGATE_COMPLIANCE_MODE`; `from_dict()` accepts `compliance_mode`
- **Updated:** `audit.py` — added `get_decision_count()`, `get_failed_open_count()`, `get_by_outcome()`, `get_by_agent()` for usage tracking and health checks
- **Updated:** `api/main.py` — added `GET /health/detailed` (component-level status + today's metrics) and `GET /usage` (decision counts for billing)
- **Updated:** `dashboard/index.html` — system status bar at top of page (green/yellow/red); fetches `/health/detailed` every 5s; shows compliance mode note when active
- **Updated:** `README.md` — "When do you need this" urgency hook section added before "The Problem"
- **Updated:** `examples/show_hn_fintech.md` — urgency opening paragraph added
- **Updated:** `examples/fintech_agent_demo.py` — reads `AGENTGATE_COMPLIANCE_MODE` env var; passes `compliance_mode` to `GatewayClient`; skips API key check in compliance mode
- **Tests:** 17 new tests in `test_heuristic_injection.py`; 98/98 passing (up from 81)
- **Benchmark:** p50=6ms / p95=127ms / p99=131ms; policy-only decisions (no LLM): p50=2.3ms / p95=5.7ms

### 2026-04-13 (v0.2.0 — Fintech Runtime Security)
- **New:** `blast_radius.py` — BlastRadiusEstimator: synchronous heuristic, financial_impact + reversibility + severity + regulatory_flags (PCI-DSS, GDPR, SOX, AML); never raises; runs before every decision
- **New:** `pii_detector.py` — PiiDetector: 6 regex patterns (credit_card, ssn, email, phone_us, iban, routing_number) + LLM confirmation; `scan()` + `redact()`; fails open
- **New:** `docs/DECISION_PRECEDENCE.md` — canonical decision evaluation order, fail behaviors, idempotency key semantics
- **New:** `examples/fintech_agent_demo.py` — 7-scenario payment agent demo (2 allowed, 1 escalated, 4 blocked)
- **New:** `examples/policies/fintech_payments.yaml` — 15-rule fintech payment policy
- **New:** `examples/show_hn_fintech.md` — fintech-focused Show HN draft
- **New:** `examples/FINTECH_DEMO_SCRIPT.md` — narrated demo script for investor/partner calls
- **New:** `POST /scan/output` API endpoint — PII scan on agent output, logs to pii_scan_log
- **New:** `tests/test_blast_radius.py` (20 tests), `tests/test_injection.py` (10 tests), `tests/test_pii.py` (17 tests), `tests/test_integration.py` (12 tests)
- **Updated:** `models.py` — `ToolCall` + `idempotency_key`; `Decision` + `attack_type` + `blast_radius`
- **Updated:** `client.py` — blast_radius runs before policy check; `_run_injection_only()` for policy-blocked decisions; `scan_output()` method; explicit-allow shortcut removed (injection can override policy ALLOW); `_parse_attack_type()` helper; `BlastRadiusEstimator` + `PiiDetector` wired in constructor; critical blast_radius forces escalation
- **Updated:** `injection.py` — added `excessive_agency` attack type; updated LLM prompt to detect disproportionate actions
- **Updated:** `audit.py` — new columns: `idempotency_key`, `attack_type`, `blast_radius`; `pii_scan_log` table; `log_pii_scan()` method; auto-migration for new columns
- **Updated:** `dashboard/index.html` — attack type badges (INJECTION, DATA LEAK, PRIV ESC, EXCESS AGENCY); blast radius chips (financial impact, reversibility dot, regulatory flag pills)
- **Updated:** `README.md` — fintech section with threat table (4 failure modes)
- **Tests:** 59 new tests across 4 new test files; 81/81 passing (up from 22)

### 2026-04-13 (v0.1.0)
- **New:** `injection.py` — InjectionScorer, LLM-based prompt injection detection with attack_type classification
- **New:** `session.py` — SessionTracker, records per-agent call history
- **New:** `anomaly.py` — AnomalyScorer, velocity + scope-drift detection (pure Python)
- **New:** `dashboard/index.html` — single-file dark-theme dashboard with WebSocket live feed
- **New:** `GET /dashboard/stats` — aggregate stats for dashboard metric cards
- **WS:** `/ws/feed` — real-time audit log stream (initial batch + polling)
- **New:** `GET /audit` — paginated, filterable audit log endpoint
- **New:** `GET /audit/export` — full CSV compliance export
- **New:** `_ApiKeyMiddleware` — optional `X-API-Key` auth on all endpoints
- **New:** `PolicyLoader.start_watching()` — hot-reload on YAML change via watchdog
- **New:** `Dockerfile` + `docker-compose.yml` — container deployment
- **New:** `README.md`, `ARCHITECTURE.md`, `examples/show_hn_draft.md`
- **New:** `examples/policies/fintech.yaml`, `healthcare.yaml`
- **Updated:** `Decision` dataclass — added `risk_reason`, `injection_score`, `injection_reason`, `anomaly_score`, `anomaly_reason`, `human_decision`, `human_reason`
- **Updated:** `ToolCall` dataclass — added `original_task` (injection detection), `session_id` (anomaly grouping)
- **Updated:** `audit_log` table — added all new Decision + ToolCall fields; auto-migrates existing DBs
- **Updated:** `client.py` — `asyncio.gather` now runs Risk + Injection + Anomaly in parallel
- **Updated:** `pyproject.toml` — added `watchdog`, `websockets` dependencies
- **Updated:** `.env.example` — added all anomaly + API key variables
- **Tests:** 6 new anomaly tests; all scorers now return `(score, reason)` tuples; 22/22 passing

### 2026-04-10
- **Fix:** Policy check now returns immediately on BLOCK without calling risk scorer
- **Fix:** 200ms timeout now applies only to risk scoring, not escalation wait
- **Fix:** Default `timeout_ms` raised from 200ms → 5000ms
- **Fix:** Default `ESCALATE_THRESHOLD` raised from 30 → 50
- **Fix:** Policy ordering in `customer_support.yaml` — block before escalate
- **New:** `examples/demo_agent.py` — 6-scenario runnable demo
