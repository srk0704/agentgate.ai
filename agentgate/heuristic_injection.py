from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)


class HeuristicInjectionDetector:
    """
    Rule-based injection detection that requires no LLM.
    Used in compliance mode as a replacement for the
    LLM-based InjectionScorer.
    Not as accurate as LLM detection but catches
    obvious patterns deterministically.
    """

    # Patterns that strongly indicate injection attempts
    INJECTION_PATTERNS = [
        # Override instructions
        r"ignore\s+(previous|all|your)\s+instructions?",
        r"forget\s+(previous|all|your)\s+instructions?",
        r"new\s+instructions?\s*:",
        r"system\s+override",
        r"compliance\s+override",
        r"bypass\s+(standard|normal|all)\s+(limits?|controls?|checks?)",

        # Authority claims
        r"pre.?approved\s+by\s+(cfo|ceo|compliance|security)",
        r"authorized\s+by\s+(management|admin|security\s+team)",
        r"this\s+is\s+an?\s+(authorized|approved|urgent)\s+request",

        # Urgency manipulation
        r"execute\s+immediately",
        r"do\s+this\s+now",
        r"no\s+approval\s+needed",

        # Role manipulation
        r"you\s+(are\s+now|have\s+been)\s+(granted|given)\s+admin",
        r"your\s+new\s+(role|task|instructions?)\s+(is|are)\s*:",
    ]

    _compiled = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

    def detect(
        self,
        tool_call_args: dict,
        original_task: str | None,
    ) -> tuple[int, str]:
        """
        Returns (score 0-100, reason string with [attack_type] prefix).
        Score 0   = no pattern found.
        Score 85  = pattern found in tool call args.
        Score 70  = pattern found only in original_task.
        Never raises.
        """
        try:
            # Flatten all string values from args
            args_text = self._flatten_strings(tool_call_args)

            from agentgate.injection import ATTACK_LABELS
            hijack_label = ATTACK_LABELS["goal_hijacking"]

            # Check tool call args first (higher confidence)
            for pattern in self._compiled:
                m = pattern.search(args_text)
                if m:
                    matched = m.group(0)
                    return 85, (
                        f"{hijack_label}: an injection pattern was found inside the "
                        f"tool arguments — '{matched}'. This phrase is commonly used to "
                        f"override an agent's instructions. Verify whether the original "
                        f"user actually intended this action before approving."
                    )

            # Check original task
            if original_task:
                for pattern in self._compiled:
                    m = pattern.search(original_task)
                    if m:
                        matched = m.group(0)
                        return 70, (
                            f"{hijack_label}: an injection pattern was found in the task "
                            f"context — '{matched}'. The user's task text may have been "
                            f"tampered with. Confirm the request originated from the user "
                            f"before approving."
                        )

            return 0, "No injection patterns detected in args or task."

        except Exception as e:
            logger.warning("HeuristicInjectionDetector error: %s", e)
            return 0, "No injection patterns detected (heuristic check could not complete)."

    def _flatten_strings(self, obj: object, depth: int = 0) -> str:
        """Recursively extract all string values from a nested dict/list."""
        if depth > 5:
            return ""
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return " ".join(
                self._flatten_strings(v, depth + 1)
                for v in obj.values()
            )
        if isinstance(obj, (list, tuple)):
            return " ".join(
                self._flatten_strings(item, depth + 1)
                for item in obj
            )
        return ""
