from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Callable

from agentgate.models import Decision, DecisionOutcome, Effect, ToolCall
from agentgate.anomaly import AnomalyScorer
from agentgate.audit import AuditLogger
from agentgate.blast_radius import BlastRadiusEstimator
from agentgate.injection import InjectionScorer
from agentgate.policy import PolicyEvaluator, PolicyLoader
from agentgate.risk import RiskScorer
from agentgate.session import SessionTracker

logger = logging.getLogger(__name__)


def _parse_attack_type(injection_reason: str | None) -> str | None:
    """Extract attack_type from injection_reason prefix like '[goal_hijacking] ...'."""
    if not injection_reason or not injection_reason.startswith("["):
        return None
    bracket_end = injection_reason.find("]")
    if bracket_end < 0:
        return None
    at = injection_reason[1:bracket_end]
    return at if at not in ("none", "") else None


class GatewayClient:
    """
    The core AgentGate client.
    Wrap any tool call through .evaluate() before executing it.

    Usage:
        gate = GatewayClient.from_env()
        decision = await gate.evaluate(tool_call)
        if decision.is_allowed:
            result = await my_tool(**tool_call.args)
    """

    def __init__(
        self,
        policy_path: str,
        db_path: str,
        risk_scorer: RiskScorer | None = None,
        fail_open: bool = True,
        timeout_ms: float = 5000.0,
        escalation_timeout_sec: float = 300.0,
        compliance_mode: bool = False,
    ):
        self.fail_open = fail_open
        self.timeout_ms = timeout_ms
        self.escalation_timeout_sec = escalation_timeout_sec
        self.compliance_mode = compliance_mode
        # Cache thresholds at init — reading env vars on every evaluate() call is both
        # wasteful and risks inconsistent decisions if env changes at runtime.
        # Full justification for every default: docs/THRESHOLD_RESEARCH.md.

        # Block if risk_score >= this value.
        # Default 80: calibrated to rarely fire on legitimate high-value actions
        # while reliably catching genuinely dangerous ones (Pan 2025, "Measuring
        # Agents in Production").
        # Raise if seeing too many false positives. Lower if dangerous actions slip through.
        self._block_threshold = int(os.getenv("AGENTGATE_RISK_THRESHOLD_BLOCK", "80"))

        # Escalate to humans if risk_score >= this value.
        # Default 60: aligns with the 68%-of-agents-require-human-intervention
        # finding (Pan 2025) — frequent escalation is normal, not a failure mode.
        # Raise if your agent's legitimate actions cluster above 60.
        self._escalate_threshold = int(os.getenv("AGENTGATE_RISK_THRESHOLD_ESCALATE", "60"))

        # Block if injection_score >= this value.
        # Default 70: blocks clear injection attempts while leaving ambiguous
        # cases for human review. Source: OWASP LLM Top 10 2025 (LLM01 — prompt
        # injection is the highest-priority LLM risk); Agent-SafetyBench
        # (Zhang et al. 2024, arXiv:2412.14470).
        # Raise only if you see false positives on benign content with override-like phrasing.
        self._injection_block_threshold = int(os.getenv("AGENTGATE_INJECTION_THRESHOLD_BLOCK", "70"))

        # Block if anomaly_score >= this value (velocity / scope drift).
        # Default 80: paired with the 5-calls-per-60s velocity threshold
        # (see anomaly.py), so blocking only triggers on egregiously abnormal
        # session behavior, not routine bursts.
        # Lower if a runaway agent is burning tokens before being caught.
        self._anomaly_block_threshold = int(os.getenv("AGENTGATE_ANOMALY_SCORE_BLOCK", "80"))

        # Escalate if anomaly_score >= this value.
        # Default 50: catches early scope-drift signals (a session reaching for
        # tools outside its stated purpose) before they become hard-block events.
        self._anomaly_escalate_threshold = int(os.getenv("AGENTGATE_ANOMALY_SCORE_ESCALATE", "50"))

        # Drift thresholds — Amazon failure taxonomy (AWS Blog 2026).
        # Default 85 block: high bar to avoid false positives on legitimate
        # surprising tool choices.
        self._drift_block = int(os.getenv("AGENTGATE_DRIFT_THRESHOLD_BLOCK", "85"))
        # Default 60 escalate: ambiguous drift goes to humans, not the bin.
        self._drift_escalate = int(os.getenv("AGENTGATE_DRIFT_THRESHOLD_ESCALATE", "60"))

        # Loop / retry-storm thresholds — Nygard "Release It!" + Replit incident.
        self._loop_block = int(os.getenv("AGENTGATE_LOOP_THRESHOLD_BLOCK", "85"))
        self._loop_escalate = int(os.getenv("AGENTGATE_LOOP_THRESHOLD_ESCALATE", "70"))

        self._policy_evaluator = PolicyEvaluator(PolicyLoader(policy_path))
        self._audit = AuditLogger(db_path)
        from agentgate.escalation import EscalationQueue
        EscalationQueue.configure(db_path)
        self._risk_scorer = risk_scorer or RiskScorer(compliance_mode=compliance_mode)
        self._injection_scorer = InjectionScorer(compliance_mode=compliance_mode)
        self._blast_radius = BlastRadiusEstimator()
        self._session_tracker = SessionTracker(db_path)
        self._anomaly_scorer = AnomalyScorer(self._session_tracker)
        from agentgate.drift_detector import DriftDetector
        from agentgate.loop_detector import LoopDetector
        self._drift_detector = DriftDetector(db_path=db_path, compliance_mode=compliance_mode)
        self._loop_detector = LoopDetector(db_path=db_path)
        from agentgate.pii_detector import PiiDetector
        self._pii_detector = PiiDetector()

    @classmethod
    def from_env(cls) -> "GatewayClient":
        """Construct client from environment variables."""
        return cls(
            policy_path=os.getenv("AGENTGATE_POLICY_PATH", "./policies.yaml"),
            db_path=os.getenv("AGENTGATE_DB_PATH", "./agentgate.db"),
            fail_open=os.getenv("AGENTGATE_FAIL_OPEN", "true").lower() == "true",
            timeout_ms=float(os.getenv("AGENTGATE_TIMEOUT_MS", "5000")),
            escalation_timeout_sec=float(os.getenv("AGENTGATE_ESCALATION_TIMEOUT_SEC", "300")),
            compliance_mode=os.getenv("AGENTGATE_COMPLIANCE_MODE", "false").lower() == "true",
        )

    @classmethod
    def from_dict(
        cls,
        policies: list,
        db_path: str = ":memory:",
        fail_open: bool = True,
        timeout_ms: float = 5000.0,
        escalation_timeout_sec: float = 300.0,
        compliance_mode: bool = False,
    ) -> "GatewayClient":
        """
        Create a GatewayClient from inline policy dicts — no YAML file needed.

        Usage:
            gate = GatewayClient.from_dict([
                {
                    "name": "block_big_refunds",
                    "match": {"tool": "issue_refund"},
                    "conditions": [{"field": "args.amount", "op": "gt", "value": 500}],
                    "effect": "block",
                    "reason": "Refunds over $500 not permitted",
                }
            ])
        """
        from agentgate.policy import PolicyLoader, PolicyEvaluator

        instance = object.__new__(cls)
        instance.fail_open = fail_open
        instance.timeout_ms = timeout_ms
        instance.escalation_timeout_sec = escalation_timeout_sec
        instance.compliance_mode = compliance_mode
        instance._block_threshold = int(os.getenv("AGENTGATE_RISK_THRESHOLD_BLOCK", "80"))
        instance._escalate_threshold = int(os.getenv("AGENTGATE_RISK_THRESHOLD_ESCALATE", "60"))
        instance._injection_block_threshold = int(os.getenv("AGENTGATE_INJECTION_THRESHOLD_BLOCK", "70"))
        instance._anomaly_block_threshold = int(os.getenv("AGENTGATE_ANOMALY_SCORE_BLOCK", "80"))
        instance._anomaly_escalate_threshold = int(os.getenv("AGENTGATE_ANOMALY_SCORE_ESCALATE", "50"))
        instance._drift_block = int(os.getenv("AGENTGATE_DRIFT_THRESHOLD_BLOCK", "85"))
        instance._drift_escalate = int(os.getenv("AGENTGATE_DRIFT_THRESHOLD_ESCALATE", "60"))
        instance._loop_block = int(os.getenv("AGENTGATE_LOOP_THRESHOLD_BLOCK", "85"))
        instance._loop_escalate = int(os.getenv("AGENTGATE_LOOP_THRESHOLD_ESCALATE", "70"))
        loader = PolicyLoader.from_list(policies)
        instance._policy_evaluator = PolicyEvaluator(loader)
        instance._audit = AuditLogger(db_path)
        instance._risk_scorer = RiskScorer(compliance_mode=compliance_mode)
        instance._injection_scorer = InjectionScorer(compliance_mode=compliance_mode)
        instance._blast_radius = BlastRadiusEstimator()
        instance._session_tracker = SessionTracker(db_path)
        instance._anomaly_scorer = AnomalyScorer(instance._session_tracker)
        from agentgate.drift_detector import DriftDetector
        from agentgate.loop_detector import LoopDetector
        instance._drift_detector = DriftDetector(db_path=db_path, compliance_mode=compliance_mode)
        instance._loop_detector = LoopDetector(db_path=db_path)
        from agentgate.pii_detector import PiiDetector
        instance._pii_detector = PiiDetector()
        return instance

    async def evaluate(self, tool_call: ToolCall) -> Decision:
        """
        Evaluate a tool call against policies and risk scorer.
        Always returns a Decision — never raises.
        """
        start = time.monotonic()
        try:
            decision = await self._evaluate_internal(tool_call)
        except Exception as exc:
            logger.error("AgentGate error: %s — failing open", exc, exc_info=True)
            decision = Decision(
                outcome=DecisionOutcome.FAILED_OPEN,
                tool_call=tool_call,
                reason=f"Gateway error: {exc}",
            )

        decision.latency_ms = (time.monotonic() - start) * 1000
        # Compute unified reliability score across all component scores.
        decision.reliability_score, decision.reliability_summary = Decision.compute_reliability_score(
            risk_score=decision.risk_score,
            injection_score=decision.injection_score,
            anomaly_score=decision.anomaly_score,
            drift_score=decision.drift_score,
            loop_score=decision.loop_score,
        )
        await self._audit.log(decision)

        # Structured logs for SIEM / security monitoring
        tc = decision.tool_call
        if decision.outcome == DecisionOutcome.BLOCKED:
            logger.warning(
                "BLOCKED agent=%s tool=%s reason=%r risk=%s injection=%s attack=%s latency=%.0fms",
                tc.agent_id, tc.tool_name, decision.reason,
                decision.risk_score, decision.injection_score, decision.attack_type,
                decision.latency_ms,
            )
        elif decision.outcome == DecisionOutcome.ESCALATED:
            logger.info(
                "ESCALATED agent=%s tool=%s escalation_id=%s risk=%s anomaly=%s latency=%.0fms",
                tc.agent_id, tc.tool_name, decision.escalation_id,
                decision.risk_score, decision.anomaly_score, decision.latency_ms,
            )

        return decision

    async def _evaluate_internal(self, tool_call: ToolCall) -> Decision:
        # Step 1: Blast radius — synchronous, always runs, never fails.
        blast_radius = self._blast_radius.estimate(tool_call)

        # Step 2: Policy check — synchronous, instant.
        policy_result = self._policy_evaluator.evaluate(tool_call)

        if policy_result.effect == Effect.BLOCK:
            # Still run injection scoring on policy blocks to surface injection attacks
            # embedded in content that also violated a policy rule (see DECISION_PRECEDENCE.md).
            inj_score, inj_reason, attack_type = await self._run_injection_only(tool_call)
            return Decision(
                outcome=DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason=policy_result.reason,
                injection_score=inj_score,
                injection_reason=inj_reason,
                attack_type=attack_type,
                blast_radius=blast_radius,
                policy_matched=policy_result.policy_name,
            )

        # Step 3: Parallel scoring (risk + injection + anomaly + drift + loop).
        # Note: explicit ALLOW policies do NOT skip scoring — injection can still override.
        try:
            (
                (risk_score, risk_reason),
                (injection_score, injection_reason),
                (anomaly_score, anomaly_reason),
                (drift_score, drift_reason),
                (loop_score, loop_reason),
            ) = await asyncio.wait_for(
                asyncio.gather(
                    self._risk_scorer.score(tool_call),
                    self._injection_scorer.score(tool_call),
                    self._anomaly_scorer.score(tool_call),
                    self._drift_detector.score(tool_call),
                    self._loop_detector.score(tool_call),
                ),
                timeout=self.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "AgentGate timeout for tool=%s call_id=%s — failing %s",
                tool_call.tool_name,
                tool_call.call_id,
                "open" if self.fail_open else "closed",
            )
            return Decision(
                outcome=DecisionOutcome.FAILED_OPEN if self.fail_open
                        else DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason="Scoring timeout",
                blast_radius=blast_radius,
            )

        attack_type = _parse_attack_type(injection_reason)

        block_threshold = self._block_threshold
        escalate_threshold = self._escalate_threshold
        injection_block_threshold = self._injection_block_threshold
        anomaly_block_threshold = self._anomaly_block_threshold
        anomaly_escalate_threshold = self._anomaly_escalate_threshold

        # Common kwargs shared by every Decision return below — keeps the new
        # drift / loop fields from getting forgotten on any path.
        common = dict(
            risk_score=risk_score, risk_reason=risk_reason,
            injection_score=injection_score, injection_reason=injection_reason,
            attack_type=attack_type,
            anomaly_score=anomaly_score, anomaly_reason=anomaly_reason,
            drift_score=drift_score, drift_reason=drift_reason,
            loop_score=loop_score, loop_reason=loop_reason,
            blast_radius=blast_radius,
            policy_matched=policy_result.policy_name,
        )

        # Step 4: Decision routing (injection wins over explicit policy ALLOW).
        if injection_score is not None and injection_score >= injection_block_threshold:
            logger.warning(
                "Injection/excessive-agency detected: tool=%s score=%d type=%s",
                tool_call.tool_name, injection_score, attack_type,
            )
            return Decision(
                outcome=DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason=f"Injection/excessive agency detected: {injection_reason}",
                **common,
            )

        if risk_score is not None and risk_score >= block_threshold:
            return Decision(
                outcome=DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason=f"Risk score {risk_score} exceeds block threshold",
                **common,
            )

        if anomaly_score is not None and anomaly_score >= anomaly_block_threshold:
            logger.warning(
                "Anomaly blocked: tool=%s score=%d reason=%s",
                tool_call.tool_name, anomaly_score, anomaly_reason,
            )
            return Decision(
                outcome=DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason=f"Anomaly detected: {anomaly_reason}",
                **common,
            )

        # Drift block — agent is acting off-task in a clear, structural way.
        if drift_score is not None and drift_score >= self._drift_block:
            logger.warning(
                "Drift blocked: tool=%s score=%d reason=%s",
                tool_call.tool_name, drift_score, drift_reason,
            )
            return Decision(
                outcome=DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason=f"Off-task action detected: {drift_reason}",
                **common,
            )

        # Loop / retry-storm block — agent is burning tokens or about to do worse.
        if loop_score is not None and loop_score >= self._loop_block:
            logger.warning(
                "Loop blocked: tool=%s score=%d reason=%s",
                tool_call.tool_name, loop_score, loop_reason,
            )
            return Decision(
                outcome=DecisionOutcome.BLOCKED,
                tool_call=tool_call,
                reason=f"Loop detected: {loop_reason}",
                **common,
            )

        # Escalation check — blast_radius critical forces escalation.
        needs_escalation = (
            policy_result.effect == Effect.ESCALATE
            or (risk_score is not None and risk_score >= escalate_threshold)
            or blast_radius.get("severity") == "critical"
            or (anomaly_score is not None and anomaly_score >= anomaly_escalate_threshold)
            or (drift_score is not None and drift_score >= self._drift_escalate)
            or (loop_score is not None and loop_score >= self._loop_escalate)
        )
        if needs_escalation:
            from agentgate.escalation import EscalationQueue
            escalation_id = await EscalationQueue.submit(tool_call, risk_score or 0)
            return Decision(
                outcome=DecisionOutcome.ESCALATED,
                tool_call=tool_call,
                reason=policy_result.reason or "Requires human approval",
                escalation_id=escalation_id,
                **common,
            )

        return Decision(
            outcome=DecisionOutcome.ALLOWED,
            tool_call=tool_call,
            reason="Passed policy and risk checks",
            **common,
        )

    async def _run_injection_only(
        self, tool_call: ToolCall
    ) -> tuple[int | None, str | None, str | None]:
        """Run injection scoring alone — used on policy-blocked decisions."""
        if not tool_call.original_task:
            return None, None, None
        try:
            inj_score, inj_reason = await asyncio.wait_for(
                self._injection_scorer.score(tool_call),
                timeout=self.timeout_ms / 1000,
            )
            return inj_score, inj_reason, _parse_attack_type(inj_reason)
        except Exception as e:
            logger.debug("Injection-only scoring failed on policy-blocked call: %s", e)
            return None, None, None

    async def scan_output(
        self,
        output: str,
        tool_name: str,
        agent_id: str = "unknown",
    ) -> dict:
        """
        Scan agent output for PII before returning to the caller.

        Returns:
            {
                "safe": bool,
                "pii_found": list[str],
                "recommendation": "allow" | "redact" | "block",
                "redacted_output": str | None,
            }

        Fail-open: errors return {"safe": True, "pii_found": [], "recommendation": "allow"}.
        """
        try:
            has_pii, findings = await self._pii_detector.scan(output)
        except Exception as e:
            logger.warning("PII scan error: %s — failing open", e)
            result = {"safe": True, "pii_found": [], "recommendation": "allow", "redacted_output": None}
            await self._audit.log_pii_scan(agent_id, tool_name, result)
            return result

        if not has_pii:
            result = {"safe": True, "pii_found": [], "recommendation": "allow", "redacted_output": None}
        else:
            read_only_prefixes = ("get_", "view_", "fetch_", "read_", "list_", "search_")
            is_read_only = any(tool_name.startswith(p) for p in read_only_prefixes)
            if is_read_only:
                redacted = self._pii_detector.redact(output, findings)
                result = {
                    "safe": False,
                    "pii_found": findings,
                    "recommendation": "redact",
                    "redacted_output": redacted,
                }
            else:
                result = {
                    "safe": False,
                    "pii_found": findings,
                    "recommendation": "block",
                    "redacted_output": None,
                }

        await self._audit.log_pii_scan(agent_id, tool_name, result)
        return result

    def guarded(self, fn: Callable) -> Callable:
        """
        Decorator for sync or async functions.
        Wraps a tool function so it's evaluated before execution.

        Usage:
            gate = GatewayClient.from_env()

            @gate.guarded
            async def issue_refund(user_id: str, amount: float) -> dict:
                ...
        """
        import functools

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            tool_call = ToolCall(
                tool_name=fn.__name__,
                args=kwargs,
                agent_id=kwargs.pop("__agent_id__", "unknown"),
                context=kwargs.pop("__context__", {}),
            )
            decision = await self.evaluate(tool_call)
            if not decision.is_allowed:
                raise PermissionError(
                    f"AgentGate blocked '{fn.__name__}': {decision.reason}"
                )
            if asyncio.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper
