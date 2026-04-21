"""
AgentGate — Blast Radius Estimator
====================================
Estimates the potential impact of a tool call before it executes.
Pure Python, synchronous, no LLM, never raises.
"""
from __future__ import annotations

import os

from agentgate.models import ToolCall


class BlastRadiusEstimator:
    """
    Returns a structured estimate of a tool call's blast radius:
    financial impact, reversibility, regulatory exposure, and severity.

    Rules are heuristic — based on tool name and numeric args.
    Financial thresholds are configurable via environment variables.
    Always returns a dict; never raises.
    """

    def __init__(self) -> None:
        # Configurable per-company risk thresholds
        self._payment_critical = float(os.getenv("AGENTGATE_BLAST_PAYMENT_CRITICAL", "50000"))
        self._payment_high = float(os.getenv("AGENTGATE_BLAST_PAYMENT_HIGH", "10000"))
        self._refund_high = float(os.getenv("AGENTGATE_BLAST_REFUND_HIGH", "500"))
        self._refund_medium = float(os.getenv("AGENTGATE_BLAST_REFUND_MEDIUM", "100"))
        self._credit_high = float(os.getenv("AGENTGATE_BLAST_CREDIT_HIGH", "5000"))

    def estimate(self, tool_call: ToolCall) -> dict:
        try:
            return self._estimate(tool_call.tool_name, tool_call.args or {})
        except Exception:
            return self._default()

    # ── Heuristic dispatch ──────────────────────────────────────────────────

    def _estimate(self, name: str, args: dict) -> dict:
        amount = args.get("amount")

        # ── Wire transfers ──────────────────────────────────────────────────
        if name == "wire_transfer":
            return {
                "financial_impact": f"${amount:,.2f}" if amount is not None else "unknown",
                "records_affected": "unknown",
                "reversibility": "irreversible",
                "regulatory_flags": ["AML", "SOX"],
                "severity": "critical",
                "estimated_affected_users": None,
            }

        # ── Payment processing ──────────────────────────────────────────────
        if name == "process_payment":
            if amount is not None:
                fin = f"${amount:,.2f}"
                if amount >= self._payment_critical:
                    return {
                        "financial_impact": fin,
                        "records_affected": "unknown",
                        "reversibility": "partially_reversible",
                        "regulatory_flags": ["SOX"],
                        "severity": "critical",
                        "estimated_affected_users": None,
                    }
                elif amount >= self._payment_high:
                    return {
                        "financial_impact": fin,
                        "records_affected": "unknown",
                        "reversibility": "partially_reversible",
                        "regulatory_flags": [],
                        "severity": "high",
                        "estimated_affected_users": None,
                    }
                else:
                    return {
                        "financial_impact": fin,
                        "records_affected": "unknown",
                        "reversibility": "reversible",
                        "regulatory_flags": [],
                        "severity": "medium",
                        "estimated_affected_users": None,
                    }
            return {
                "financial_impact": "unknown",
                "records_affected": "unknown",
                "reversibility": "partially_reversible",
                "regulatory_flags": [],
                "severity": "high",
                "estimated_affected_users": None,
            }

        # ── Refunds ─────────────────────────────────────────────────────────
        if name == "issue_refund":
            if amount is not None:
                if amount >= self._refund_high:
                    sev = "high"
                elif amount >= self._refund_medium:
                    sev = "medium"
                else:
                    sev = "low"
                return {
                    "financial_impact": f"${amount:,.2f}",
                    "records_affected": "1 transaction",
                    "reversibility": "reversible",
                    "regulatory_flags": [],
                    "severity": sev,
                    "estimated_affected_users": 1,
                }
            return {
                "financial_impact": "unknown",
                "records_affected": "1 transaction",
                "reversibility": "reversible",
                "regulatory_flags": [],
                "severity": "medium",
                "estimated_affected_users": 1,
            }

        # ── Account operations ───────────────────────────────────────────────
        if name == "close_account":
            return {
                "financial_impact": "unknown",
                "records_affected": "all account data",
                "reversibility": "irreversible",
                "regulatory_flags": ["GDPR"],
                "severity": "critical",
                "estimated_affected_users": 1,
            }

        if name == "freeze_account":
            return {
                "financial_impact": "$0",
                "records_affected": "1 account",
                "reversibility": "partially_reversible",
                "regulatory_flags": [],
                "severity": "high",
                "estimated_affected_users": 1,
            }

        # ── Card / payment data ──────────────────────────────────────────────
        if name == "view_full_card_number":
            return {
                "financial_impact": "unknown",
                "records_affected": "1 card record",
                "reversibility": "irreversible",
                "regulatory_flags": ["PCI-DSS"],
                "severity": "high",
                "estimated_affected_users": 1,
            }

        # ── Data exports ─────────────────────────────────────────────────────
        if name == "export_transaction_history":
            return {
                "financial_impact": "unknown",
                "records_affected": "all transactions",
                "reversibility": "irreversible",
                "regulatory_flags": ["GDPR", "SOX"],
                "severity": "high",
                "estimated_affected_users": None,
            }

        if name in ("export_customer_data", "export_all_data"):
            return {
                "financial_impact": "unknown",
                "records_affected": "all customer records",
                "reversibility": "irreversible",
                "regulatory_flags": ["GDPR"],
                "severity": "high",
                "estimated_affected_users": None,
            }

        # ── Bulk / batch operations ──────────────────────────────────────────
        if name.startswith("bulk_") or name.startswith("batch_"):
            ids = args.get("ids") or args.get("user_ids") or []
            count = args.get("count") or (len(ids) if ids else None)
            fin = (
                f"${amount * count:,.2f}" if amount and count
                else (f"${amount:,.2f}" if amount else "unknown")
            )
            return {
                "financial_impact": fin,
                "records_affected": f"{count} records" if count else "multiple records",
                "reversibility": "irreversible",
                "regulatory_flags": [],
                "severity": "critical",
                "estimated_affected_users": count,
            }

        # ── Destructive operations ───────────────────────────────────────────
        if name.startswith("delete_") or name.startswith("drop_") or name.startswith("destroy_"):
            return {
                "financial_impact": "unknown",
                "records_affected": "unknown",
                "reversibility": "irreversible",
                "regulatory_flags": [],
                "severity": "critical",
                "estimated_affected_users": None,
            }

        # ── Credit limit changes ─────────────────────────────────────────────
        if name == "update_credit_limit":
            increase = args.get("increase")
            decrease = args.get("decrease")
            if increase is not None:
                sev = "high" if increase >= self._credit_high else "medium"
                return {
                    "financial_impact": f"+${increase:,.2f} credit",
                    "records_affected": "1 account",
                    "reversibility": "reversible",
                    "regulatory_flags": [],
                    "severity": sev,
                    "estimated_affected_users": 1,
                }
            if decrease is not None:
                return {
                    "financial_impact": f"-${decrease:,.2f} credit",
                    "records_affected": "1 account",
                    "reversibility": "reversible",
                    "regulatory_flags": [],
                    "severity": "low",
                    "estimated_affected_users": 1,
                }

        # ── AML / compliance checks ──────────────────────────────────────────
        if name in ("run_aml_check", "aml_check"):
            return {
                "financial_impact": "$0",
                "records_affected": "1 record",
                "reversibility": "reversible",
                "regulatory_flags": ["AML"],
                "severity": "low",
                "estimated_affected_users": 1,
            }

        return self._default()

    def _default(self) -> dict:
        return {
            "financial_impact": "unknown",
            "records_affected": "unknown",
            "reversibility": "reversible",
            "regulatory_flags": [],
            "severity": "low",
            "estimated_affected_users": None,
        }
