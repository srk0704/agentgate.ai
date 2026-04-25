from __future__ import annotations
import logging
import os

from agentgate.models import ToolCall
from agentgate.session import SessionTracker

logger = logging.getLogger(__name__)

# Tools that are always expected to be called in any session — never velocity-flagged.
_BENIGN_TOOLS = frozenset({
    "get_user", "list_users", "read_file", "get_config",
    "list_orders", "get_order", "search",
    # payment / fintech read-only tools
    "get_customer_info", "get_customer", "get_transaction",
    "get_customer_transactions", "check_fraud_flags",
})


class AnomalyScorer:
    """
    Detects unusual session-level behavior without calling an LLM.

    Two signals:
    - velocity_score: same tool called > N times in 60 seconds.
    - scope_drift_score: agent calling many unrelated tools, suggesting
      the session has drifted away from its stated purpose.

    anomaly_score = max(velocity_score, scope_drift_score)

    Returns (anomaly_score: int 0-100, reason: str).
    """

    def __init__(self, session_tracker: SessionTracker):
        self._tracker = session_tracker
        # Max same-tool calls allowed per window before velocity score fires.
        # Default 5: aggressive for most legitimate agent workflows — a payment
        # agent should not call issue_refund 5 times per minute. Modeled on
        # Stripe's per-minute rate-limit defaults and standard payment-fraud
        # velocity rules. See docs/THRESHOLD_RESEARCH.md.
        # Raise for batch agents that legitimately fan out a single tool.
        # Lower if you observe runaway retry loops that escape detection.
        self._velocity_threshold = int(os.getenv("AGENTGATE_ANOMALY_VELOCITY_THRESHOLD", "5"))

        # Time window (seconds) over which velocity is measured.
        # Default 60: matches the canonical "calls per minute" framing used in
        # payment-fraud and API rate-limit literature.
        self._velocity_window_sec = int(os.getenv("AGENTGATE_ANOMALY_VELOCITY_WINDOW_SEC", "60"))

    async def score(self, tool_call: ToolCall) -> tuple[int, str]:
        """
        Record the call, then compute anomaly score.
        Never raises — returns (0, "ok") on any error.
        """
        try:
            await self._tracker.record(tool_call)
            return await self._compute(tool_call)
        except Exception as e:
            logger.warning("AnomalyScorer error: %s — skipping", e)
            return 0, "scorer unavailable"

    async def _compute(self, tool_call: ToolCall) -> tuple[int, str]:
        stats = await self._tracker.get_session_stats(
            agent_id=tool_call.agent_id,
            window_minutes=max(self._velocity_window_sec // 60, 5),
            session_id=tool_call.session_id,
        )

        velocity_score, velocity_reason = self._velocity_score(
            stats["calls_last_60s"],
            tool_call.tool_name,
            stats["tool_frequency"],
            self._velocity_threshold,
        )

        scope_score, scope_reason = self._scope_drift_score(
            stats["unique_tools"],
            stats["call_count"],
            tool_call.original_task,
        )

        if velocity_score >= scope_score:
            return velocity_score, velocity_reason
        return scope_score, scope_reason

    def _velocity_score(
        self,
        calls_60s: int,
        tool_name: str,
        tool_freq: dict[str, int],
        threshold: int,
    ) -> tuple[int, str]:
        """Flag if the same tool is called more than threshold times in 60 seconds."""
        if tool_name in _BENIGN_TOOLS:
            return 0, "benign read-only tool — velocity not scored"
        same_tool_count = tool_freq.get(tool_name, 0)

        if same_tool_count > threshold * 2:
            score = min(95, 60 + (same_tool_count - threshold) * 5)
            return score, (
                f"velocity: '{tool_name}' called {same_tool_count} times — "
                f"threshold {threshold}/min"
            )
        if same_tool_count > threshold:
            score = min(75, 40 + (same_tool_count - threshold) * 5)
            return score, (
                f"velocity: '{tool_name}' called {same_tool_count} times in window "
                f"(threshold {threshold})"
            )
        if calls_60s > threshold:
            score = min(70, 30 + (calls_60s - threshold) * 4)
            return score, (
                f"velocity: {calls_60s} calls in last 60s across all tools "
                f"(threshold {threshold})"
            )
        return 0, "velocity normal"

    def _scope_drift_score(
        self,
        unique_tools: int,
        call_count: int,
        original_task: str | None,
    ) -> tuple[int, str]:
        """
        Flag if an agent is calling many different tools relative to the session size.
        High tool diversity in a short session suggests scope creep or hijacking.
        """
        if call_count < 3:
            return 0, "too few calls to assess scope"

        diversity_ratio = unique_tools / call_count

        # High diversity in a small session = suspicious
        if unique_tools >= 8 and call_count <= 15:
            return 65, (
                f"scope drift: {unique_tools} unique tools in {call_count} calls — "
                "unusually broad tool usage"
            )
        if unique_tools >= 6 and call_count <= 10:
            return 50, (
                f"scope drift: {unique_tools} unique tools in {call_count} calls"
            )
        if diversity_ratio > 0.9 and call_count >= 5:
            return 40, (
                f"scope drift: {unique_tools}/{call_count} unique tools ratio {diversity_ratio:.1f} "
                "— agent exploring broadly"
            )
        return 0, "scope normal"
