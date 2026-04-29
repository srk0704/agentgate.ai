from __future__ import annotations
import asyncio
import json
import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite
import httpx

from agentgate.models import ToolCall

logger = logging.getLogger(__name__)

# How long an escalation stays pending before auto-reject.
# Default 300 s (5 min) gives a human realistic time to review during a demo
# or off-hours review queue. Override with AGENTGATE_ESCALATION_TIMEOUT_SEC.
DEFAULT_ESCALATION_TIMEOUT = int(os.getenv("AGENTGATE_ESCALATION_TIMEOUT_SEC", "300"))

# In-process store: {escalation_id: asyncio.Event}
_decisions: dict[str, asyncio.Event] = {}
_approved: dict[str, bool] = {}

CREATE_ESCALATION_TABLE = """
CREATE TABLE IF NOT EXISTS escalations (
    id              TEXT PRIMARY KEY,
    call_id         TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    args            TEXT NOT NULL,
    context         TEXT NOT NULL,
    risk_score      INTEGER,
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    decision        TEXT
);
"""


class EscalationQueue:
    """
    Manages escalations: stores in DB, notifies humans via Slack/email.
    Escalations stay pending until a human approves or rejects via the dashboard.
    """

    _db_path: str | None = None
    _initialized = False

    @classmethod
    def _get_db_path(cls) -> str:
        if cls._db_path:
            return cls._db_path
        return os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")

    @classmethod
    def configure(cls, db_path: str) -> None:
        """Set the database path before using."""
        cls._db_path = db_path
        cls._initialized = False  # re-init if path changes

    @classmethod
    async def _ensure_init(cls) -> None:
        if cls._initialized:
            return
        async with aiosqlite.connect(cls._get_db_path()) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(CREATE_ESCALATION_TABLE)
            await db.commit()
        cls._initialized = True

    @classmethod
    async def submit(
        cls,
        tool_call: ToolCall,
        risk_score: int,
        reason: str = "Requires human review",
    ) -> str:
        """
        Submit a tool call for human escalation.
        Returns escalation_id.
        Auto-rejects after 60 seconds if no decision.
        """
        await cls._ensure_init()
        escalation_id = str(uuid4())

        async with aiosqlite.connect(cls._get_db_path()) as db:
            await db.execute(
                """INSERT INTO escalations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL, NULL)""",
                (
                    escalation_id,
                    tool_call.call_id,
                    tool_call.tool_name,
                    tool_call.agent_id,
                    json.dumps(tool_call.args, default=str),
                    json.dumps(tool_call.context, default=str),
                    risk_score,
                    reason,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

        # Notify humans
        await cls._notify(escalation_id, tool_call, risk_score, reason)

        # Register event for wait_for_decision
        _decisions[escalation_id] = asyncio.Event()
        _approved[escalation_id] = False

        logger.info(
            "Escalation submitted: id=%s tool=%s risk=%d",
            escalation_id,
            tool_call.tool_name,
            risk_score,
        )

        return escalation_id

    @classmethod
    async def wait_for_decision(cls, escalation_id: str, timeout_sec: float | None = None) -> bool:
        """
        Wait for a human decision on an escalation.
        Returns True if approved, False if rejected/timeout.
        Default timeout is AGENTGATE_ESCALATION_TIMEOUT_SEC (300 s).
        """
        if timeout_sec is None:
            timeout_sec = DEFAULT_ESCALATION_TIMEOUT
        if escalation_id not in _decisions:
            _decisions[escalation_id] = asyncio.Event()

        try:
            await asyncio.wait_for(
                _decisions[escalation_id].wait(),
                timeout=timeout_sec,
            )
            return _approved.get(escalation_id, False)
        except asyncio.TimeoutError:
            logger.warning("Escalation timeout: %s — auto-rejecting", escalation_id)
            await cls._record_decision(escalation_id, False, "Timeout")
            return False

    @classmethod
    async def approve(cls, escalation_id: str) -> None:
        """Mark an escalation as approved."""
        await cls._record_decision(escalation_id, True, "Human approved")
        _approved[escalation_id] = True
        if escalation_id in _decisions:
            _decisions[escalation_id].set()

    @classmethod
    async def reject(cls, escalation_id: str) -> None:
        """Mark an escalation as rejected."""
        await cls._record_decision(escalation_id, False, "Human rejected")
        _approved[escalation_id] = False
        if escalation_id in _decisions:
            _decisions[escalation_id].set()

    @classmethod
    async def _record_decision(
        cls, escalation_id: str, approved: bool, reason: str
    ) -> None:
        """Record the decision in the database."""
        await cls._ensure_init()
        status = "approved" if approved else "rejected"
        async with aiosqlite.connect(cls._get_db_path()) as db:
            await db.execute(
                """UPDATE escalations
                   SET status = ?, decided_at = ?, decision = ?
                   WHERE id = ?""",
                (status, datetime.utcnow().isoformat(), reason, escalation_id),
            )
            await db.commit()

    @classmethod
    async def _notify(
        cls,
        escalation_id: str,
        tool_call: ToolCall,
        risk_score: int,
        reason: str,
    ) -> None:
        """Notify humans via Slack and/or email."""
        message = (
            f"🚨 *Escalation Required*\n"
            f"ID: `{escalation_id}`\n"
            f"Tool: `{tool_call.tool_name}`\n"
            f"Agent: `{tool_call.agent_id}`\n"
            f"Risk Score: {risk_score}/100\n"
            f"Reason: {reason}\n"
            f"Args: ```{json.dumps(tool_call.args, indent=2, default=str)}```\n"
            f"\nApprove: `/approve {escalation_id}`\n"
            f"Reject: `/reject {escalation_id}`"
        )

        # Try Slack
        slack_url = os.getenv("SLACK_WEBHOOK_URL")
        if slack_url:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        slack_url,
                        json={
                            "text": message,
                            "blocks": [
                                {
                                    "type": "section",
                                    "text": {
                                        "type": "mrkdwn",
                                        "text": message,
                                    },
                                }
                            ],
                        },
                    )
            except Exception as e:
                logger.error("Slack notification failed: %s", e)

        # Try email — run in thread pool to avoid blocking the async event loop
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASSWORD")
        escalation_email = os.getenv("ESCALATION_EMAIL")

        if smtp_host and escalation_email:
            try:
                msg = MIMEText(
                    f"Tool: {tool_call.tool_name}\n"
                    f"Agent: {tool_call.agent_id}\n"
                    f"Risk: {risk_score}/100\n"
                    f"Reason: {reason}\n\n"
                    f"Args:\n{json.dumps(tool_call.args, indent=2, default=str)}\n\n"
                    f"Escalation ID: {escalation_id}\n"
                )
                msg["Subject"] = f"[AgentGate] Escalation: {tool_call.tool_name}"
                msg["From"] = smtp_user
                msg["To"] = escalation_email

                def _send() -> None:
                    with smtplib.SMTP(smtp_host, smtp_port) as server:
                        server.starttls()
                        if smtp_user and smtp_pass:
                            server.login(smtp_user, smtp_pass)
                        server.send_message(msg)

                await asyncio.to_thread(_send)
            except Exception as e:
                logger.error("Email notification failed: %s", e)

    @classmethod
    async def _auto_reject(cls, escalation_id: str, timeout_sec: float | None = None) -> None:
        """Auto-reject after timeout. Default AGENTGATE_ESCALATION_TIMEOUT_SEC."""
        if timeout_sec is None:
            timeout_sec = DEFAULT_ESCALATION_TIMEOUT
        await asyncio.sleep(timeout_sec)
        if escalation_id in _decisions and not _decisions[escalation_id].is_set():
            await cls.reject(escalation_id)

    @classmethod
    async def recent(cls, limit: int = 100) -> list[dict]:
        """Get recent escalations."""
        await cls._ensure_init()
        async with aiosqlite.connect(cls._get_db_path()) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM escalations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    @classmethod
    async def get_by_id(cls, escalation_id: str) -> dict | None:
        """Get a specific escalation."""
        await cls._ensure_init()
        async with aiosqlite.connect(cls._get_db_path()) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM escalations WHERE id = ?", (escalation_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None
