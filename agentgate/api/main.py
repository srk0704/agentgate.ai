from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiosqlite
import httpx

from dotenv import find_dotenv, load_dotenv
# override=False so explicit env vars (e.g. set on the uvicorn command line or
# by pytest's monkeypatch.setenv) win over .env defaults. usecwd=True so a
# source/editable install searches from the customer's working directory
# instead of walking up from this file's own location in the package source.
load_dotenv(find_dotenv(usecwd=True), override=False)

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from agentgate.audit import AuditLogger
from agentgate.escalation import EscalationQueue

logger = logging.getLogger(__name__)

app = FastAPI(title="AgentGate API", version="0.8.5")

# Paths that never require an API key
_AUTH_SKIP = frozenset({"/health", "/v2"})


class _ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header on all endpoints except the skip list."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        api_key = os.getenv("AGENTGATE_API_KEY")
        agentgate_env = os.getenv("AGENTGATE_ENV", "production")

        if not api_key and agentgate_env == "production":
            raise RuntimeError(
                "AGENTGATE_API_KEY must be set in production. "
                "Set AGENTGATE_ENV=development to run without auth."
            )

        if api_key and request.url.path not in _AUTH_SKIP:
            provided = request.headers.get("X-API-Key")
            if provided != api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing X-API-Key"},
                )
        return await call_next(request)


app.add_middleware(_ApiKeyMiddleware)


@app.on_event("startup")
async def _startup() -> None:
    """Configure shared services with the correct DB path on startup."""
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    EscalationQueue.configure(db_path)

    api_key = os.getenv("AGENTGATE_API_KEY")
    if not api_key:
        logger.warning(
            "⚠ AGENTGATE_API_KEY is not set. "
            "All API endpoints are publicly accessible. "
            "Set this variable before production deployment."
        )


def _audit() -> AuditLogger:
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    return AuditLogger(db_path)


def _parse_financial_impact(blast_radius_json: str | None) -> float:
    """Coerce the `financial_impact` field on a stored blast_radius blob.

    The blast-radius estimator writes one of:
      {"financial_impact": "unknown", ...}
      {"financial_impact": "$50,000", ...}
      {"financial_impact": 50000, ...}
    Returns 0.0 for unknown / missing / unparseable values so callers can
    safely sum across rows without an extra None check.
    """
    if not blast_radius_json:
        return 0.0
    try:
        br = json.loads(blast_radius_json)
        fi = br.get("financial_impact", 0)
        if isinstance(fi, (int, float)):
            return float(fi)
        if isinstance(fi, str):
            cleaned = fi.replace("$", "").replace(",", "").strip()
            if cleaned.lower() in ("unknown", "none", ""):
                return 0.0
            return float(cleaned)
    except Exception:
        return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/v2", include_in_schema=False)
async def dashboard_v2() -> FileResponse:
    """Serve the v2 single-page dashboard (sidebar + narrative hero)."""
    p = Path(__file__).parent.parent / "dashboard" / "index_v2.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="v2 dashboard not built")
    return FileResponse(p, media_type="text/html")


@app.get("/landing", include_in_schema=False)
async def landing_page() -> FileResponse:
    """Serve the public landing page (marketing site)."""
    p = Path(__file__).parent.parent / "landing" / "index.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Landing page not built")
    return FileResponse(p, media_type="text/html")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@app.get("/dashboard/stats")
