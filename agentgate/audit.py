from __future__ import annotations
import csv
import io
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import aiosqlite

from agentgate.models import Decision

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id               TEXT PRIMARY KEY,
    call_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,
    tool_name        TEXT NOT NULL,
    args             TEXT NOT NULL,
    context          TEXT NOT NULL,
    original_task    TEXT,
    idempotency_key  TEXT,
    outcome          TEXT NOT NULL,
    reason           TEXT NOT NULL,
    risk_score       INTEGER,
    risk_reason      TEXT,
    injection_score  INTEGER,
    injection_reason TEXT,
    attack_type      TEXT,
    anomaly_score    INTEGER,
    anomaly_reason   TEXT,
    blast_radius     TEXT,
    human_decision   TEXT,
    human_reason     TEXT,
    policy_matched   TEXT,
    escalation_id    TEXT,
    latency_ms       REAL,
    decided_at       TEXT NOT NULL
);
"""

CREATE_PII_SCAN_TABLE = """
CREATE TABLE IF NOT EXISTS pii_scan_log (
    id             TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    pii_found      TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    safe           INTEGER NOT NULL,
    scanned_at     TEXT NOT NULL
);
"""


class AuditLogger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_TABLE)
            await db.execute(CREATE_PII_SCAN_TABLE)
            # Migrate existing DBs — ignore error if columns already exist.
            for col in (
                "original_task TEXT", "session_id TEXT",
                "idempotency_key TEXT",
                "injection_score INTEGER", "injection_reason TEXT",
                "attack_type TEXT",
                "anomaly_score INTEGER", "anomaly_reason TEXT",
                "blast_radius TEXT",
                "risk_reason TEXT", "human_decision TEXT", "human_reason TEXT",
            ):
                try:
                    await db.execute(f"ALTER TABLE audit_log ADD COLUMN {col}")
                except Exception:
                    pass
            await db.commit()
        self._initialized = True

    async def log(self, decision: Decision) -> None:
        await self._ensure_init()
        tc = decision.tool_call
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO audit_log
                (id, call_id, agent_id, session_id, tool_name, args, context, original_task,
                 idempotency_key, outcome, reason,
                 risk_score, risk_reason, injection_score, injection_reason, attack_type,
                 anomaly_score, anomaly_reason, blast_radius,
                 human_decision, human_reason,
                 policy_matched, escalation_id, latency_ms, decided_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"{tc.call_id}-{decision.decided_at.timestamp()}",
                    tc.call_id,
                    tc.agent_id,
                    tc.session_id,
                    tc.tool_name,
                    json.dumps(tc.args, default=str),
                    json.dumps(tc.context, default=str),
                    tc.original_task,
                    tc.idempotency_key,
                    decision.outcome.value,
                    decision.reason,
                    decision.risk_score,
                    decision.risk_reason,
                    decision.injection_score,
                    decision.injection_reason,
                    decision.attack_type,
                    decision.anomaly_score,
                    decision.anomaly_reason,
                    json.dumps(decision.blast_radius) if decision.blast_radius else None,
                    decision.human_decision,
                    decision.human_reason,
                    decision.policy_matched,
                    decision.escalation_id,
                    decision.latency_ms,
                    decision.decided_at.isoformat(),
                ),
            )
            await db.commit()
        logger.debug(
            "Audit: tool=%s outcome=%s latency=%.1fms",
            tc.tool_name, decision.outcome.value, decision.latency_ms
        )

    async def recent(self, limit: int = 100) -> list[dict]:
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_log ORDER BY decided_at DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def since(self, timestamp: str, limit: int = 100) -> list[dict]:
        """Return entries with decided_at strictly after timestamp (ISO string)."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_log WHERE decided_at > ? ORDER BY decided_at ASC LIMIT ?",
                (timestamp, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_stats(self, injection_threshold: int = 70) -> dict[str, Any]:
        """Return dashboard stats for today."""
        await self._ensure_init()
        today = date.today().isoformat()  # "YYYY-MM-DD" — ISO prefix comparison works in SQLite
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM audit_log WHERE decided_at >= ?", (today,)
            ) as cur:
                total: int = (await cur.fetchone())[0]  # type: ignore[index]

            async with db.execute(
                "SELECT COUNT(*) FROM audit_log WHERE decided_at >= ? AND outcome = 'blocked'",
                (today,),
            ) as cur:
                blocked: int = (await cur.fetchone())[0]  # type: ignore[index]

            async with db.execute(
                "SELECT COUNT(*) FROM audit_log WHERE decided_at >= ? AND outcome LIKE 'escalat%'",
                (today,),
            ) as cur:
                escalated: int = (await cur.fetchone())[0]  # type: ignore[index]

            async with db.execute(
                "SELECT COUNT(*) FROM audit_log WHERE decided_at >= ? AND injection_score >= ?",
                (today, injection_threshold),
            ) as cur:
                injections: int = (await cur.fetchone())[0]  # type: ignore[index]

            # Active agents in last 5 min (proxy for "live agent count")
            from datetime import datetime, timedelta
            five_min_ago = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
            async with db.execute(
                "SELECT COUNT(DISTINCT agent_id) FROM audit_log WHERE decided_at >= ?",
                (five_min_ago,),
            ) as cur:
                active_agents: int = (await cur.fetchone())[0]  # type: ignore[index]

        return {
            "total_actions_today": total,
            "block_rate": round(blocked / total * 100, 1) if total else 0.0,
            "escalation_rate": round(escalated / total * 100, 1) if total else 0.0,
            "injection_attempts_today": injections,
            "active_agents": active_agents,
        }

    async def get_paginated(
        self,
        agent_id: str | None = None,
        tool_name: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Paginated query with optional filters."""
        await self._ensure_init()
        conditions: list[str] = []
        params: list[Any] = []
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM audit_log {where} ORDER BY decided_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def log_pii_scan(self, agent_id: str, tool_name: str, result: dict) -> None:
        """Log a PII output scan result."""
        await self._ensure_init()
        import uuid
        from datetime import datetime
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO pii_scan_log (id, agent_id, tool_name, pii_found, recommendation, safe, scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    agent_id,
                    tool_name,
                    json.dumps(result.get("pii_found", [])),
                    result.get("recommendation", "allow"),
                    1 if result.get("safe") else 0,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def get_decision_count(
        self,
        agent_id: str | None = None,
        since: str | None = None,
    ) -> int:
        """
        Count decisions. Used for usage tracking.
        agent_id: filter by agent (for per-agent billing).
        since: ISO timestamp (for billing period).
        """
        await self._ensure_init()
        conditions: list[str] = []
        params: list[Any] = []
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if since:
            conditions.append("decided_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM audit_log {where}", params
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0  # type: ignore[index]

    async def get_failed_open_count(self, since: str | None = None) -> int:
        """Count FAILED_OPEN outcomes — used for health/detailed endpoint."""
        await self._ensure_init()
        params: list[Any] = ["failed_open"]
        where = "WHERE outcome = ?"
        if since:
            where += " AND decided_at >= ?"
            params.append(since)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM audit_log {where}", params
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0  # type: ignore[index]

    async def get_by_outcome(self, since: str | None = None) -> dict[str, int]:
        """Return decision counts grouped by outcome."""
        await self._ensure_init()
        params: list[Any] = []
        where = ""
        if since:
            where = "WHERE decided_at >= ?"
            params.append(since)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT outcome, COUNT(*) FROM audit_log {where} GROUP BY outcome",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_by_agent(self, since: str | None = None) -> dict[str, int]:
        """Return decision counts grouped by agent_id."""
        await self._ensure_init()
        params: list[Any] = []
        where = ""
        if since:
            where = "WHERE decided_at >= ?"
            params.append(since)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT agent_id, COUNT(*) FROM audit_log {where} GROUP BY agent_id",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_by_call_id(self, call_id: str) -> dict | None:
        """Fetch the most recent audit entry for a given call_id."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_log WHERE call_id = ? ORDER BY decided_at DESC LIMIT 1",
                (call_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_by_agent_outcomes(
        self, since: str | None = None
    ) -> dict[str, dict[str, int]]:
        """
        Returns per-agent outcome counts.
        {
          "payment-agent-prod": {"allowed": 1520, "blocked": 210, "escalated": 117, "total": 1847},
          ...
        }
        Query: SELECT agent_id, outcome, COUNT(*) FROM audit_log GROUP BY agent_id, outcome
        """
        await self._ensure_init()
        params: list[Any] = []
        where = ""
        if since:
            where = "WHERE decided_at >= ?"
            params.append(since)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT agent_id, outcome, COUNT(*) FROM audit_log {where} GROUP BY agent_id, outcome",
                params,
            ) as cur:
                rows = await cur.fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            aid, outcome_val, cnt = row[0], row[1], row[2]
            if aid not in result:
                result[aid] = {}
            result[aid][outcome_val] = cnt
        for agent_data in result.values():
            agent_data["total"] = sum(v for k, v in agent_data.items())
        return result

    async def export_csv(self) -> str:
        """Return full audit log as a CSV string."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_log ORDER BY decided_at DESC"
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return ""
        dicts = [dict(r) for r in rows]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(dicts[0].keys()))
        writer.writeheader()
        writer.writerows(dicts)
        return buf.getvalue()
