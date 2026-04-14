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
    latency_ms: float = 0.0
    decided_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_allowed(self) -> bool:
        return self.outcome in (
            DecisionOutcome.ALLOWED,
            DecisionOutcome.ESCALATION_APPROVED,
            DecisionOutcome.FAILED_OPEN,
        )