async def dashboard_stats(since: Optional[str] = Query(default=None)) -> dict:
    """Aggregate stats for the dashboard metric cards."""
    audit = _audit()
    stats = await audit.get_stats(since=since)
    recent = await audit.recent(50)
    pending = await EscalationQueue.recent(limit=200)
    pending_only = [e for e in pending if e["status"] == "pending"]

    # Enrich pending escalations with audit log data — batch fetch to avoid N+1
    call_ids = [e["call_id"] for e in pending_only if e.get("call_id")]
    audit_by_call_id = await audit.get_by_call_ids(call_ids) if call_ids else {}
    for esc in pending_only:
        audit_entry = audit_by_call_id.get(esc.get("call_id", ""))
        if audit_entry:
            for field in (
                "original_task", "injection_score", "injection_reason",
                "attack_type", "blast_radius", "policy_matched",
                "risk_reason", "anomaly_score", "anomaly_reason",
            ):
                esc.setdefault(field, audit_entry.get(field))

    # Flagged sessions: pick highest anomaly_score per agent from recent decisions
    flagged: dict[str, dict] = {}
    for d in recent:
        score = d.get("anomaly_score") or 0
        if score > 30:
            key = d["agent_id"]
            if key not in flagged or flagged[key]["anomaly_score"] < score:
                flagged[key] = d
    flagged_sessions = sorted(flagged.values(), key=lambda x: x["anomaly_score"], reverse=True)

    # Financial impact protected today: sum financial_impact across every
    # escalated decision (pending + resolved). The dashboard's FINANCIAL
    # PROTECTED card needs a server-computed total so it doesn't depend on
    # whatever subset of decisions happens to be loaded client-side.
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    financial_protected = 0.0
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """SELECT blast_radius FROM audit_log
                   WHERE decided_at >= ?
                     AND outcome IN ('escalated', 'escalation_approved',
                                     'escalation_rejected')""",
                (today_start,),
            ) as cursor:
                rows = await cursor.fetchall()
        financial_protected = sum(_parse_financial_impact(r[0]) for r in rows)
    except Exception as e:
        logger.warning("financial_protected_today query failed: %s", e)

    return {
        **stats,
        "recent_decisions": recent,
        "pending_escalations": pending_only,
        "flagged_sessions": flagged_sessions,
        "financial_protected_today": financial_protected,
    }


# ---------------------------------------------------------------------------
# WebSocket live feed
# ---------------------------------------------------------------------------

_feed_queues: set[asyncio.Queue] = set()  # type: ignore[type-arg]


async def broadcast_decision(entry: dict) -> None:
    """Push a new audit entry to all connected WebSocket clients."""
    dead: list[asyncio.Queue] = []
    for q in _feed_queues:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _feed_queues.discard(q)


@app.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket) -> None:
    """
    Stream new audit log entries in real time.
    On connect: sends last 50 entries as type="initial".
    Thereafter: sends each new entry as type="decision" (polled every 1 s).
    """
    await websocket.accept()
    audit = _audit()

    initial = await audit.recent(50)
    await websocket.send_json({"type": "initial", "decisions": initial})

    last_ts: str = initial[0]["decided_at"] if initial else "1970-01-01T00:00:00"

    q: asyncio.Queue = asyncio.Queue(maxsize=200)  # type: ignore[type-arg]
    _feed_queues.add(q)
    try:
        while True:
            await asyncio.sleep(1)
            new_entries = await audit.since(last_ts)
            for entry in new_entries:
                await websocket.send_json({"type": "decision", "decision": entry})
                last_ts = entry["decided_at"]
            while not q.empty():
                entry = q.get_nowait()
                if entry["decided_at"] > last_ts:
                    await websocket.send_json({"type": "decision", "decision": entry})
                    last_ts = entry["decided_at"]
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _feed_queues.discard(q)


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


class ApprovalRequest(BaseModel):
    reason: Optional[str] = None


