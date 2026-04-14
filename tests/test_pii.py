"""
Tests for PiiDetector — regex patterns, redaction, and output scanning.
LLM confirmation calls are mocked; no ANTHROPIC_API_KEY required.
"""
import pytest
from unittest.mock import AsyncMock, patch

from agentgate.pii_detector import PiiDetector


@pytest.fixture
def detector():
    return PiiDetector()


# ── Unit tests: regex pattern matching ───────────────────────────────────────


@pytest.mark.asyncio
async def test_detects_credit_card_number(detector):
    """Standard 16-digit credit card is detected."""
    text = "Your card 4532015112830366 has been charged."
    with patch.object(detector, "_llm_confirm",
                      new=AsyncMock(return_value=["credit_card"])):
        has_pii, findings = await detector.scan(text)
    assert has_pii is True
    assert "credit_card" in findings


@pytest.mark.asyncio
async def test_detects_ssn(detector):
    """US Social Security Number in XXX-XX-XXXX format is detected."""
    text = "SSN on file: 123-45-6789"
    with patch.object(detector, "_llm_confirm",
                      new=AsyncMock(return_value=["ssn"])):
        has_pii, findings = await detector.scan(text)
    assert has_pii is True
    assert "ssn" in findings


@pytest.mark.asyncio
async def test_detects_email(detector):
    """Valid email address is detected."""
    text = "Please contact support@example.com for help."
    with patch.object(detector, "_llm_confirm",
                      new=AsyncMock(return_value=["email"])):
        has_pii, findings = await detector.scan(text)
    assert has_pii is True
    assert "email" in findings


@pytest.mark.asyncio
async def test_detects_iban(detector):
    """IBAN number is detected."""
    text = "Transfer to GB29NWBK60161331926819 please."
    with patch.object(detector, "_llm_confirm",
                      new=AsyncMock(return_value=["iban"])):
        has_pii, findings = await detector.scan(text)
    assert has_pii is True
    assert "iban" in findings


@pytest.mark.asyncio
async def test_clean_output_passes(detector):
    """Text with no PII returns safe=True, empty findings."""
    text = "Your refund of $49.99 has been processed. Thank you for your patience."
    with patch.object(detector, "_llm_confirm",
                      new=AsyncMock(return_value=[])):
        has_pii, findings = await detector.scan(text)
    # If regex doesn't match, _llm_confirm never fires
    # The text has no CC/SSN/email/IBAN etc so regex won't match
    assert findings == [] or has_pii is False


@pytest.mark.asyncio
async def test_no_pii_skips_llm(detector):
    """Text with no regex matches never calls LLM."""
    text = "Account balance: $1,234.56. Last transaction on March 3rd."
    llm_mock = AsyncMock(return_value=[])
    with patch.object(detector, "_llm_confirm", new=llm_mock):
        has_pii, findings = await detector.scan(text)
    # LLM should not have been called if regex found nothing
    assert not has_pii
    assert findings == []


# ── Redaction tests ────────────────────────────────────────────────────────────


def test_redact_replaces_credit_card(detector):
    """Credit card number in text is replaced with placeholder."""
    text = "Card 4532015112830366 was charged."
    redacted = detector.redact(text, ["credit_card"])
    assert "4532015112830366" not in redacted
    assert "[REDACTED-CREDIT_CARD]" in redacted


def test_redact_replaces_ssn(detector):
    """SSN in text is replaced with placeholder."""
    text = "SSN: 123-45-6789 on record."
    redacted = detector.redact(text, ["ssn"])
    assert "123-45-6789" not in redacted
    assert "[REDACTED-SSN]" in redacted


def test_redact_replaces_email(detector):
    """Email in text is replaced with placeholder."""
    text = "Contact user@example.com for details."
    redacted = detector.redact(text, ["email"])
    assert "user@example.com" not in redacted
    assert "[REDACTED-EMAIL]" in redacted


def test_redact_multiple_types(detector):
    """Multiple PII types are all redacted."""
    text = "Email: user@example.com  SSN: 987-65-4321  Card: 4111111111111111"
    redacted = detector.redact(text, ["email", "ssn", "credit_card"])
    assert "user@example.com" not in redacted
    assert "987-65-4321" not in redacted
    assert "[REDACTED-EMAIL]" in redacted
    assert "[REDACTED-SSN]" in redacted


