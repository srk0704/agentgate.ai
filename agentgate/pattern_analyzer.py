"""
AgentGate — Pattern Analyzer
Mines audit_log + output_log to find actionable improvement patterns.
"""
from __future__ import annotations
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import uuid4

import aiosqlite

from agentgate.audit import AuditLogger
from agentgate.output_logger import OutputLogger

logger = logging.getLogger(__name__)


class PatternType(str, Enum):
    OVER_ESCALATION = "over_escalation"
    REPEATED_BLOCK = "repeated_block"
    FALSE_POSITIVE = "false_positive"
    THRESHOLD_TOO_LOW = "threshold_too_low"
    PROMPT_IMPROVEMENT = "prompt_improvement"
    POLICY_DRIFT = "policy_drift"


@dataclass
class Pattern:
    id: str
    pattern_type: PatternType
    tool_name: str
    description: str
    evidence: dict
    suggestion: str
    suggested_action: dict
    confidence: float
    impact: str          # "high" | "medium" | "low"
    auto_applicable: bool
    created_at: str


def _confidence_from_n(n: int) -> float:
    """
    Confidence grows with sample size: min(0.95, 1 - 1/sqrt(n)).
    n=3→0.42, n=5→0.55, n=10→0.68, n=25→0.80, n=50→0.86, n=100→0.90, n=400→0.95
    """
    if n <= 0:
        return 0.0
    return min(0.95, 1 - 1 / math.sqrt(n))


