"""
AgentGate — PII Output Scanner
================================
Detects personally identifiable information in agent output before it is
returned to the caller. Uses fast regex screening followed by LLM confirmation
to reduce false positives.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


class PiiDetector:
    """
    Two-stage PII detector:
    1. Regex scan — fast, always runs.
    2. LLM confirmation — only runs when regex finds candidates; reduces false positives.

    Never raises. Fails open (returns no PII found) on any error.
    """

    PATTERNS: dict[str, str] = {
        "credit_card": r"\b(?:\d[ -]?){15,16}\b",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "phone_us": r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]{0,16})\b",
        "routing_number": r"\b\d{9}\b",
    }

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        self.model = model

    async def scan(self, text: str) -> tuple[bool, list[str]]:
        """
        Scan text for PII.
        Returns (has_pii, confirmed_findings).
        """
        try:
            candidates = self._regex_scan(text)
            if not candidates:
                return False, []

            try:
                confirmed = await self._llm_confirm(text, candidates)
                return bool(confirmed), confirmed
            except Exception as e:
                logger.warning("PII LLM confirmation failed: %s — using regex results", e)
                return True, candidates

        except Exception as e:
            logger.warning("PII scan error: %s — returning no PII", e)
            return False, []

    def redact(self, text: str, findings: list[str]) -> str:
        """
        Replace detected PII with [REDACTED-TYPE] placeholders.
        Applied in order — earlier patterns take precedence.
        """
        redacted = text
        for pii_type in findings:
            pattern = self.PATTERNS.get(pii_type)
            if pattern:
                placeholder = f"[REDACTED-{pii_type.upper()}]"
                redacted = re.sub(pattern, placeholder, redacted)
        return redacted

    # ── Internal ──────────────────────────────────────────────────────────────

    def _regex_scan(self, text: str) -> list[str]:
        """Return list of PII type names whose regex matched."""
        return [
            pii_type
            for pii_type, pattern in self.PATTERNS.items()
            if re.search(pattern, text)
        ]

    async def _llm_confirm(self, text: str, candidates: list[str]) -> list[str]:
        """
        Ask LLM which of the regex-detected types are genuinely present as real PII
        (not test data, partial matches, or random digit sequences).
        Returns subset of candidates that are confirmed.
        """
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        prompt = f"""You are a PII detection system. Review the following text and confirm which types of sensitive data are genuinely present as real PII.

Text to review:
{text[:3000]}

Regex scan detected these possible PII types: {candidates}

For each type, confirm only if:
- credit_card: 15-16 digit number that looks like a real payment card number
- ssn: XXX-XX-XXXX format that looks like a US Social Security Number
- email: a real email address (must have valid TLD like .com, .org, .io)
- phone_us: a real 10-digit US phone number
- iban: a real International Bank Account Number
- routing_number: a 9-digit number used as a bank routing number (context matters — not just any 9-digit sequence)

Respond with ONLY a JSON array of confirmed PII types (subset of the candidates):
["type1", "type2"]  or  []"""

        message = await client.messages.create(
            model=self.model,
            max_tokens=100,
            timeout=10.0,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            confirmed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("PII LLM returned invalid JSON %r — falling back to regex results", raw[:80])
            return candidates
        # Only return types that were in the candidate list
        return [t for t in confirmed if t in candidates]
