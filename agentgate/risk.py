from __future__ import annotations
import hashlib
import json
import logging
import os

from agentgate.models import ToolCall

logger = logging.getLogger(__name__)

# Simple in-process cache: {hash: (score, reason)}
_cache: dict[str, tuple[int, str]] = {}

LOW_RISK_TOOLS = {"get_", "list_", "fetch_", "read_", "search_"}


class RiskScorer:
    """
    Scores a tool call 0-100 for risk.
    Returns (score, reason) — both are stored in the audit log.
    Uses LLM for ambiguous cases, heuristics for obvious ones.

    compliance_mode=True: uses heuristics only — no LLM calls.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        compliance_mode: bool = False,
    ):
        self.model = model
        self.compliance_mode = compliance_mode

    async def score(
        self,
        tool_call: ToolCall,
        recent_calls: list[dict] | None = None,
    ) -> tuple[int, str]:
        # Fast path: obviously safe read-only tools
        if any(tool_call.tool_name.startswith(p) for p in LOW_RISK_TOOLS):
            return 5, (
                f"Safe: '{tool_call.tool_name}' is a read-only operation. "
                f"No data will be modified."
            )

        if self.compliance_mode:
            return self._heuristic_score(tool_call)

        cache_key = self._cache_key(tool_call, recent_calls)
        if cache_key in _cache:
            return _cache[cache_key]

        try:
            result = await self._llm_score(tool_call, recent_calls)
        except Exception as exc:
            # ValueError means key missing/invalid —
            # expected in dev, log quietly
            if isinstance(exc, ValueError):
                logger.debug(
                    "Risk scorer skipping LLM: %s",
                    exc
                )
            else:
                logger.warning(
                    "Risk scorer LLM failed "
                    "[%s]: %s — using heuristic",
                    type(exc).__name__, exc
                )
            result = self._heuristic_score(tool_call)

        _cache[cache_key] = result
        return result

    async def _llm_score(
        self,
        tool_call: ToolCall,
        recent_calls: list[dict] | None = None,
    ) -> tuple[int, str]:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key == "your-key-here":
            raise ValueError(
                "ANTHROPIC_API_KEY not set"
            )
        client = anthropic.AsyncAnthropic(
            api_key=api_key
        )

        # Build trajectory section if we have recent calls in this session.
        if recent_calls:
            trajectory_lines = []
            for i, call in enumerate(recent_calls, 1):
                trajectory_lines.append(
                    f"  {i}. {call['tool_name']}"
                    f" (task: {call['original_task'][:60]})"
                )
            trajectory_str = "\n".join(trajectory_lines)
            trajectory_section = (
                f"\nRecent session history (last {len(recent_calls)} calls before this one):\n"
                f"{trajectory_str}\n\n"
                f"Consider: does this current action escalate risk "
                f"given what the agent has already done this session? "
                f"A sequence of actions can be more dangerous than "
                f"any single action in isolation."
            )
        else:
            trajectory_section = ""

        prompt = f"""You are a risk assessment system for AI agent actions.
Score the following tool call from 0-100 for risk.

Scoring guide:
0-30  = safe, read-only, or small routine action (e.g. lookup, small refund <$50)
31-60 = moderate risk, reversible action (e.g. update record, medium refund $50-$200)
61-80 = high risk, significant financial or data action (e.g. large refund >$200, bulk update)
81-100 = critical, irreversible, or security-sensitive (e.g. delete account, export all PII)

Tool: {tool_call.tool_name}
Args: {json.dumps(tool_call.args, default=str)}
Context: {json.dumps(tool_call.context, default=str)}
{trajectory_section}
Respond with ONLY a JSON object:
{{"score": <integer 0-100>, "reason": "<2-3 sentences written for a human reviewer who needs to decide whether to approve or reject this action. State: (1) what makes this risky or safe, (2) what the financial or data impact could be, (3) what the reviewer should verify before approving. Be specific about dollar amounts, tool names, and data involved.>"}}"""

        message = await client.messages.create(
            model=self.model,
            max_tokens=150,
            timeout=10.0,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        if not raw:
            raise ValueError(f"Empty LLM response. stop_reason={message.stop_reason}")
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        raw_score = int(data["score"])
        score = max(0, min(100, raw_score))
        return score, data.get("reason", "")

    def _heuristic_score(self, tool_call: ToolCall) -> tuple[int, str]:
        """Fallback when LLM is unavailable."""
        name = tool_call.tool_name.lower()
        if any(w in name for w in ["delete", "remove", "drop", "destroy"]):
            return 85, (
                f"High risk: '{tool_call.tool_name}' contains a destructive keyword "
                f"(delete/remove/drop). This action may be irreversible. "
                f"Verify the target scope before approving — confirm what will be deleted "
                f"and whether a backup exists."
            )
        if any(w in name for w in ["refund", "charge", "transfer", "payment"]):
            return 45, (
                f"Moderate risk: '{tool_call.tool_name}' is a financial action. "
                f"Check the amount and recipient in the args before approving. "
                f"Confirm this matches the user's original request."
            )
        if any(w in name for w in ["update", "write", "create", "insert"]):
            return 40, (
                f"Low-moderate risk: '{tool_call.tool_name}' will modify data. "
                f"Verify the record being changed and confirm the change is intentional."
            )
        return 20, (
            f"Low risk: '{tool_call.tool_name}' does not match any high-risk patterns. "
            f"Standard review applies."
        )

    def _cache_key(
        self,
        tool_call: ToolCall,
        recent_calls: list[dict] | None = None,
    ) -> str:
        trajectory = json.dumps(
            [c["tool_name"] for c in recent_calls] if recent_calls else [],
            sort_keys=True,
        )
        payload = (
            f"v2:{tool_call.tool_name}:"
            f"{json.dumps(tool_call.args, sort_keys=True, default=str)}:"
            f"{trajectory}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()
