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

    async def score(self, tool_call: ToolCall) -> tuple[int, str]:
        # Fast path: obviously safe read-only tools
        if any(tool_call.tool_name.startswith(p) for p in LOW_RISK_TOOLS):
            return 5, "read-only tool prefix — safe by default"

        if self.compliance_mode:
            return self._heuristic_score(tool_call)

        cache_key = self._cache_key(tool_call)
        if cache_key in _cache:
            return _cache[cache_key]

        try:
            result = await self._llm_score(tool_call)
        except Exception as e:
            logger.warning("Risk scorer LLM failed [%s]: %s — using heuristic", type(e).__name__, e)
            result = self._heuristic_score(tool_call)

        _cache[cache_key] = result
        return result

    async def _llm_score(self, tool_call: ToolCall) -> tuple[int, str]:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

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

Respond with ONLY a JSON object:
{{"score": <integer 0-100>, "reason": "<one sentence explaining the specific risk factors>"}}"""

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
        return int(data["score"]), data.get("reason", "")

    def _heuristic_score(self, tool_call: ToolCall) -> tuple[int, str]:
        """Fallback when LLM is unavailable."""
        name = tool_call.tool_name.lower()
        if any(w in name for w in ["delete", "remove", "drop", "destroy"]):
            return 85, "heuristic: destructive/irreversible action keyword"
        if any(w in name for w in ["refund", "charge", "transfer", "payment"]):
            return 45, "heuristic: financial action keyword"
        if any(w in name for w in ["update", "write", "create", "insert"]):
            return 40, "heuristic: write action keyword"
        return 20, "heuristic: no high-risk keyword matched"

    def _cache_key(self, tool_call: ToolCall) -> str:
        payload = f"v1:{tool_call.tool_name}:{json.dumps(tool_call.args, sort_keys=True, default=str)}"
        return hashlib.sha256(payload.encode()).hexdigest()
