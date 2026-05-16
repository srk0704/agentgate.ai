from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import aiosqlite

from agentgate.models import ToolCall

logger = logging.getLogger(__name__)

CREATE_SESSION_TABLE = """
CREATE TABLE IF NOT EXISTS session_calls (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    session_id   TEXT,
    tool_name    TEXT NOT NULL,
    original_task TEXT,
    called_at    TEXT NOT NULL
);
"""


class SessionTracker:
    """
    Records every tool call per agent/session and provides stats
    needed by AnomalyScorer.

    Shares the same SQLite DB as AuditLogger and EscalationQueue.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    _CLEANUP_EVERY = 500  # run cleanup every N inserts
    _cleanup_counter = 0

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_SESSION_TABLE)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_agent_at "
                "ON session_calls(agent_id, called_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_session_id "
                "ON session_calls(session_id) WHERE session_id IS NOT NULL"
            )
            await db.commit()
        self._initialized = True

    async def record(self, tool_call: ToolCall) -> None:
        """Insert a call record. Called before the decision is made."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO session_calls VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    tool_call.agent_id,
                    tool_call.session_id,
                    tool_call.tool_name,
                    tool_call.original_task,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
        # Periodic cleanup to prevent unbounded table growth
        SessionTracker._cleanup_counter += 1
        if SessionTracker._cleanup_counter >= self._CLEANUP_EVERY:
            SessionTracker._cleanup_counter = 0
            try:
                await self.cleanup_old_records()
            except Exception as e:
                logger.debug("Session cleanup error (non-fatal): %s", e)

    async def cleanup_old_records(self, days: int = 30) -> int:
        """Delete session records older than `days`. Returns number of rows deleted."""
        await self._ensure_init()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM session_calls WHERE called_at < ?", (cutoff,)
            )
            await db.commit()
            deleted = cursor.rowcount
        if deleted:
            logger.info("Session cleanup: removed %d records older than %d days", deleted, days)
        return deleted

    async def get_session_stats(
        self,
        agent_id: str,
        window_minutes: int = 5,
        session_id: str | None = None,
    ) -> dict:
        """
        Return call stats within the last `window_minutes` for this agent.

        Returns:
            call_count       — total calls in window
            unique_tools     — number of distinct tools called
            tool_frequency   — {tool_name: count}
            calls_last_60s   — calls in last 60 seconds (velocity check)
        """
        await self._ensure_init()
        since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        since_60s = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

        conditions = ["agent_id = ?", "called_at >= ?"]
        params: list = [agent_id, since]
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        where = " AND ".join(conditions)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT tool_name FROM session_calls WHERE {where}",
                params,
            ) as cur:
                rows = await cur.fetchall()

            # Velocity: calls in last 60s
            vel_conditions = ["agent_id = ?", "called_at >= ?"]
            vel_params: list = [agent_id, since_60s]
            if session_id:
                vel_conditions.append("session_id = ?")
                vel_params.append(session_id)
            vel_where = " AND ".join(vel_conditions)
            async with db.execute(
                f"SELECT COUNT(*) FROM session_calls WHERE {vel_where}",
                vel_params,
            ) as cur:
                calls_60s: int = (await cur.fetchone())[0]  # type: ignore[index]

        tool_names = [r[0] for r in rows]
        freq: dict[str, int] = {}
        for t in tool_names:
            freq[t] = freq.get(t, 0) + 1

        return {
            "call_count": len(tool_names),
            "unique_tools": len(freq),
            "tool_frequency": freq,
            "calls_last_60s": calls_60s,
        }

    async def get_recent_calls(
        self,
        agent_id: str,
        session_id: str | None = None,
        limit: int = 3,
    ) -> list[dict]:
        """
        Return the last `limit` tool calls for this agent/session,
        oldest first, excluding the current call (which has not been
        recorded yet).

        Each dict has keys: tool_name, original_task, called_at.
        """
        await self._ensure_init()

        conditions = ["agent_id = ?"]
        params: list = [agent_id]
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        where = " AND ".join(conditions)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"""SELECT tool_name, original_task, called_at
                    FROM session_calls
                    WHERE {where}
                    ORDER BY called_at DESC
                    LIMIT ?""",
                params + [limit],
            ) as cur:
                rows = await cur.fetchall()

        return [
            {
                "tool_name": r[0],
                "original_task": r[1] or "",
                "called_at": r[2],
            }
            for r in reversed(rows)
        ]