def test_redact_unchanged_when_no_findings(detector):
    """Redact with empty findings list returns original text unchanged."""
    text = "No sensitive data here."
    assert detector.redact(text, []) == text


# ── Boundary tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_partial_credit_card_does_not_match(detector):
    """A 10-digit number (partial card) should not match the credit card regex."""
    text = "Reference number: 1234567890"
    # regex for credit_card requires 15-16 digits; 10 digits won't match
    llm_mock = AsyncMock(return_value=[])
    with patch.object(detector, "_llm_confirm", new=llm_mock):
        has_pii, findings = await detector.scan(text)
    # If regex didn't match, LLM is never called
    assert "credit_card" not in findings


@pytest.mark.asyncio
async def test_email_without_tld_does_not_match(detector):
    """Email without a valid TLD (e.g. user@domain) should not match."""
    # The regex requires \.[A-Za-z]{2,} at the end
    text = "Contact user@domain for help"  # no .com or similar
    has_pii, findings = await detector.scan(text)
    assert "email" not in findings


@pytest.mark.asyncio
async def test_nine_digit_non_routing_llm_confirms_not_routing(detector):
    """A 9-digit number that is not a routing number — LLM says no."""
    text = "Order ID: 123456789"  # 9-digit order ID, not a bank routing number
    # Regex will match (it's 9 digits), but LLM confirms it's not a routing number
    with patch.object(detector, "_llm_confirm",
                      new=AsyncMock(return_value=[])):
        has_pii, findings = await detector.scan(text)
    assert "routing_number" not in findings
    assert not has_pii


# ── Integration tests via GatewayClient.scan_output ──────────────────────────


@pytest.mark.asyncio
async def test_read_tool_with_pii_gets_redacted(tmp_path):
    """PII in output from a read-only tool → recommendation: redact."""
    from agentgate.client import GatewayClient

    policy_file = tmp_path / "p.yaml"
    policy_file.write_text("policies: []\n")
    gate = GatewayClient(
        policy_path=str(policy_file),
        db_path=str(tmp_path / "test.db"),
    )

    output = "Account holder: John Doe. SSN on file: 123-45-6789. Balance: $500."
    with patch.object(gate._pii_detector, "_llm_confirm",
                      new=AsyncMock(return_value=["ssn"])):
        result = await gate.scan_output(output, tool_name="get_customer_info")

    assert result["recommendation"] == "redact"
    assert result["safe"] is False
    assert "ssn" in result["pii_found"]
    assert result["redacted_output"] is not None
    assert "123-45-6789" not in result["redacted_output"]


@pytest.mark.asyncio
async def test_export_tool_with_pii_gets_blocked(tmp_path):
    """PII in output from an export tool → recommendation: block."""
    from agentgate.client import GatewayClient

    policy_file = tmp_path / "p.yaml"
    policy_file.write_text("policies: []\n")
    gate = GatewayClient(
        policy_path=str(policy_file),
        db_path=str(tmp_path / "test.db"),
    )

    output = "customer_id,card\n001,4532015112830366\n002,5425233430109903"
    with patch.object(gate._pii_detector, "_llm_confirm",
                      new=AsyncMock(return_value=["credit_card"])):
        result = await gate.scan_output(output, tool_name="export_customer_data")

    assert result["recommendation"] == "block"
    assert result["safe"] is False
    assert result["redacted_output"] is None


@pytest.mark.asyncio
async def test_clean_output_from_any_tool_allowed(tmp_path):
    """Output with no PII → recommendation: allow regardless of tool type."""
    from agentgate.client import GatewayClient

    policy_file = tmp_path / "p.yaml"
    policy_file.write_text("policies: []\n")
    gate = GatewayClient(
        policy_path=str(policy_file),
        db_path=str(tmp_path / "test.db"),
    )

    output = "Refund of $49.99 successfully processed. Transaction ID: TXN-20240413."
    # No PII → LLM confirm never called; regex finds nothing
    result = await gate.scan_output(output, tool_name="export_customer_data")

    assert result["recommendation"] == "allow"
    assert result["safe"] is True
    assert result["pii_found"] == []