class PatternAnalyzer:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._audit = AuditLogger(db_path)
        self._output = OutputLogger(db_path)

    async def analyze(
        self,
        lookback_hours: int = 168,
        policies: list[dict] | None = None,
    ) -> list[Pattern]:
        """
        Run all detectors. Pass `policies` (list of raw policy dicts) for
        data-derived threshold suggestions; falls back to defaults without it.
        """
        all_patterns: list[Pattern] = []

        detectors = [
            self._detect_over_escalation(lookback_hours, policies),
            self._detect_threshold_too_low(lookback_hours),
            self._detect_repeated_blocks(lookback_hours),
            self._detect_false_positives(lookback_hours),
            self._detect_prompt_improvements(lookback_hours),
            self._detect_policy_drift(lookback_hours),
            self._detect_drift_patterns(lookback_hours),
            self._detect_loop_patterns(lookback_hours),
        ]

        for coro in detectors:
            try:
                results = await coro
                all_patterns.extend(results)
            except Exception as e:
                logger.warning("Pattern detector error: %s", e)

        impact_order = {"high": 0, "medium": 1, "low": 2}
        all_patterns.sort(key=lambda p: (impact_order.get(p.impact, 3), -p.confidence))
        return all_patterns

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_escalate_threshold(
        self, tool_name: str, policies: list[dict] | None
    ) -> tuple[float, str]:
        """
        Return (current_value, condition_field) for the escalate rule on tool_name.
        Falls back to (40.0, "args.amount") if policies not provided or no match.
        """
        if not policies:
            return 40.0, "args.amount"
        for policy in policies:
            if (
                policy.get("match", {}).get("tool") == tool_name
                and policy.get("effect") == "escalate"
            ):
                for cond in policy.get("conditions", []):
                    if cond.get("op") in ("gte", "gt", "lte", "lt"):
                        return float(cond.get("value", 40)), cond.get("field", "args.amount")
        return 40.0, "args.amount"

    def _has_unconditional_allow(self, tool_name: str, policies: list[dict] | None) -> bool:
        """True if the tool has an explicit allow policy with no conditions."""
        if not policies:
            return False
        for policy in policies:
            if (
                policy.get("match", {}).get("tool") == tool_name
                and policy.get("effect") == "allow"
                and not policy.get("conditions")
            ):
                return True
        return False

    async def _compute_p90_threshold(
        self, tool_name: str, condition_field: str, lookback_hours: int
    ) -> float | None:
        """
        Compute the 90th percentile of `condition_field` among approved escalations
        for `tool_name`. Rounds up to a clean breakpoint.
        Handles only args.* fields (SQLite json_extract).
        """
        parts = condition_field.split(".")
        if len(parts) != 2 or parts[0] != "args":
            return None
        json_path = f"$.{parts[1]}"

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"""SELECT CAST(json_extract(args, ?) AS REAL) AS val
                    FROM audit_log
                    WHERE tool_name = ?
                      AND human_decision = 'approved'
                      AND decided_at > datetime('now', '-{lookback_hours} hours')
                      AND json_extract(args, ?) IS NOT NULL
                    ORDER BY val ASC""",
                (json_path, tool_name, json_path),
            ) as cur:
                rows = await cur.fetchall()

        values = [r[0] for r in rows if r[0] is not None]
        if not values:
            return None

        idx = int(len(values) * 0.90)
        p90 = values[min(idx, len(values) - 1)]

        # Round up to the nearest clean breakpoint
        if p90 < 100:
            return float(math.ceil(p90 / 25) * 25)
        elif p90 < 1000:
            return float(math.ceil(p90 / 50) * 50)
        else:
            return float(math.ceil(p90 / 500) * 500)

    # ------------------------------------------------------------------
    # Detectors
    # ------------------------------------------------------------------

    async def _detect_over_escalation(
        self, lookback_hours: int, policies: list[dict] | None = None
    ) -> list[Pattern]:
        """
        Tools escalated >= 3 times where approval_rate > 0.5 OR avg_risk < 60.
        Confidence derived from sample size. Threshold derived from p90 of approved amounts.
        """
        patterns: list[Pattern] = []
        await self._audit._ensure_init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""SELECT tool_name,
                           COUNT(*) as total,
                           SUM(CASE WHEN human_decision='approved' THEN 1 ELSE 0 END) as approved,
                           AVG(CASE WHEN risk_score IS NOT NULL THEN risk_score ELSE 0 END) as avg_risk
                    FROM audit_log
                    WHERE outcome IN ('escalated','escalation_approved','escalation_rejected')
                      AND decided_at > datetime('now', '-{lookback_hours} hours')
                    GROUP BY tool_name
                    HAVING total >= 2"""
            ) as cur:
                rows = await cur.fetchall()

        for row in rows:
            tool_name = row["tool_name"]
            total = row["total"]
            approved = row["approved"] or 0
            avg_risk = row["avg_risk"] or 0

            # Skip tools with unconditional allow policies — their escalations come from
            # risk/anomaly scoring, not a policy threshold we can tune here.
            if self._has_unconditional_allow(tool_name, policies):
                continue

            approval_rate = approved / total if total > 0 else 0
            pct = round(approval_rate * 100)

            if approval_rate >= 0.5 or avg_risk < 60:
                confidence = _confidence_from_n(total)
                impact = "high" if approval_rate > 0.8 else "medium"

                current_value, condition_field = self._find_escalate_threshold(tool_name, policies)
                suggested_value = await self._compute_p90_threshold(
                    tool_name, condition_field, lookback_hours
                )
                if suggested_value is None or suggested_value <= current_value:
                    suggested_value = current_value * 2  # fallback: double the threshold

                patterns.append(Pattern(
                    id=str(uuid4()),
                    pattern_type=PatternType.OVER_ESCALATION,
                    tool_name=tool_name,
                    description=(
                        f"{tool_name} was escalated {total} times; "
                        f"{pct}% were approved by humans (avg risk {avg_risk:.0f}/100). "
                        f"Threshold appears too conservative."
                    ),
                    evidence={
                        "total_escalations": total,
                        "approved_count": approved,
                        "approval_rate": round(approval_rate, 3),
                        "avg_risk_score": round(avg_risk, 1),
                        "lookback_hours": lookback_hours,
                    },
                    suggestion=(
                        f"Raise the escalation threshold for {tool_name} from "
                        f"${current_value:.0f} to ${suggested_value:.0f} "
                        f"(p90 of approved amounts). "
                        f"{pct}% of escalations were low-risk approvals."
                    ),
                    suggested_action={
                        "action": "raise_threshold",
                        "tool_name": tool_name,
                        "condition_field": condition_field,
                        "current_value": current_value,
                        "suggested_value": suggested_value,
                        "reason": f"{pct}% approved (n={total}, confidence={confidence:.2f})",
                    },
                    confidence=confidence,
                    impact=impact,
                    auto_applicable=True,
                    created_at=datetime.utcnow().isoformat(),
                ))

        return patterns

    async def _detect_threshold_too_low(self, lookback_hours: int) -> list[Pattern]:
        """
        Escalations decided in under 30s — suggests the timeout is too short.
        """
        patterns: list[Pattern] = []
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    f"""SELECT a.tool_name, COUNT(*) as quick_decisions
                        FROM escalations e
                        JOIN audit_log a ON a.escalation_id = e.id
                        WHERE e.decided_at IS NOT NULL
                          AND (julianday(e.decided_at) - julianday(e.created_at)) * 86400 < 30
                          AND e.created_at > datetime('now', '-{lookback_hours} hours')
                        GROUP BY a.tool_name
                        HAVING quick_decisions > 2"""
                ) as cur:
                    rows = await cur.fetchall()

            for row in rows:
                tool_name = row["tool_name"]
                count = row["quick_decisions"]
                confidence = _confidence_from_n(count)
                patterns.append(Pattern(
                    id=str(uuid4()),
                    pattern_type=PatternType.THRESHOLD_TOO_LOW,
                    tool_name=tool_name,
                    description=(
                        f"{tool_name} had {count} escalations decided in under 30 seconds. "
                        f"Humans may not have had adequate time to review."
                    ),
                    evidence={"quick_decision_count": count, "lookback_hours": lookback_hours},
                    suggestion=f"Increase the escalation review window for {tool_name} to at least 5 minutes.",
                    suggested_action={
                        "action": "increase_timeout",
                        "tool_name": tool_name,
                        "current_timeout_sec": 60,
                        "suggested_timeout_sec": 300,
                    },
                    confidence=confidence,
                    impact="low",
                    auto_applicable=True,
                    created_at=datetime.utcnow().isoformat(),
                ))
        except Exception as e:
            logger.debug("_detect_threshold_too_low error: %s", e)

        return patterns

    async def _detect_repeated_blocks(self, lookback_hours: int) -> list[Pattern]:
        """
        Same tool+policy blocked >= 5 times. Confidence from sample size.
        """
        patterns: list[Pattern] = []
        await self._audit._ensure_init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""SELECT tool_name, policy_matched, COUNT(*) as block_count
                    FROM audit_log
                    WHERE outcome = 'blocked'
                      AND decided_at > datetime('now', '-{lookback_hours} hours')
                    GROUP BY tool_name, policy_matched
                    HAVING block_count >= 5"""
            ) as cur:
                rows = await cur.fetchall()

        for row in rows:
            tool_name = row["tool_name"]
            policy = row["policy_matched"] or "unknown"
            count = row["block_count"]
            confidence = _confidence_from_n(count)
            patterns.append(Pattern(
                id=str(uuid4()),
                pattern_type=PatternType.REPEATED_BLOCK,
                tool_name=tool_name,
                description=(
                    f"{tool_name} was blocked {count} times by policy '{policy}'. "
                    f"May indicate systematic agent misbehavior or an overly strict policy."
                ),
                evidence={
                    "block_count": count,
                    "policy_matched": policy,
                    "lookback_hours": lookback_hours,
                },
                suggestion=f"Review why {tool_name} is repeatedly hitting policy '{policy}' and clarify agent instructions.",
                suggested_action={
                    "action": "add_policy_rule",
                    "tool_name": tool_name,
                    "effect": "block",
                    "reason": f"Repeated blocks ({count}x) by {policy}",
                },
                confidence=confidence,
                impact="medium",
                auto_applicable=False,
                created_at=datetime.utcnow().isoformat(),
            ))

        return patterns

    async def _detect_false_positives(self, lookback_hours: int) -> list[Pattern]:
        """
        Block followed by allowed call for the same tool within 2 minutes.
        """
        patterns: list[Pattern] = []
        await self._audit._ensure_init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""SELECT a1.tool_name, COUNT(*) as fp_count
                    FROM audit_log a1
                    JOIN audit_log a2
                      ON a1.tool_name = a2.tool_name
                     AND a1.agent_id = a2.agent_id
                     AND a2.outcome = 'allowed'
                     AND (julianday(a2.decided_at) - julianday(a1.decided_at)) * 1440 < 2
                     AND (julianday(a2.decided_at) - julianday(a1.decided_at)) * 1440 > 0
                    WHERE a1.outcome = 'blocked'
                      AND a1.decided_at > datetime('now', '-{lookback_hours} hours')
                    GROUP BY a1.tool_name
                    HAVING fp_count > 2"""
            ) as cur:
                rows = await cur.fetchall()

        for row in rows:
            tool_name = row["tool_name"]
            count = row["fp_count"]
            confidence = _confidence_from_n(count)
            patterns.append(Pattern(
                id=str(uuid4()),
                pattern_type=PatternType.FALSE_POSITIVE,
                tool_name=tool_name,
                description=(
                    f"{tool_name} was blocked and then allowed for the same tool within 2 minutes "
                    f"on {count} occasions — possible false positive blocks."
                ),
                evidence={"false_positive_count": count, "lookback_hours": lookback_hours},
                suggestion=f"Review {tool_name} policy conditions to tighten the block criteria.",
                suggested_action={
                    "action": "review_policy",
                    "tool_name": tool_name,
                    "reason": f"{count} possible false positives detected (confidence={confidence:.2f})",
                },
                confidence=confidence,
                impact="medium",
                auto_applicable=False,
                created_at=datetime.utcnow().isoformat(),
            ))

        return patterns

    async def _detect_prompt_improvements(self, lookback_hours: int) -> list[Pattern]:
        """
        Sessions where issue_refund was called without a prerequisite lookup.
        """
        patterns: list[Pattern] = []
        await self._audit._ensure_init()

        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    f"""SELECT DISTINCT session_id
                        FROM audit_log
                        WHERE tool_name = 'issue_refund'
                          AND session_id IS NOT NULL
                          AND decided_at > datetime('now', '-{lookback_hours} hours')"""
                ) as cur:
                    refund_sessions = [r["session_id"] for r in await cur.fetchall()]

            if not refund_sessions:
                return patterns

            bad_sessions = 0
            for sid in refund_sessions:
                async with aiosqlite.connect(self.db_path) as db:
                    async with db.execute(
                        """SELECT COUNT(*) FROM audit_log
                           WHERE session_id = ?
                             AND tool_name IN ('get_transaction', 'get_customer_info',
                                               'get_customer_transactions')""",
                        (sid,),
                    ) as cur:
                        row = await cur.fetchone()
                        if not row or row[0] == 0:
                            bad_sessions += 1

            total_sessions = len(refund_sessions)
            if bad_sessions > 2:
                confidence = _confidence_from_n(total_sessions)
                patterns.append(Pattern(
                    id=str(uuid4()),
                    pattern_type=PatternType.PROMPT_IMPROVEMENT,
                    tool_name="issue_refund",
                    description=(
                        f"In {bad_sessions}/{total_sessions} sessions, issue_refund was called "
                        f"without a prior transaction or customer lookup. "
                        f"Agent needs clearer prerequisites."
                    ),
                    evidence={
                        "sessions_without_lookup": bad_sessions,
                        "total_refund_sessions": total_sessions,
                        "lookback_hours": lookback_hours,
                    },
                    suggestion=(
                        "Add system prompt instruction: 'Always call get_transaction or "
                        "get_customer_info before issuing a refund to verify the transaction.'"
                    ),
                    suggested_action={
                        "action": "add_prompt_instruction",
                        "instruction": (
                            "IMPORTANT: Always call get_transaction or get_customer_info "
                            "BEFORE issuing any refund to verify transaction details."
                        ),
                    },
                    confidence=confidence,
                    impact="medium",
                    auto_applicable=True,
                    created_at=datetime.utcnow().isoformat(),
                ))
        except Exception as e:
            logger.debug("_detect_prompt_improvements error: %s", e)

        return patterns

    async def _detect_policy_drift(self, lookback_hours: int) -> list[Pattern]:
        """
        Look at policy_changes applied > 24h ago that have metrics_after populated.
        If block_rate increased by > 10pp after raising a threshold, the change
        may have been too aggressive — suggest reverting.
        """
        patterns: list[Pattern] = []
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """SELECT id, tool_name, action, before_value, after_value,
                              metrics_before, metrics_after, applied_at
                       FROM policy_changes
                       WHERE action = 'raise_threshold'
                         AND applied_at < datetime('now', '-24 hours')
                         AND metrics_after IS NOT NULL
                         AND reverted_at IS NULL"""
                ) as cur:
                    changes = [dict(r) for r in await cur.fetchall()]
        except Exception:
            return patterns

        for change in changes:
            try:
                before = json.loads(change["metrics_before"]) if change["metrics_before"] else {}
                after = json.loads(change["metrics_after"]) if change["metrics_after"] else {}
            except Exception:
                continue

            before_block = before.get("block_rate", 0)
            after_block = after.get("block_rate", 0)
            before_esc = before.get("escalation_rate", 0)
            after_esc = after.get("escalation_rate", 0)
            n_after = after.get("total", 0)

            # Block rate rose > 10pp after raising threshold (threshold went too far)
            if (after_block - before_block) > 10 and n_after >= 5:
                confidence = _confidence_from_n(n_after)
                patterns.append(Pattern(
                    id=str(uuid4()),
                    pattern_type=PatternType.POLICY_DRIFT,
                    tool_name=change["tool_name"],
                    description=(
                        f"After raising the {change['tool_name']} threshold from "
                        f"${change['before_value']} to ${change['after_value']}, "
                        f"block rate increased from {before_block:.0f}% to {after_block:.0f}% "
                        f"(+{after_block - before_block:.0f}pp). "
                        f"The threshold may have been raised too high."
                    ),
                    evidence={
                        "change_id": change["id"],
                        "before_value": change["before_value"],
                        "after_value": change["after_value"],
                        "block_rate_before": before_block,
                        "block_rate_after": after_block,
                        "escalation_rate_before": before_esc,
                        "escalation_rate_after": after_esc,
                        "n_after": n_after,
                    },
                    suggestion=(
                        f"Consider reverting the {change['tool_name']} threshold to "
                        f"${change['before_value']} or a midpoint between "
                        f"${change['before_value']} and ${change['after_value']}."
                    ),
                    suggested_action={
                        "action": "revert_threshold",
                        "change_id": change["id"],
                        "tool_name": change["tool_name"],
                        "revert_to": change["before_value"],
                        "current_value": change["after_value"],
                    },
                    confidence=confidence,
                    impact="high",
                    auto_applicable=False,
                    created_at=datetime.utcnow().isoformat(),
                ))

        return patterns

    # ── New: Drift / Loop pattern detection ────────────────────────────────

    async def _detect_drift_patterns(self, lookback_hours: int) -> list[Pattern]:
        """Find agents with consistent goal drift — system-prompt fix is suggested."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT agent_id,
                          COUNT(*) AS count,
                          AVG(drift_score) AS avg_drift,
                          MAX(drift_reason) AS common_reason
                   FROM audit_log
                   WHERE drift_score > 50
                     AND decided_at > datetime('now', ?)
                   GROUP BY agent_id
                   HAVING count >= 3""",
                (f"-{lookback_hours} hours",),
            ) as cur:
                rows = await cur.fetchall()

        patterns: list[Pattern] = []
        for row in rows:
            count = row["count"]
            agent_id = row["agent_id"]
            avg_drift = round(row["avg_drift"] or 0, 1)
            common = row["common_reason"] or "unknown drift"
            patterns.append(Pattern(
                id=str(uuid4()),
                pattern_type=PatternType.PROMPT_IMPROVEMENT,
                tool_name=agent_id,
                description=(
                    f"Agent {agent_id} shows consistent goal drift: "
                    f"drift_score > 50 in {count} recent calls (avg {avg_drift}). "
                    f"Most common: {common}. Adding explicit task boundaries to the "
                    f"system prompt will reduce drift."
                ),
                evidence={
                    "agent_id": agent_id,
                    "count": count,
                    "avg_drift": avg_drift,
                    "common_reason": common,
                },
                suggestion="Add explicit task boundary instruction to agent system prompt.",
                suggested_action={
                    "action": "add_prompt_instruction",
                    "instruction": (
                        "Only use tools directly relevant to the user's stated request. "
                        "Do not expand scope beyond what was explicitly asked. "
                        "If asked to look up an account, do not export data."
                    ),
                },
                confidence=_confidence_from_n(count),
                impact="high",
                auto_applicable=True,
                created_at=datetime.utcnow().isoformat(),
            ))
        return patterns

    async def _detect_loop_patterns(self, lookback_hours: int) -> list[Pattern]:
        """Find tools that frequently cause retry storms — likely unreliable."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT tool_name, COUNT(*) AS count
                   FROM audit_log
                   WHERE loop_score > 70
                     AND decided_at > datetime('now', ?)
                   GROUP BY tool_name
                   HAVING count >= 3""",
                (f"-{lookback_hours} hours",),
            ) as cur:
                rows = await cur.fetchall()

        patterns: list[Pattern] = []
        days = max(lookback_hours // 24, 1)
        for row in rows:
            count = row["count"]
            tool = row["tool_name"]
            patterns.append(Pattern(
                id=str(uuid4()),
                pattern_type=PatternType.REPEATED_BLOCK,
                tool_name=tool,
                description=(
                    f"{tool} triggered retry storm detection {count} times in the "
                    f"last {days} day(s). The tool may be unreliable or the agent "
                    f"needs explicit error handling guidance."
                ),
                evidence={"tool_name": tool, "count": count},
                suggestion=(
                    f"Review {tool} reliability and add error handling instructions "
                    f"to agent prompt."
                ),
                suggested_action={
                    "action": "add_prompt_instruction",
                    "instruction": (
                        f"If {tool} fails or returns an error, do not retry more than once. "
                        f"Inform the user that the service is temporarily unavailable and stop."
                    ),
                },
                confidence=_confidence_from_n(count),
                impact="medium",
                auto_applicable=False,
                created_at=datetime.utcnow().isoformat(),
            ))
        return patterns
