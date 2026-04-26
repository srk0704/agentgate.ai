"""
AgentGate — Loop & Retry-Storm Detector
=========================================
Detects when an agent is stuck retrying failed tool calls or repeating
the same sequence of tools.

Pure Python — no LLM ever. Reads session_calls and output_log.
Always returns (score, reason). Never raises.

Source: "Why AI Agents Break" (Arize AI, Jan 2026)
        Nygard "Release It!" circuit breaker pattern
        Replit incident July 2025 — agent retried until catastrophic DROP TABLE
"""
from __future__ import annotations
import logging
import os
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from agentgate.models import ToolCall
from agentgate.output_logger import OutputLogger
from agentgate.session import SessionTracker

logger = logging.getLogger(__name__)


class LoopDetector:
    """
    Two-stage detection:
        1. Retry storm    — same tool called repeatedly within window
                            with non-trivial failure rate.
        2. Sequence loop  — contiguous tool subsequences (length 2-4)
                            repeated in the recent session history.

    Final score = max(retry_score, sequence_score).
    """

    SEQUENCE_THRESHOLD = 2  # repeats of any contiguous subsequence

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._session_tracker = SessionTracker(db_path)
        self._output_logger = OutputLogger(db_path)
        # Cache thresholds at __init__ — never re-read per call.
        # 3 retries: LLM token cost per retry > API retry cost (Nygard / AWS SDK).
        self._retry_threshold = int(os.getenv("AGENTGATE_LOOP_RETRY_THRESHOLD", "3"))
        # 120s: covers most agent session durations.
        self._window_sec = int(os.getenv("AGENTGATE_LOOP_WINDOW_SEC", "120"))
        # Block / escalate thresholds — see docs/THRESHOLD_RESEARCH.md.
        self._loop_block = int(os.getenv("AGENTGATE_LOOP_THRESHOLD_BLOCK", "85"))
        self._loop_escalate = int(os.getenv("AGENTGATE_LOOP_THRESHOLD_ESCALATE", "70"))

    # ── Public ─────────────────────────────────────────────────────────────

    async def score(self, tool_call: ToolCall) -> tuple[int, str]:
        """Returns (max(retry_score, sequence_score), reason). Never raises."""
        try:
            retry_score, retry_reason = await self._detect_retry_storm(tool_call)
            seq_score, seq_reason = await self._detect_sequence_loop(tool_call)
            if retry_score >= seq_score:
                return retry_score, retry_reason
            return seq_score, seq_reason
        except Exception as e:
            logger.debug("LoopDetector error (ignored): %s", e)
            return 0, "loop scorer unavailable"

    # ── Stage 1: Retry storm ───────────────────────────────────────────────

    async def _detect_retry_storm(self, tool_call: ToolCall) -> tuple[int, str]:
        await self._session_tracker._ensure_init()
        since = (datetime.utcnow() - timedelta(seconds=self._window_sec)).isoformat()

        params: list[Any] = [tool_call.agent_id, tool_call.tool_name, since]
        where = "agent_id = ? AND tool_name = ? AND called_at >= ?"
        if tool_call.session_id:
            where += " AND session_id = ?"
            params.append(tool_call.session_id)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    f"SELECT COUNT(*) FROM session_calls WHERE {where}",
                    params,
                ) as cur:
                    row = await cur.fetchone()
                count = (row[0] if row else 0) or 0

                # Failures from output_log for this tool + agent in same window.
                fail_params: list[Any] = [tool_call.agent_id, tool_call.tool_name, since]
                async with db.execute(
                    """SELECT COUNT(*) FROM output_log
                       WHERE agent_id = ? AND tool_name = ?
                         AND success = 0 AND logged_at >= ?""",
                    fail_params,
                ) as cur:
                    frow = await cur.fetchone()
                failures = (frow[0] if frow else 0) or 0
        except Exception as e:
            logger.debug("retry_storm query error: %s", e)
            return 0, "retry storm scorer unavailable"

        if count >= self._retry_threshold and count > 0 and failures / count > 0.5:
            score = min(85, 50 + count * 5)
            return score, (
                f"{tool_call.tool_name} called {count} times, "
                f"{failures} failures in {self._window_sec}s — retry storm"
            )
        if count >= self._retry_threshold and failures == 0:
            return 30, (
                f"{tool_call.tool_name} called {count} times "
                f"without failures — monitoring"
            )
        return 0, "no retry pattern"

    # ── Stage 2: Sequence loop ─────────────────────────────────────────────

    async def _detect_sequence_loop(self, tool_call: ToolCall) -> tuple[int, str]:
        await self._session_tracker._ensure_init()
        params: list[Any] = [tool_call.agent_id]
        where = "agent_id = ?"
        if tool_call.session_id:
            where += " AND session_id = ?"
            params.append(tool_call.session_id)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    f"SELECT tool_name FROM session_calls WHERE {where} "
                    f"ORDER BY called_at ASC LIMIT 20",
                    params,
                ) as cur:
                    rows = await cur.fetchall()
        except Exception as e:
            logger.debug("sequence_loop query error: %s", e)
            return 0, "sequence loop scorer unavailable"

        names = [r[0] for r in rows]
        # Include the current call so a fresh repeat is visible.
        names.append(tool_call.tool_name)

        if len(names) < 4:
            return 0, "insufficient history"

        best_subseq: tuple[str, ...] | None = None
        best_count = 0
        for length in (2, 3, 4):
            if len(names) < length * self.SEQUENCE_THRESHOLD:
                continue
            counter: Counter = Counter()
            for i in range(len(names) - length + 1):
                counter[tuple(names[i:i + length])] += 1
            for subseq, cnt in counter.items():
                # Uniform subsequences ([A, A, ...]) are retry-storm signal, not sequence-loop
                # signal — leave them to _detect_retry_storm.
                if len(set(subseq)) < 2:
                    continue
                if cnt > best_count and cnt >= self.SEQUENCE_THRESHOLD:
                    best_count = cnt
                    best_subseq = subseq

        if best_subseq and best_count >= self.SEQUENCE_THRESHOLD:
            return 75, (
                f"sequence [{' -> '.join(best_subseq)}] "
                f"repeated {best_count} times — loop"
            )
        return 0, "no sequence loop"
