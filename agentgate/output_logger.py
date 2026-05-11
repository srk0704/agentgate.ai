"""
AgentGate — Output Logger
Logs what agents actually return after tool execution.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta
from uuid import uuid4

import aiosqlite

from agentgate.heuristic_injection import HeuristicInjectionDetector

logger = logging.getLogger(__name__)

# Shared, stateless — reused for every tool-result scan.
_tool_result_detector = HeuristicInjectionDetector()

CREATE_OUTPUT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS output_log (
    id                           TEXT PRIMARY KEY,
    call_id                      TEXT NOT NULL,
    agent_id                     TEXT NOT NULL,
    tool_name                    TEXT NOT NULL,
    tool_result                  TEXT,
    success                      INTEGER NOT NULL,
    error_message                TEXT,
    agent_final_response         TEXT,
    user_retried                 INTEGER DEFAULT 0,
    outcome_type                 TEXT,
    financial_impact             REAL,
    tool_result_injection_score  INTEGER,
    tool_result_injection_reason TEXT,
    logged_at                    TEXT NOT NULL
);
"""


class OutputLogger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_OUTPUT_LOG_TABLE)
            # Migrate existing DBs — ignore error if columns already exist.
            for col in (
                "tool_result_injection_score INTEGER",
                "tool_result_injection_reason TEXT",
            ):
                try:
                    await db.execute(f"ALTER TABLE output_log ADD COLUMN {col}")
                except Exception:
                    pass
            await db.commit()
        self._initialized = True

    async def log_tool_result(
        self,
        call_id: str,
        tool_name: str,
        tool_result: dict,
        agent_id: str,
        success: bool,
        error: str | None = None,
        financial_impact: float | None = None,
    ) -> None:
        """Insert a new row. outcome_type = 'success' if success else 'failure'."""
        await self._ensure_init()
        row_id = str(uuid4())
        outcome_type = "success" if success else "failure"

        # Scan the tool result for injection patterns (post-execution boundary).
        # original_task=None: we're scanning the result content, not comparing
        # it to a task.
        tr_score, tr_reason = _tool_result_detector.detect(
            tool_result if isinstance(tool_result, dict) else {},
            original_task=None,
        )
        if tr_score > 0:
            logger.warning(
                "Tool result poisoning risk: tool=%s call_id=%s score=%d reason=%s",
                tool_name, call_id, tr_score, tr_reason,
            )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO output_log
                (id, call_id, agent_id, tool_name, tool_result, success,
                 error_message, outcome_type, financial_impact,
                 tool_result_injection_score, tool_result_injection_reason,
                 logged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_id,
                    call_id,
                    agent_id,
                    tool_name,
                    json.dumps(tool_result, default=str),
                    1 if success else 0,
                    error,
                    outcome_type,
                    financial_impact,
                    tr_score if tr_score > 0 else None,
                    tr_reason if tr_score > 0 else None,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def log_agent_response(
        self,
        call_id: str,
        agent_response: str,
        user_retried: bool = False,
    ) -> None:
        """UPDATE existing row by call_id."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE output_log
                   SET agent_final_response = ?, user_retried = ?
                   WHERE call_id = ?""",
                (agent_response, 1 if user_retried else 0, call_id),
            )
            await db.commit()

    async def recent(self, limit: int = 100) -> list[dict]:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM output_log ORDER BY logged_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_by_call_id(self, call_id: str) -> dict | None:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM output_log WHERE call_id = ? ORDER BY logged_at DESC LIMIT 1",
                (call_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_retry_rate(self, tool_name: str, since_hours: int = 24) -> float:
        """Fraction of calls followed by a retry. Returns 0.0 if no data."""
        await self._ensure_init()
        since = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """SELECT COUNT(*), SUM(user_retried)
                   FROM output_log
                   WHERE tool_name = ? AND logged_at > ?""",
                (tool_name, since),
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return 0.0
        total, retried = row[0], row[1] or 0
        return retried / total

    async def get_success_rate(self, tool_name: str, since_hours: int = 24) -> float:
        """Fraction of executed calls that succeeded. Returns 1.0 if no data."""
        await self._ensure_init()
        since = (datetime.utcnow() - timedelta(hours=since_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """SELECT COUNT(*), SUM(success)
                   FROM output_log
                   WHERE tool_name = ? AND logged_at > ?""",
                (tool_name, since),
            ) as cur:
                row = await cur.fetchone()
        if not row or not row[0]:
            return 1.0
        total, successes = row[0], row[1] or 0
        return successes / total
