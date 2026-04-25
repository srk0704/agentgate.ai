from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
import uuid


class Effect(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class DecisionOutcome(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    ESCALATION_APPROVED = "escalation_approved"
    ESCALATION_REJECTED = "escalation_rejected"
    FAILED_OPEN = "failed_open"  # gateway error, allowed by default


@dataclass
class ToolCall:
    """Represents an agent's intent to call a tool."""
    tool_name: str
    args: dict[str, Any]
    agent_id: str
    context: dict[str, Any] = field(default_factory=dict)
    # The original user request that triggered this agent session.
    # Required for prompt injection detection — captures the "why" behind the action.
    original_task: str | None = None
    # Groups all tool calls from one agent run — used for session-level analysis.
    session_id: str | None = None
    # Stable caller-supplied key for deduplication on retry (e.g. "refund-txn-123").
    # AgentGate logs it but does not enforce idempotency.
    idempotency_key: str | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Decision:
    """The gateway's verdict on a ToolCall."""
    outcome: DecisionOutcome
    tool_call: ToolCall
    reason: str
    risk_score: int | None = None
    risk_reason: str | None = None          # why the risk scorer gave that score
    injection_score: int | None = None      # 0-100; high = likely prompt injection
    injection_reason: str | None = None     # explanation from the injection scorer
    anomaly_score: int | None = None        # 0-100; high = unusual session behavior
    anomaly_reason: str | None = None       # explanation from the anomaly scorer
    human_decision: str | None = None       # "approved" / "rejected" — set after escalation
    human_reason: str | None = None         # reviewer's explanation — becomes training data
    policy_matched: str | None = None
    escalation_id: str | None = None
    # Parsed from injection_reason: goal_hijacking | data_exfiltration |
    # privilege_escalation | excessive_agency | other | None
    attack_type: str | None = None
    # Blast radius estimate from BlastRadiusEstimator (always present, never None after eval)
    blast_radius: dict | None = None
    # Unified 0-100 reliability score for this call. Higher = healthier.
    # Inverted from component scores (where higher = worse) and summarized in plain English.
    reliability_score: int | None = None
    reliability_summary: str | None = None
    latency_ms: float = 0.0
    decided_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_allowed(self) -> bool:
        return self.outcome in (
            DecisionOutcome.ALLOWED,
            DecisionOutcome.ESCALATION_APPROVED,
            DecisionOutcome.FAILED_OPEN,
        )

    @staticmethod
    def compute_reliability_score(
        risk_score: int | None,
        injection_score: int | None,
        anomaly_score: int | None,
        drift_score: int | None = None,
        loop_score: int | None = None,
    ) -> tuple[int, str]:
        """
        Compute unified reliability score 0-100. Higher = healthier.

        Algorithm: collect all non-None component scores (where higher = worse),
        find the worst, invert it to 100 - worst. Map to a plain-English summary band:
          90-100 → Healthy
          70-89  → Caution: elevated <name>
          40-69  → Degraded: high <name> score
          0-39   → Critical: <name> detected
        """
        scores = {
            "injection": injection_score,
            "risk": risk_score,
            "anomaly": anomaly_score,
            "drift": drift_score,
            "loop": loop_score,
        }
        active = {k: v for k, v in scores.items() if v is not None and v > 0}

        if not active:
            return (100, "Healthy")

        worst_name = max(active, key=lambda k: active[k])
        worst_score = active[worst_name]
        reliability = max(0, 100 - worst_score)

        if reliability >= 90:
            summary = "Healthy"
        elif reliability >= 70:
            summary = f"Caution: elevated {worst_name}"
        elif reliability >= 40:
            summary = f"Degraded: high {worst_name} score"
        else:
            summary = f"Critical: {worst_name} detected"

        return (reliability, summary)
