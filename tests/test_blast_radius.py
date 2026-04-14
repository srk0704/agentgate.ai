"""
Tests for BlastRadiusEstimator — boundary conditions and heuristic rules.
Pure Python, no LLM, no async.
"""
import pytest
from agentgate.blast_radius import BlastRadiusEstimator
from agentgate.models import ToolCall


@pytest.fixture
def estimator():
    return BlastRadiusEstimator()


def _call(tool_name: str, **args) -> ToolCall:
    return ToolCall(tool_name=tool_name, args=args, agent_id="test")


# ── issue_refund boundary tests ───────────────────────────────────────────────


def test_issue_refund_low_severity(estimator):
    """amount=99 → below $100 threshold → low severity."""
    result = estimator.estimate(_call("issue_refund", amount=99))
    assert result["severity"] == "low"
    assert result["reversibility"] == "reversible"
    assert result["financial_impact"] == "$99.00"


def test_issue_refund_medium_boundary(estimator):
    """amount=100 → exactly at $100 boundary → medium severity."""
    result = estimator.estimate(_call("issue_refund", amount=100))
    assert result["severity"] == "medium"
    assert result["reversibility"] == "reversible"


def test_issue_refund_high_boundary(estimator):
    """amount=500 → exactly at $500 boundary → high severity."""
    result = estimator.estimate(_call("issue_refund", amount=500))
    assert result["severity"] == "high"
    assert result["reversibility"] == "reversible"


def test_issue_refund_above_high(estimator):
    """amount=750 → above $500 → still high severity."""
    result = estimator.estimate(_call("issue_refund", amount=750))
    assert result["severity"] == "high"


# ── process_payment boundary tests ───────────────────────────────────────────


def test_process_payment_medium(estimator):
    """amount=9999 → below $10,000 threshold → medium severity."""
    result = estimator.estimate(_call("process_payment", amount=9999))
    assert result["severity"] == "medium"
    assert result["reversibility"] == "reversible"


def test_process_payment_high_boundary(estimator):
    """amount=10000 → exactly at $10,000 boundary → high severity, partially_reversible."""
    result = estimator.estimate(_call("process_payment", amount=10000))
    assert result["severity"] == "high"
    assert result["reversibility"] == "partially_reversible"


def test_process_payment_critical_boundary(estimator):
    """amount=50000 → exactly at $50,000 boundary → critical severity."""
    result = estimator.estimate(_call("process_payment", amount=50000))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "partially_reversible"


def test_process_payment_above_critical(estimator):
    """amount=100000 → above $50,000 → still critical."""
    result = estimator.estimate(_call("process_payment", amount=100000))
    assert result["severity"] == "critical"


# ── wire_transfer ─────────────────────────────────────────────────────────────


def test_wire_transfer_always_critical(estimator):
    """Wire transfers are always critical and irreversible."""
    result = estimator.estimate(_call("wire_transfer", amount=25000))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "irreversible"
    assert "AML" in result["regulatory_flags"]
    assert "SOX" in result["regulatory_flags"]
    assert result["financial_impact"] == "$25,000.00"


def test_wire_transfer_no_amount(estimator):
    """Wire transfer without amount → still critical."""
    result = estimator.estimate(_call("wire_transfer"))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "irreversible"
    assert result["financial_impact"] == "unknown"


# ── bulk operations ───────────────────────────────────────────────────────────


def test_bulk_process_refunds_always_critical(estimator):
    """bulk_* tools are always critical."""
    result = estimator.estimate(_call("bulk_process_refunds"))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "irreversible"


def test_batch_transfer_always_critical(estimator):
    """batch_* tools are always critical."""
    result = estimator.estimate(_call("batch_transfer"))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "irreversible"


# ── regulatory flags ──────────────────────────────────────────────────────────


def test_view_full_card_number_pci_flag(estimator):
    """Card number view → PCI-DSS flag."""
    result = estimator.estimate(_call("view_full_card_number", customer_id="c001"))
    assert "PCI-DSS" in result["regulatory_flags"]
    assert result["severity"] == "high"


def test_export_customer_data_gdpr_flag(estimator):
    """Customer data export → GDPR flag."""
    result = estimator.estimate(_call("export_customer_data", user_id="all"))
    assert "GDPR" in result["regulatory_flags"]
    assert result["severity"] == "high"


def test_export_transaction_history_gdpr_and_sox(estimator):
    """Transaction history export → GDPR + SOX flags."""
    result = estimator.estimate(_call("export_transaction_history", user_id="all"))
    assert "GDPR" in result["regulatory_flags"]
    assert "SOX" in result["regulatory_flags"]


# ── destructive operations ────────────────────────────────────────────────────


def test_delete_tool_critical(estimator):
    """delete_* tools are always critical and irreversible."""
    result = estimator.estimate(_call("delete_account", user_id="123"))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "irreversible"


def test_close_account_critical(estimator):
    """close_account is critical (all account data), irreversible."""
    result = estimator.estimate(_call("close_account", user_id="123"))
    assert result["severity"] == "critical"
    assert result["reversibility"] == "irreversible"


# ── freeze account ────────────────────────────────────────────────────────────


def test_freeze_account_high_partially_reversible(estimator):
    """freeze_account → high severity, partially_reversible."""
    result = estimator.estimate(_call("freeze_account", account_id="acc_007"))
    assert result["severity"] == "high"
    assert result["reversibility"] == "partially_reversible"


# ── default / unknown tool ────────────────────────────────────────────────────


def test_unknown_tool_returns_default(estimator):
    """Unknown tool → default low severity, reversible."""
    result = estimator.estimate(_call("some_unknown_operation"))
    assert result["severity"] == "low"
    assert result["reversibility"] == "reversible"
    assert result["regulatory_flags"] == []


def test_never_raises_on_bad_args(estimator):
    """BlastRadiusEstimator never raises regardless of input."""
    # Args with non-numeric amount
    result = estimator.estimate(_call("issue_refund", amount="not_a_number"))
    assert isinstance(result, dict)
    assert "severity" in result
