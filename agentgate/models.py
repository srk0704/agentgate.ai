from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    # Context drift: did the agent move away from the user's original task?
    drift_score: int | None = None
    drift_reason: str | None = None
    # Retry storms / sequence loops detected from session_calls + output_log.
    loop_score: int | None = None
    loop_reason: str | None = None
    # Unified 0-100 reliability score for this call. Higher = healthier.
    # Inverted from component scores (where higher = worse) and summarized in plain English.
    reliability_score: int | None = None
    reliability_summary: str | None = None
    latency_ms: float = 0.0
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

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
        find the worst, invert it to 100 - worst.

        The summary is a JSON string with four dimensions — overall, safety,
        consistency, caution — each carrying a 0-100 score and a plain-English
        label. The "overall" score equals the returned integer score.
          90-100 → Healthy band
          70-89  → Caution band
          40-69  → Degraded band
          0-39   → Critical band
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
            return (100, json.dumps({
                "overall": {"score": 100,
                            "label": "Agent is operating reliably"},
                "safety": {"score": 100,
                           "label": "No injection or drift detected"},
                "consistency": {"score": 100,
                                "label": "Behavior is consistent and predictable"},
                "caution": {"score": 100,
                            "label": "Actions are within normal risk bounds"},
            }, separators=(",", ":")))

        worst_name = max(active, key=lambda k: active[k])
        worst_score = active[worst_name]
        reliability = max(0, 100 - worst_score)

        # Dimension 1: Safety — injection + drift (how well is the agent
        # resisting attacks and staying within its task?).
        safety_inputs = {k: v for k, v in active.items()
                         if k in ("injection", "drift")}
        safety_worst = max(safety_inputs.values()) if safety_inputs else 0
        safety_score = max(0, 100 - safety_worst)
        safety_driver = (max(safety_inputs, key=safety_inputs.get)
                         if safety_inputs else "signal")
        if safety_score >= 90:
            safety_label = "No injection or drift detected"
        elif safety_score >= 70:
            safety_label = f"Elevated {safety_driver} signal — monitor closely"
        elif safety_score >= 40:
            safety_label = f"High {safety_driver} score — review recent actions"
        else:
            safety_label = f"Critical {safety_driver} detected — immediate review needed"

        # Dimension 2: Consistency — loop + anomaly (is the agent behaving
        # predictably across calls?).
        consistency_inputs = {k: v for k, v in active.items()
                              if k in ("loop", "anomaly")}
        consistency_worst = (max(consistency_inputs.values())
                             if consistency_inputs else 0)
        consistency_score = max(0, 100 - consistency_worst)
        consistency_driver = (max(consistency_inputs, key=consistency_inputs.get)
                              if consistency_inputs else "signal")
        if consistency_score >= 90:
            consistency_label = "Behavior is consistent and predictable"
        elif consistency_score >= 70:
            consistency_label = f"Slight {consistency_driver} pattern — watch for escalation"
        elif consistency_score >= 40:
            consistency_label = f"Inconsistent behavior detected via {consistency_driver}"
        else:
            consistency_label = f"Severe {consistency_driver} pattern — agent may be stuck"

        # Dimension 3: Caution — risk alone (how risky are the actions being taken?).
        caution_raw = active.get("risk", 0)
        caution_score = max(0, 100 - caution_raw)
        if caution_score >= 90:
            caution_label = "Actions are within normal risk bounds"
        elif caution_score >= 70:
            caution_label = "Moderate risk detected — verify action intent"
        elif caution_score >= 40:
            caution_label = "High-risk action taken — human review recommended"
        else:
            caution_label = "Critical risk level — action may cause significant harm"

        # Dimension 4: Overall — composite of all signals (same as reliability).
        if reliability >= 90:
            overall_label = "Agent is operating reliably"
        elif reliability >= 70:
            overall_label = f"Caution: elevated {worst_name} signal"
        elif reliability >= 40:
            overall_label = f"Degraded: high {worst_name} score detected"
        else:
            overall_label = f"Critical: {worst_name} signal at dangerous level"

        summary = json.dumps({
            "overall": {"score": reliability, "label": overall_label},
            "safety": {"score": safety_score, "label": safety_label},
            "consistency": {"score": consistency_score,
                            "label": consistency_label},
            "caution": {"score": caution_score, "label": caution_label},
        }, separators=(",", ":"))

        return (reliability, summary)
