"""
AgentGate — Learning Engine
Applies patterns to improve agent behavior, persists changes, and measures impact.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

from agentgate.client import GatewayClient
from agentgate.pattern_analyzer import Pattern

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    success: bool
    description: str
    expected_impact: str = ""
    change_id: str | None = None


class LearningEngine:
    def __init__(self, gateway: GatewayClient, db_path: str):
        self.gateway = gateway
        self.db_path = db_path
        self._applied_patterns: list[str] = []
        self._change_ids: list[str] = []
        self._injected_examples: list[dict] = []
        self._prompt_additions: list[str] = []

    # ------------------------------------------------------------------
    # Apply patterns
    # ------------------------------------------------------------------

    async def apply_pattern(self, pattern: Pattern) -> ApplyResult:
        action = pattern.suggested_action.get("action")
        if action == "raise_threshold":
            return await self._raise_threshold(pattern)
        elif action == "add_policy_rule":
            return await self._add_policy_rule(pattern)
        elif action == "add_prompt_instruction":
            return await self._add_prompt_instruction(pattern)
        elif action == "increase_timeout":
            return await self._increase_timeout(pattern)
        return ApplyResult(success=False, description=f"Unknown action: {action}")

    async def _raise_threshold(self, pattern: Pattern) -> ApplyResult:
        """
        Mutate the in-memory policy condition value, capture before-metrics,
        log to policy_changes, and persist to YAML.
        """
        tool_name = pattern.suggested_action.get("tool_name")
        suggested_value = pattern.suggested_action.get("suggested_value")

        if not tool_name or suggested_value is None:
            return ApplyResult(success=False, description="Missing tool_name or suggested_value")

        policies = self.gateway._policy_evaluator._loader._policies
        actual_current_value: float | None = None
        updated = False

        for policy in policies:
            if (
                policy.get("match", {}).get("tool") == tool_name
                and policy.get("effect") == "escalate"
            ):
                for condition in policy.get("conditions", []):
                    if condition.get("op") in ("gte", "gt"):
                        actual_current_value = condition.get("value")
                        condition["value"] = suggested_value
                        updated = True
                        break
            if updated:
                break

        if not updated:
            return ApplyResult(
                success=False,
                description=f"No escalate policy with gte/gt condition found for {tool_name}",
            )

        # Capture metrics from the last 7 days before this change
        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)
        since_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()
        metrics_before = await audit.get_tool_metrics(tool_name, since_7d)

        # Persist to YAML
        loader = self.gateway._policy_evaluator._loader
        loader.save()

        # Log the change
        change_id = await audit.log_policy_change({
            "pattern_id": pattern.id,
            "pattern_type": pattern.pattern_type.value,
            "tool_name": tool_name,
            "action": "raise_threshold",
            "before_value": str(actual_current_value),
            "after_value": str(suggested_value),
            "metrics_before": json.dumps(metrics_before),
        })

        self._applied_patterns.append(pattern.id)
        self._change_ids.append(change_id)

        logger.info(
            "Raised threshold for %s: %s → %s (change_id=%s)",
            tool_name, actual_current_value, suggested_value, change_id,
        )
        return ApplyResult(
            success=True,
            description=(
                f"Raised escalation threshold for {tool_name} from "
                f"${actual_current_value} to ${suggested_value}"
            ),
            expected_impact=pattern.impact,
            change_id=change_id,
        )

    async def _add_policy_rule(self, pattern: Pattern) -> ApplyResult:
        """Append a new policy dict and persist."""
        tool_name = pattern.suggested_action.get("tool_name")
        effect = pattern.suggested_action.get("effect", "block")
        reason = pattern.suggested_action.get("reason", "Auto-added policy rule")

        new_policy = {
            "name": f"auto_{effect}_{tool_name}",
            "match": {"tool": tool_name},
            "effect": effect,
            "reason": reason,
        }
        self.gateway._policy_evaluator._loader._policies.append(new_policy)

        loader = self.gateway._policy_evaluator._loader
        loader.save()

        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)
        since_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()
        metrics_before = await audit.get_tool_metrics(tool_name or "", since_7d)

        change_id = await audit.log_policy_change({
            "pattern_id": pattern.id,
            "pattern_type": pattern.pattern_type.value,
            "tool_name": tool_name or "",
            "action": "add_policy_rule",
            "before_value": None,
            "after_value": json.dumps(new_policy),
            "metrics_before": json.dumps(metrics_before),
        })

        self._applied_patterns.append(pattern.id)
        self._change_ids.append(change_id)

        return ApplyResult(
            success=True,
            description=f"Added {effect} policy rule for {tool_name}",
            expected_impact=pattern.impact,
            change_id=change_id,
        )

    async def _add_prompt_instruction(self, pattern: Pattern) -> ApplyResult:
        instruction = pattern.suggested_action.get("instruction", "")
        if not instruction:
            return ApplyResult(success=False, description="No instruction provided")

        self._prompt_additions.append(instruction)

        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)
        change_id = await audit.log_policy_change({
            "pattern_id": pattern.id,
            "pattern_type": pattern.pattern_type.value,
            "tool_name": pattern.tool_name,
            "action": "add_prompt_instruction",
            "before_value": None,
            "after_value": instruction[:200],
            "metrics_before": None,
        })

        self._applied_patterns.append(pattern.id)
        self._change_ids.append(change_id)

        return ApplyResult(
            success=True,
            description=f"Added prompt instruction: {instruction[:80]}...",
            expected_impact=pattern.impact,
            change_id=change_id,
        )

    async def _increase_timeout(self, pattern: Pattern) -> ApplyResult:
        suggested = pattern.suggested_action.get("suggested_timeout_sec", 300)
        old = self.gateway.escalation_timeout_sec
        self.gateway.escalation_timeout_sec = float(suggested)

        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)
        change_id = await audit.log_policy_change({
            "pattern_id": pattern.id,
            "pattern_type": pattern.pattern_type.value,
            "tool_name": pattern.tool_name,
            "action": "increase_timeout",
            "before_value": str(old),
            "after_value": str(suggested),
            "metrics_before": None,
        })

        self._applied_patterns.append(pattern.id)
        self._change_ids.append(change_id)

        return ApplyResult(
            success=True,
            description=f"Increased escalation timeout from {old:.0f}s to {suggested}s",
            expected_impact=pattern.impact,
            change_id=change_id,
        )

    # ------------------------------------------------------------------
    # Impact measurement
    # ------------------------------------------------------------------

    async def measure_impact(self, change_id: str | None = None) -> list[dict]:
        """
        Compare post-change metrics against pre-change metrics.
        If change_id is given, measures only that change.
        Otherwise measures all pending changes (metrics_after IS NULL).
        Stores results back into policy_changes.metrics_after.
        """
        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)

        changes = await audit.get_policy_changes(limit=100)
        if change_id:
            changes = [c for c in changes if c["id"] == change_id]
        else:
            changes = [c for c in changes if not c.get("metrics_after")]

        results = []
        for change in changes:
            tool_name = change.get("tool_name", "")
            applied_at = change.get("applied_at", "")
            if not tool_name or not applied_at:
                continue

            metrics_after = await audit.get_tool_metrics(tool_name, applied_at)
            await audit.update_policy_change_metrics(change["id"], json.dumps(metrics_after))

            before = {}
            try:
                before = json.loads(change["metrics_before"]) if change.get("metrics_before") else {}
            except Exception:
                pass

            delta = {
                "escalation_rate": round(
                    metrics_after.get("escalation_rate", 0) - before.get("escalation_rate", 0), 1
                ),
                "block_rate": round(
                    metrics_after.get("block_rate", 0) - before.get("block_rate", 0), 1
                ),
                "allow_rate": round(
                    metrics_after.get("allow_rate", 0) - before.get("allow_rate", 0), 1
                ),
            }
            results.append({
                "change_id": change["id"],
                "tool_name": tool_name,
                "action": change.get("action"),
                "before_value": change.get("before_value"),
                "after_value": change.get("after_value"),
                "applied_at": applied_at,
                "metrics_before": before,
                "metrics_after": metrics_after,
                "delta": delta,
            })
        return results

    # ------------------------------------------------------------------
    # Change history
    # ------------------------------------------------------------------

    async def get_change_history(self) -> list[dict]:
        """Return all policy changes logged by this engine, newest first."""
        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)
        return await audit.get_policy_changes(limit=100)

    # ------------------------------------------------------------------
    # Few-shot examples
    # ------------------------------------------------------------------

    async def mine_examples(self, limit: int = 10) -> list[dict]:
        """
        Query approved escalations and format as few-shot examples.
        Deduplicates by (tool_name, args_summary) so repeated identical calls
        don't crowd out diverse examples.
        """
        from agentgate.audit import AuditLogger
        audit = AuditLogger(self.db_path)
        await audit._ensure_init()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT tool_name, args, original_task, human_reason, reason
                   FROM audit_log
                   WHERE human_decision = 'approved'
                   ORDER BY decided_at DESC
                   LIMIT ?""",
                (limit * 3,),  # fetch extra to allow dedup
            ) as cur:
                rows = await cur.fetchall()

        seen: set[str] = set()
        examples: list[dict] = []
        for row in rows:
            if len(examples) >= limit:
                break
            try:
                args = json.loads(row["args"]) if row["args"] else {}
            except Exception:
                args = {}
            args_summary = ", ".join(
                f"{k}={str(v)[:30]}" for k, v in list(args.items())[:3]
            )
            dedup_key = f"{row['tool_name']}:{args_summary}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            examples.append({
                "task": row["original_task"] or "(unknown task)",
                "action": f"{row['tool_name']}({args_summary})",
                "outcome": "approved",
                "reason": row["human_reason"] or row["reason"] or "approved by reviewer",
            })

        self._injected_examples = examples
        return examples

    def get_enhanced_system_prompt(self, base_prompt: str) -> str:
        """
        Returns base_prompt + learned instructions + few-shot examples.
        Only adds sections if there is content to add.
        """
        parts = [base_prompt]

        if self._prompt_additions:
            parts.append("\n\n--- Learned Instructions ---")
            for instruction in self._prompt_additions:
                parts.append(f"- {instruction}")

        if self._injected_examples:
            parts.append("\n\n--- Approved Decision Examples (use as guidance) ---")
            for ex in self._injected_examples[:5]:
                parts.append(
                    f"Task: {ex['task'][:100]}\n"
                    f"Action: {ex['action']}\n"
                    f"Outcome: {ex['outcome']} — {ex['reason']}"
                )

        return "\n".join(parts)