class EscalationConflict(Exception):
    """Raised when an escalation can't be decided — not found or already decided."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


async def _decide_escalation(
    escalation_id: str, approved: bool, reason: Optional[str]
) -> dict:
    """Shared approve/reject path for the REST endpoints and Slack
    interactions — same validation, same audit trail, regardless of who
    clicked the button."""
    escalation = await EscalationQueue.get_by_id(escalation_id)
    if not escalation:
        raise EscalationConflict(404, "Escalation not found")
    if escalation["status"] != "pending":
        raise EscalationConflict(
            400, f"Escalation is already {escalation['status']}"
        )
    if approved:
        await EscalationQueue.approve(escalation_id)
        outcome, status = "escalation_approved", "approved"
    else:
        await EscalationQueue.reject(escalation_id)
        outcome, status = "escalation_rejected", "rejected"
    audit = _audit()
    await audit.update_escalation_outcome(
        escalation_id, outcome, status, reason
    )
    return {"escalation_id": escalation_id, "status": status, "reason": reason}


@app.post("/escalations/{escalation_id}/approve")
async def approve_escalation(escalation_id: str, body: ApprovalRequest) -> dict:
    try:
        return await _decide_escalation(escalation_id, True, body.reason)
    except EscalationConflict as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@app.post("/escalations/{escalation_id}/reject")
async def reject_escalation(escalation_id: str, body: ApprovalRequest) -> dict:
    try:
        return await _decide_escalation(escalation_id, False, body.reason)
    except EscalationConflict as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


def _verify_slack_signature(
    signing_secret: str, timestamp: str, body: bytes, signature: str
) -> bool:
    """Verify Slack's HMAC request signature (Slack's documented scheme).

    Without this, anyone who learns the /slack/interactions URL — not hard,
    Slack app manifests and reverse proxies leak URLs — could approve or
    reject arbitrary escalations, including real payments, with a bare
    HTTP POST and no Slack workspace membership at all.
    """
    import hashlib
    import hmac as hmac_lib

    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except (TypeError, ValueError):
        return False
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    computed = "v0=" + hmac_lib.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    return hmac_lib.compare_digest(computed, signature or "")


@app.post("/slack/interactions")
async def slack_interactions(request: Request) -> Response:
    """Receives Slack's interactive-button callback (approve/reject clicks
    on an escalation message) and applies the same decision path as the
    REST endpoints.

    Requires SLACK_SIGNING_SECRET to be set — see README for the Slack
    app setup (enable Interactivity, point the Request URL here).
    """
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(
            status_code=501,
            detail="SLACK_SIGNING_SECRET is not configured on this server",
        )

    raw_body = await request.body()
    if not _verify_slack_signature(
        signing_secret,
        request.headers.get("X-Slack-Request-Timestamp", ""),
        raw_body,
        request.headers.get("X-Slack-Signature", ""),
    ):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    # Parse the urlencoded body directly instead of request.form() — Slack
    # always sends application/x-www-form-urlencoded here, and Starlette's
    # generic form parser requires the python-multipart dependency for a
    # format we never receive.
    from urllib.parse import parse_qs
    fields = parse_qs(raw_body.decode("utf-8"))
    payload = json.loads(fields.get("payload", ["{}"])[0])
    action = (payload.get("actions") or [{}])[0]
    action_id = action.get("action_id", "")
    escalation_id = action.get("value", "")
    user = payload.get("user", {}).get("username", "someone")
    response_url = payload.get("response_url")

    if action_id not in ("approve_escalation", "reject_escalation"):
        raise HTTPException(status_code=400, detail=f"Unknown action_id: {action_id}")

    approved = action_id == "approve_escalation"
    try:
        await _decide_escalation(escalation_id, approved, f"Decided via Slack by {user}")
        verb = "✅ Approved" if approved else "❌ Rejected"
        text = f"{verb} by *{user}* — escalation `{escalation_id}`"
    except EscalationConflict as e:
        text = f"⚠️ Could not record decision: {e.detail}"

    # Replace the original message so the buttons don't stay clickable and
    # every reviewer sees who already acted on it.
    if response_url:
        async with httpx.AsyncClient() as client:
            await client.post(
                response_url,
                json={"replace_original": "true", "text": text},
            )

    return Response(status_code=200)


@app.get("/escalations/{escalation_id}")
async def get_escalation(escalation_id: str) -> dict:
    escalation = await EscalationQueue.get_by_id(escalation_id)
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    return escalation


@app.get("/escalations")
async def list_escalations(limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    escalations = await EscalationQueue.recent(limit=limit)
    return {"count": len(escalations), "escalations": escalations}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@app.get("/audit")
async def list_audit(
    agent_id: Optional[str] = Query(default=None),
    tool_name: Optional[str] = Query(default=None),
    outcome: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    since: Optional[str] = Query(default=None),
) -> dict:
    """Paginated audit log with optional filters. Compliance export endpoint."""
    audit = _audit()
    entries = await audit.get_paginated(
        agent_id=agent_id,
        tool_name=tool_name,
        outcome=outcome,
        limit=limit,
        offset=offset,
        since=since,
    )
    return {"count": len(entries), "offset": offset, "entries": entries}


@app.get("/audit/export")
async def export_audit(since: Optional[str] = Query(default=None, description="ISO date, e.g. 2024-01-01. Defaults to last 90 days.")) -> Response:
    """Download audit log as CSV. Optionally scoped to entries after `since`."""
    audit = _audit()
    csv_data = await audit.export_csv(since=since)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=agentgate_audit.csv"},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/agent-loops")
async def health_agent_loops() -> dict:
    """Recent agent sessions stuck in retry storms or sequence loops."""
    import aiosqlite
    from datetime import datetime, timezone
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    rows: list[dict] = []
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            # MAX(loop_score) is correct (max of integers = worst loop seen).
            # MAX(loop_reason) was lexicographic — we want the most frequent
            # reason instead, via a correlated subquery.
            async with db.execute(
                """SELECT a.agent_id, a.session_id,
                          MAX(a.loop_score) AS loop_score,
                          (SELECT loop_reason
                             FROM audit_log a2
                             WHERE a2.agent_id = a.agent_id
                               AND a2.session_id IS a.session_id
                               AND a2.loop_score > 50
                               AND a2.decided_at > datetime('now', '-1 hour')
                             GROUP BY loop_reason
                             ORDER BY COUNT(*) DESC
                             LIMIT 1) AS reason,
                          MIN(a.decided_at) AS first_detected
                   FROM audit_log a
                   WHERE a.loop_score > 50
                     AND a.decided_at > datetime('now', '-1 hour')
                   GROUP BY a.agent_id, a.session_id
                   ORDER BY loop_score DESC""",
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception as e:
        logger.debug("health/agent-loops query failed: %s", e)
    return {
        "agents_in_loops": rows,
        "total": len(rows),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/agents")
async def health_agents() -> dict:
    """
    Per-agent reliability snapshot for external monitoring.
    Use this to alert when any agent's health_score drops below a threshold.
    """
    audit = _audit()
    agents = await audit.get_agent_health()
    summary = {
        "total_agents": len(agents),
        "healthy": sum(1 for a in agents if a["health_status"] == "Healthy"),
        "caution": sum(1 for a in agents if a["health_status"] == "Caution"),
        "degraded": sum(1 for a in agents if a["health_status"] == "Degraded"),
        "critical": sum(1 for a in agents if a["health_status"] == "Critical"),
    }
    return {"summary": summary, "agents": agents}


@app.get("/health/detailed")
async def health_detailed() -> dict:
    """
    Detailed health check for monitoring systems.
    Returns component-level status and today's decision metrics.
    """
    audit = _audit()
    compliance_mode = os.getenv("AGENTGATE_COMPLIANCE_MODE", "false").lower() == "true"
    today = __import__("datetime").date.today().isoformat()

    db_status = "ok"
    decisions_today = 0
    failed_open_today = 0
    try:
        decisions_today = await audit.get_decision_count(since=today)
        failed_open_today = await audit.get_failed_open_count(since=today)
    except Exception:
        db_status = "error"

    llm_status = "ok"
    if compliance_mode:
        llm_status = "disabled (compliance mode)"
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            llm_status = "unavailable (no API key)"

    if db_status == "error":
        overall = "error"
    elif llm_status.startswith("unavailable"):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "components": {
            "policy_engine": "ok",
            "database": db_status,
            "llm_api": llm_status,
            "compliance_mode": compliance_mode,
        },
        "decisions_today": decisions_today,
        "failed_open_today": failed_open_today,
    }


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------


@app.get("/usage")
async def usage() -> dict:
    """
    Decision counts for usage tracking and billing.
    Includes per-agent outcome breakdown.
    """
    from datetime import date

    audit = _audit()
    today = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()

    total = await audit.get_decision_count()
    decisions_today = await audit.get_decision_count(since=today)
    decisions_this_month = await audit.get_decision_count(since=month_start)
    by_agent = await audit.get_by_agent()
    by_outcome = await audit.get_by_outcome()
    by_agent_outcomes = await audit.get_by_agent_outcomes()

    # Reliability rollup — same window as the dashboard health card
    stats_today = await audit.get_stats()
    agent_health = await audit.get_agent_health(since=today)
    reliability_by_agent = {
        a["agent_id"]: {
            "avg_reliability": a["health_score"],
            "trend": "stable",
            "worst_event": (a["active_issues"][0]["type"] + "_score_active") if a["active_issues"] else None,
        }
        for a in agent_health
    }

    return {
        "total_decisions": total,
        "decisions_today": decisions_today,
        "decisions_this_month": decisions_this_month,
        "by_agent": by_agent,
        "by_outcome": by_outcome,
        "by_agent_outcomes": by_agent_outcomes,
        "avg_reliability_score_today": stats_today.get("avg_reliability_score_today"),
        "reliability_by_agent": reliability_by_agent,
    }


# ---------------------------------------------------------------------------
# PII output scanning
# ---------------------------------------------------------------------------


class ScanOutputRequest(BaseModel):
    output: str
    tool_name: str
    agent_id: str = "unknown"


@app.post("/scan/output")
async def scan_output(body: ScanOutputRequest) -> dict:
    """
    Scan agent output text for PII before returning it to the caller.
    Returns recommendation: allow | redact | block.
    """
    from agentgate.pii_detector import PiiDetector

    detector = PiiDetector()
    has_pii, findings = await detector.scan(body.output)

    if not has_pii:
        result = {
            "safe": True,
            "pii_found": [],
            "recommendation": "allow",
            "redacted_output": None,
        }
    else:
        read_only_prefixes = ("get_", "view_", "fetch_", "read_", "list_", "search_")
        is_read_only = any(body.tool_name.startswith(p) for p in read_only_prefixes)
        if is_read_only:
            redacted = detector.redact(body.output, findings)
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

    audit = _audit()
    await audit.log_pii_scan(body.agent_id, body.tool_name, result)
    return result


class ScanToolResultRequest(BaseModel):
    tool_result: str
    tool_name: str
    agent_id: str = "unknown"
    call_id: Optional[str] = None
    success: bool = True


@app.post("/scan/tool-result")
async def scan_tool_result(body: ScanToolResultRequest) -> dict:
    """
    Scan a tool's return value for hidden instructions before the agent
    reads it — the post-execution detection boundary (indirect prompt
    injection embedded in a webpage, email, or API response the agent
    reads back). For non-Python integrations; Python callers should use
    GatewayClient.scan_tool_result() directly.
    """
    from agentgate.output_logger import OutputLogger

    logger_out = OutputLogger(os.getenv("AGENTGATE_DB_PATH", "./agentgate.db"))
    score, reason = await logger_out.log_tool_result(
        call_id=body.call_id or str(uuid4()),
        tool_name=body.tool_name,
        tool_result=body.tool_result,
        agent_id=body.agent_id,
        success=body.success,
    )
    threshold = int(os.getenv("AGENTGATE_INJECTION_THRESHOLD_BLOCK", "70"))
    return {
        "safe": score < threshold,
        "injection_score": score,
        "injection_reason": reason,
    }


# ---------------------------------------------------------------------------
# Output log
# ---------------------------------------------------------------------------


@app.get("/output-log")
async def output_log(agent_id: Optional[str] = Query(default=None), limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    from agentgate.output_logger import OutputLogger
    logger_out = OutputLogger(os.getenv("AGENTGATE_DB_PATH", "./agentgate.db"))
    entries = await logger_out.recent(limit=limit)
    if agent_id:
        entries = [e for e in entries if e.get("agent_id") == agent_id]
    return {"count": len(entries), "entries": entries}


@app.get("/pii-scan-log")
async def pii_scan_log(agent_id: Optional[str] = Query(default=None), limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    audit = _audit()
    entries = await audit.recent_pii_scans(limit=limit)
    if agent_id:
        entries = [e for e in entries if e.get("agent_id") == agent_id]
    return {"count": len(entries), "entries": entries}


# ---------------------------------------------------------------------------
# Learning / patterns
# ---------------------------------------------------------------------------

_patterns_cache: dict = {"ts": 0.0, "data": []}


@app.get("/patterns")
async def get_patterns() -> dict:
    import time
    from agentgate.pattern_analyzer import PatternAnalyzer
    from agentgate.policy import PolicyLoader
    global _patterns_cache
    if time.time() - _patterns_cache["ts"] < 300:
        return {"count": len(_patterns_cache["data"]), "patterns": _patterns_cache["data"]}
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    policy_path = os.getenv("AGENTGATE_POLICY_PATH", "./policies.yaml")
    # Load current policies so analyzer can derive data-based thresholds
    try:
        loader = PolicyLoader(policy_path)
        policies = loader._policies
    except Exception:
        policies = None
    analyzer = PatternAnalyzer(db_path)
    patterns = await analyzer.analyze(policies=policies)
    serialized = [
        {
            "id": p.id,
            "pattern_type": p.pattern_type.value,
            "tool_name": p.tool_name,
            "description": p.description,
            "evidence": p.evidence,
            "suggestion": p.suggestion,
            "suggested_action": p.suggested_action,
            "confidence": round(p.confidence, 3),
            "impact": p.impact,
            "auto_applicable": p.auto_applicable,
            "created_at": p.created_at,
        }
        for p in patterns
    ]
    _patterns_cache = {"ts": time.time(), "data": serialized}
    return {"count": len(serialized), "patterns": serialized}


@app.post("/patterns/apply")
async def apply_pattern_endpoint(body: dict) -> dict:
    from agentgate.pattern_analyzer import Pattern, PatternType
    from agentgate.learning_engine import LearningEngine
    from agentgate.client import GatewayClient
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    policy_path = os.getenv("AGENTGATE_POLICY_PATH", "./policies.yaml")
    gate = GatewayClient(policy_path=policy_path, db_path=db_path, fail_open=True)
    engine = LearningEngine(gateway=gate, db_path=db_path)

    try:
        pattern = Pattern(
            id=body.get("id", ""),
            pattern_type=PatternType(body.get("pattern_type", "over_escalation")),
            tool_name=body.get("tool_name", ""),
            description=body.get("description", ""),
            evidence=body.get("evidence", {}),
            suggestion=body.get("suggestion", ""),
            suggested_action=body.get("suggested_action", {}),
            confidence=float(body.get("confidence", 0.5)),
            impact=body.get("impact", "medium"),
            auto_applicable=body.get("auto_applicable", False),
            created_at=body.get("created_at", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid pattern body: {e}")

    result = await engine.apply_pattern(pattern)
    # Bust the patterns cache so next /patterns call re-analyzes
    _patterns_cache["ts"] = 0.0
    return {
        "success": result.success,
        "description": result.description,
        "expected_impact": result.expected_impact,
        "change_id": result.change_id,
    }


@app.get("/learning/examples")
async def learning_examples() -> dict:
    from agentgate.learning_engine import LearningEngine
    from agentgate.client import GatewayClient
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    policy_path = os.getenv("AGENTGATE_POLICY_PATH", "./policies.yaml")
    gate = GatewayClient(policy_path=policy_path, db_path=db_path, fail_open=True)
    engine = LearningEngine(gateway=gate, db_path=db_path)
    examples = await engine.mine_examples(limit=10)
    return {"count": len(examples), "examples": examples}


@app.get("/learning/prompt-additions")
async def learning_prompt_additions() -> dict:
    # Additions are accumulated per-session in LearningEngine instances.
    # The server-side instance is stateless; agents should call apply_pattern directly.
    return {"count": 0, "additions": []}


@app.get("/learning/changes")
async def learning_changes(tool_name: Optional[str] = Query(default=None)) -> dict:
    """Return the policy change history with before/after metrics."""
    audit = _audit()
    changes = await audit.get_policy_changes(tool_name=tool_name, limit=50)
    return {"count": len(changes), "changes": changes}


@app.post("/learning/changes/{change_id}/measure")
async def measure_change_impact(change_id: str) -> dict:
    """
    Compute post-change metrics for a specific policy change and store them.
    Call this after sufficient traffic has flowed through the updated policy.
    """
    from agentgate.learning_engine import LearningEngine
    from agentgate.client import GatewayClient
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    policy_path = os.getenv("AGENTGATE_POLICY_PATH", "./policies.yaml")
    gate = GatewayClient(policy_path=policy_path, db_path=db_path, fail_open=True)
    engine = LearningEngine(gateway=gate, db_path=db_path)
    results = await engine.measure_impact(change_id=change_id)
    if not results:
        raise HTTPException(status_code=404, detail="Change not found or already measured")
    # Also bust patterns cache — drift detector may now fire
    _patterns_cache["ts"] = 0.0
    return results[0]


# ---------------------------------------------------------------------------
# Failure modes catalog
# ---------------------------------------------------------------------------


@app.get("/failure-modes")
async def list_failure_modes() -> dict:
    from agentgate.failure_modes import (
        get_all_categories,
        get_modes_by_category,
        get_summary,
    )
    return {
        "summary": get_summary(),
        "categories": [
            {**cat, "modes": get_modes_by_category(cat["id"])}
            for cat in get_all_categories()
        ],
    }


@app.get("/failure-modes/stats")
async def failure_mode_stats(
    since: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
) -> dict:
    from datetime import datetime, timedelta, timezone

    import aiosqlite

    from agentgate.failure_modes import DETECTOR_WIRING, get_built_modes

    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

    # 8 boundaries → 7 day buckets, ending at tomorrow_00:00 so today is the last bucket
    day_boundaries: list[str] = []
    for i in range(6, -1, -1):
        b = (now - timedelta(days=i)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%S")
        day_boundaries.append(b)
    day_boundaries.append(
        (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%S")
    )

    stats: dict = {}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Composite index for the audit_log scans below.
        try:
            await db.execute(
                """CREATE INDEX IF NOT EXISTS idx_fm_stats
                   ON audit_log(decided_at, agent_id, tool_name, outcome)"""
            )
            await db.commit()
        except Exception:
            pass

        for mode in get_built_modes():
            mode_id = mode["id"]
            wiring = DETECTOR_WIRING.get(mode_id)
            if not wiring:
                continue

            table = wiring["table"]
            where = wiring["where"]
            time_field = wiring.get("time_field", "decided_at")

            params_base: list = []
            agent_clause = ""
            if agent_id:
                agent_clause = " AND agent_id = ?"
                params_base.append(agent_id)

            since_clause = ""
            since_params: list = []
            if since:
                since_clause = f" AND {time_field} >= ?"
                since_params.append(since)

            try:
                # Total in window
                async with db.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}"
                    f"{agent_clause}{since_clause}",
                    params_base + since_params,
                ) as cur:
                    row = await cur.fetchone()
                total = row["cnt"] if row else 0

                # Today
                async with db.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}"
                    f"{agent_clause} AND {time_field} >= ?",
                    params_base + [today_start],
                ) as cur:
                    row = await cur.fetchone()
                today = row["cnt"] if row else 0

                # This week
                async with db.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}"
                    f"{agent_clause} AND {time_field} >= ?",
                    params_base + [week_start],
                ) as cur:
                    row = await cur.fetchone()
                this_week = row["cnt"] if row else 0

                # Last triggered + top tool
                async with db.execute(
                    f"SELECT {time_field} AS ts, tool_name FROM {table} "
                    f"WHERE {where}{agent_clause} "
                    f"ORDER BY {time_field} DESC LIMIT 1",
                    params_base,
                ) as cur:
                    row = await cur.fetchone()
                last_triggered = row["ts"] if row else None
                top_tool = row["tool_name"] if row else None

                # 7-day sparkline
                sparkline: list[int] = []
                for i in range(7):
                    day_s = day_boundaries[i]
                    day_e = day_boundaries[i + 1]
                    async with db.execute(
                        f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}"
                        f"{agent_clause} AND {time_field} >= ? AND {time_field} < ?",
                        params_base + [day_s, day_e],
                    ) as cur:
                        srow = await cur.fetchone()
                    sparkline.append(srow["cnt"] if srow else 0)

                # False-positive rate — only audit_log + total >= 10
                fp_rate = None
                if table == "audit_log" and total >= 10:
                    async with db.execute(
                        f"SELECT COUNT(*) AS cnt FROM audit_log WHERE {where}"
                        f"{agent_clause} AND outcome = 'escalation_approved'",
                        params_base,
                    ) as cur:
                        fp_row = await cur.fetchone()
                    fp_count = fp_row["cnt"] if fp_row else 0
                    fp_rate = round(fp_count / total, 3)

                stats[mode_id] = {
                    "total": total,
                    "today": today,
                    "this_week": this_week,
                    "last_triggered": last_triggered,
                    "top_tool": top_tool,
                    "false_positive_rate": fp_rate,
                    "sparkline": sparkline,
                }
            except Exception as e:
                logger.warning("failure_mode_stats query failed for %s: %s", mode_id, e)
                stats[mode_id] = {
                    "total": 0,
                    "today": 0,
                    "this_week": 0,
                    "last_triggered": None,
                    "top_tool": None,
                    "false_positive_rate": None,
                    "sparkline": [0, 0, 0, 0, 0, 0, 0],
                }

    return stats


# ---------------------------------------------------------------------------
# Demo runner  [DEMO ONLY — not for production use]
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
