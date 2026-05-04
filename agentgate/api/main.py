from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
# override=False so explicit env vars (e.g. set on the uvicorn command line or
# by pytest's monkeypatch.setenv) win over .env defaults.
load_dotenv(override=False)

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from agentgate.audit import AuditLogger
from agentgate.escalation import EscalationQueue

logger = logging.getLogger(__name__)

app = FastAPI(title="AgentGate API", version="0.1.0")

DASHBOARD_HTML = Path(__file__).parent.parent / "dashboard" / "index.html"

# Paths that never require an API key
_AUTH_SKIP = frozenset({"/", "/health"})


class _ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header on all endpoints except the skip list."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        api_key = os.getenv("AGENTGATE_API_KEY")
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


def _audit() -> AuditLogger:
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    return AuditLogger(db_path)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """Serve the single-page dashboard."""
    if not DASHBOARD_HTML.exists():
        raise HTTPException(status_code=404, detail="Dashboard not built")
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


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

    return {
        **stats,
        "recent_decisions": recent,
        "pending_escalations": pending_only,
        "flagged_sessions": flagged_sessions,
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


@app.post("/escalations/{escalation_id}/approve")
async def approve_escalation(escalation_id: str, body: ApprovalRequest) -> dict:
    escalation = await EscalationQueue.get_by_id(escalation_id)
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    if escalation["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Escalation is already {escalation['status']}")
    await EscalationQueue.approve(escalation_id)
    audit = _audit()
    await audit.update_escalation_outcome(
        escalation_id, "escalation_approved", "approved", body.reason
    )
    return {"escalation_id": escalation_id, "status": "approved", "reason": body.reason}


@app.post("/escalations/{escalation_id}/reject")
async def reject_escalation(escalation_id: str, body: ApprovalRequest) -> dict:
    escalation = await EscalationQueue.get_by_id(escalation_id)
    if not escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")
    if escalation["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Escalation is already {escalation['status']}")
    await EscalationQueue.reject(escalation_id)
    audit = _audit()
    await audit.update_escalation_outcome(
        escalation_id, "escalation_rejected", "rejected", body.reason
    )
    return {"escalation_id": escalation_id, "status": "rejected", "reason": body.reason}


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
    from datetime import datetime
    db_path = os.getenv("AGENTGATE_DB_PATH", "./agentgate.db")
    rows: list[dict] = []
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT agent_id, session_id,
                          MAX(loop_score) AS loop_score,
                          MAX(loop_reason) AS reason,
                          MIN(decided_at) AS first_detected
                   FROM audit_log
                   WHERE loop_score > 50
                     AND decided_at > datetime('now', '-1 hour')
                   GROUP BY agent_id, session_id
                   ORDER BY loop_score DESC""",
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
    except Exception as e:
        logger.debug("health/agent-loops query failed: %s", e)
    return {
        "agents_in_loops": rows,
        "total": len(rows),
        "checked_at": datetime.utcnow().isoformat() + "Z",
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
# Demo runner  [DEMO ONLY — not for production use]
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
