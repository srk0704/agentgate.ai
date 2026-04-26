from __future__ import annotations
import csv
import io
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from agentgate.models import Decision

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id                  TEXT PRIMARY KEY,
    call_id             TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    session_id          TEXT,
    tool_name           TEXT NOT NULL,
    args                TEXT NOT NULL,
    context             TEXT NOT NULL,
    original_task       TEXT,
    idempotency_key     TEXT,
    outcome             TEXT NOT NULL,
    reason              TEXT NOT NULL,
    risk_score          INTEGER,
    risk_reason         TEXT,
    injection_score     INTEGER,
    injection_reason    TEXT,
    attack_type         TEXT,
    anomaly_score       INTEGER,
    anomaly_reason      TEXT,
    blast_radius        TEXT,
    drift_score         INTEGER,
    drift_reason        TEXT,
    loop_score          INTEGER,
    loop_reason         TEXT,
    reliability_score   INTEGER,
    reliability_summary TEXT,
    human_decision      TEXT,
    human_reason        TEXT,
    policy_matched      TEXT,
    escalation_id       TEXT,
    latency_ms          REAL,
    decided_at          TEXT NOT NULL
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

CREATE_POLICY_CHANGES_TABLE = """
CREATE TABLE IF NOT EXISTS policy_changes (
    id             TEXT PRIMARY KEY,
    pattern_id     TEXT,
    pattern_type   TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    action         TEXT NOT NULL,
    before_value   TEXT,
    after_value    TEXT,
    metrics_before TEXT,
    metrics_after  TEXT,
    applied_at     TEXT NOT NULL,
    reverted_at    TEXT
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
            await db.execute(CREATE_POLICY_CHANGES_TABLE)
            # Migrate existing DBs — ignore error if columns already exist.
            for col in (
                "original_task TEXT", "session_id TEXT",
                "idempotency_key TEXT",
                "injection_score INTEGER", "injection_reason TEXT",
                "attack_type TEXT",
                "anomaly_score INTEGER", "anomaly_reason TEXT",
                "blast_radius TEXT",
                "drift_score INTEGER", "drift_reason TEXT",
                "loop_score INTEGER", "loop_reason TEXT",
                "reliability_score INTEGER", "reliability_summary TEXT",
                "risk_reason TEXT", "human_decision TEXT", "human_reason TEXT",
            ):
                try:
                    await db.execute(f"ALTER TABLE audit_log ADD COLUMN {col}")
                except Exception:
                    pass
            # Indexes for commonly-queried columns — safe to run on existing DBs.
            for ddl in (
                "CREATE INDEX IF NOT EXISTS idx_audit_decided_at ON audit_log(decided_at)",
                "CREATE INDEX IF NOT EXISTS idx_audit_agent_id ON audit_log(agent_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_tool_name ON audit_log(tool_name)",
                "CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_log(outcome)",
                "CREATE INDEX IF NOT EXISTS idx_audit_call_id ON audit_log(call_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_escalation_id ON audit_log(escalation_id)",
                "CREATE INDEX IF NOT EXISTS idx_audit_idempotency ON audit_log(idempotency_key) WHERE idempotency_key IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS idx_audit_human_decision ON audit_log(human_decision) WHERE human_decision IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS idx_policy_changes_applied ON policy_changes(applied_at)",
                "CREATE INDEX IF NOT EXISTS idx_policy_changes_tool ON policy_changes(tool_name)",
            ):
                try:
                    await db.execute(ddl)
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
                 drift_score, drift_reason, loop_score, loop_reason,
                 reliability_score, reliability_summary,
                 human_decision, human_reason,
                 policy_matched, escalation_id, latency_ms, decided_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    decision.drift_score,
                    decision.drift_reason,
                    decision.loop_score,
                    decision.loop_reason,
                    decision.reliability_score,
                    decision.reliability_summary,
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

            # Reliability events: any non-trivial component score elevated today.
            # Captures injection + anomaly (covers drift) + (future) loop signals.
            async with db.execute(
                """SELECT COUNT(*) FROM audit_log
                   WHERE decided_at >= ?
                     AND (injection_score >= 50 OR anomaly_score >= 50 OR risk_score >= 70)""",
                (today,),
            ) as cur:
                reliability_events: int = (await cur.fetchone())[0]  # type: ignore[index]

            # Average reliability score today (None if no decisions yet).
            async with db.execute(
                """SELECT AVG(reliability_score) FROM audit_log
                   WHERE decided_at >= ? AND reliability_score IS NOT NULL""",
                (today,),
            ) as cur:
                avg_row = await cur.fetchone()
            avg_reliability = avg_row[0] if avg_row and avg_row[0] is not None else None  # type: ignore[index]

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
            "reliability_events_today": reliability_events,
            "avg_reliability_score_today": round(avg_reliability) if avg_reliability is not None else None,
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

    async def update_escalation_outcome(
        self, escalation_id: str, outcome: str, human_decision: str, human_reason: str | None
    ) -> None:
        """Update audit_log entry outcome when a human approves or rejects an escalation."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE audit_log
                   SET outcome = ?, human_decision = ?, human_reason = ?
                   WHERE escalation_id = ?""",
                (outcome, human_decision, human_reason, escalation_id),
            )
            await db.commit()

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

    async def get_agent_health(
        self, since: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Per-agent reliability snapshot.

        For each agent_id seen since `since` (default: last 24 h):
          - decisions count
          - average reliability score (rounded; None if never scored)
          - intervention rate (blocked + escalated) / total
          - last_seen ISO timestamp
          - active_issues: aggregate score-type signals from the *recent* window only
            (last hour by default) so old, resolved issues do not stick around
          - worst component recorded in the window
        Sorted by health_score ascending (worst first).
        """
        await self._ensure_init()
        if since is None:
            since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        recent_since = (datetime.utcnow() - timedelta(hours=1)).isoformat()

        agents: dict[str, dict[str, Any]] = {}
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT agent_id,
                          COUNT(*) AS total,
                          AVG(reliability_score) AS avg_rel,
                          MAX(decided_at) AS last_seen,
                          SUM(CASE WHEN outcome = 'blocked' THEN 1 ELSE 0 END) AS blocked,
                          SUM(CASE WHEN outcome LIKE 'escalat%' THEN 1 ELSE 0 END) AS escalated
                   FROM audit_log
                   WHERE decided_at >= ?
                   GROUP BY agent_id""",
                (since,),
            ) as cur:
                rows = await cur.fetchall()
            for r in rows:
                d = dict(r)
                aid = d["agent_id"]
                total = d["total"] or 0
                blocked = d["blocked"] or 0
                escalated = d["escalated"] or 0
                avg_rel = d["avg_rel"]
                health_score = round(avg_rel) if avg_rel is not None else 100
                if health_score >= 90:
                    status = "Healthy"
                elif health_score >= 70:
                    status = "Caution"
                elif health_score >= 40:
                    status = "Degraded"
                else:
                    status = "Critical"
                agents[aid] = {
                    "agent_id": aid,
                    "health_score": health_score,
                    "health_status": status,
                    "decisions_today": total,
                    "intervention_rate": round((blocked + escalated) / total, 3) if total else 0.0,
                    "active_issues": [],
                    "last_seen": d["last_seen"],
                }

            # Pull active issues from the last hour: any score type elevated above 50
            async with db.execute(
                """SELECT agent_id, decided_at, risk_score, injection_score, anomaly_score, attack_type
                   FROM audit_log
                   WHERE decided_at >= ?
                     AND (risk_score >= 50 OR injection_score >= 50 OR anomaly_score >= 50)""",
                (recent_since,),
            ) as cur:
                event_rows = await cur.fetchall()

        issues_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
        for er in event_rows:
            ed = dict(er)
            aid = ed["agent_id"]
            ts = ed["decided_at"]
            buckets = issues_by_agent.setdefault(aid, {})
            for kind, score, label in (
                ("injection", ed.get("injection_score"),
                 f"Prompt injection ({ed['attack_type']})" if ed.get("attack_type") else "Prompt injection"),
                ("risk", ed.get("risk_score"), "High-risk action"),
                ("anomaly", ed.get("anomaly_score"), "Anomalous session behavior"),
            ):
                if score is None or score < 50:
                    continue
                bucket = buckets.setdefault(kind, {
                    "type": kind,
                    "description": label,
                    "first_seen": ts,
                    "occurrences": 0,
                })
                bucket["occurrences"] += 1
                if ts < bucket["first_seen"]:
                    bucket["first_seen"] = ts

        for aid, kinds in issues_by_agent.items():
            if aid in agents:
                agents[aid]["active_issues"] = list(kinds.values())

        return sorted(agents.values(), key=lambda a: a["health_score"])

    async def get_tool_metrics(
        self, tool_name: str, since: str, until: str | None = None
    ) -> dict[str, Any]:
        """Return outcome distribution for a tool between two ISO timestamps."""
        await self._ensure_init()
        params: list[Any] = [tool_name, since]
        until_clause = ""
        if until:
            until_clause = "AND decided_at < ?"
            params.append(until)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"""SELECT outcome, COUNT(*) FROM audit_log
                    WHERE tool_name = ? AND decided_at >= ? {until_clause}
                    GROUP BY outcome""",
                params,
            ) as cur:
                rows = await cur.fetchall()
        counts: dict[str, int] = {r[0]: r[1] for r in rows}
        total = sum(counts.values())
        escalated_total = (
            counts.get("escalated", 0)
            + counts.get("escalation_approved", 0)
            + counts.get("escalation_rejected", 0)
        )
        return {
            "total": total,
            "allowed": counts.get("allowed", 0),
            "blocked": counts.get("blocked", 0),
            "escalated": escalated_total,
            "escalation_rate": round(escalated_total / total * 100, 1) if total else 0.0,
            "block_rate": round(counts.get("blocked", 0) / total * 100, 1) if total else 0.0,
            "allow_rate": round(counts.get("allowed", 0) / total * 100, 1) if total else 0.0,
        }

    async def log_policy_change(self, data: dict) -> str:
        """Insert a policy change record. Returns the new change id."""
        await self._ensure_init()
        import uuid
        from datetime import datetime
        change_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO policy_changes
                   (id, pattern_id, pattern_type, tool_name, action,
                    before_value, after_value, metrics_before, applied_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    change_id,
                    data.get("pattern_id"),
                    data.get("pattern_type", "unknown"),
                    data.get("tool_name", ""),
                    data.get("action", ""),
                    data.get("before_value"),
                    data.get("after_value"),
                    data.get("metrics_before"),
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()
        return change_id

    async def update_policy_change_metrics(
        self, change_id: str, metrics_after: str
    ) -> None:
        """Store post-change metrics after measuring impact."""
        await self._ensure_init()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE policy_changes SET metrics_after = ? WHERE id = ?",
                (metrics_after, change_id),
            )
            await db.commit()

    async def get_policy_changes(
        self, tool_name: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Return recent policy changes, optionally filtered by tool."""
        await self._ensure_init()
        params: list[Any] = []
        where = ""
        if tool_name:
            where = "WHERE tool_name = ?"
            params.append(tool_name)
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM policy_changes {where} ORDER BY applied_at DESC LIMIT ?",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_by_call_ids(self, call_ids: list[str]) -> dict[str, dict]:
        """Fetch audit entries for multiple call_ids in one query. Returns {call_id: entry}."""
        if not call_ids:
            return {}
        await self._ensure_init()
        placeholders = ",".join("?" * len(call_ids))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM audit_log WHERE call_id IN ({placeholders}) ORDER BY decided_at DESC",
                call_ids,
            ) as cur:
                rows = await cur.fetchall()
        result: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            result.setdefault(d["call_id"], d)
        return result

    async def get_by_idempotency_key(
        self, idempotency_key: str, within_minutes: int = 5
    ) -> dict | None:
        """Return the most recent ALLOWED decision for this key within the time window."""
        await self._ensure_init()
        since = (datetime.utcnow() - timedelta(minutes=within_minutes)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM audit_log
                   WHERE idempotency_key = ? AND outcome = 'allowed' AND decided_at >= ?
                   ORDER BY decided_at DESC LIMIT 1""",
                (idempotency_key, since),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def export_csv(self, since: str | None = None) -> str:
        """Return audit log as a CSV string. Defaults to last 90 days to prevent OOM on large DBs."""
        await self._ensure_init()
        if since is None:
            from datetime import timedelta
            since = (datetime.utcnow() - timedelta(days=90)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_log WHERE decided_at >= ? ORDER BY decided_at DESC",
                (since,),
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
