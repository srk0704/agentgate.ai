"""Unit tests for the unified reliability score."""
from __future__ import annotations

from agentgate.models import Decision


def test_all_healthy_returns_100():
    score, summary = Decision.compute_reliability_score(None, None, None, None, None)
    assert score == 100
    assert summary == "Healthy"


def test_all_zero_scores_return_100():
    score, summary = Decision.compute_reliability_score(0, 0, 0, 0, 0)
    assert score == 100
    assert summary == "Healthy"


def test_critical_injection_score():
    score, summary = Decision.compute_reliability_score(
        risk_score=None, injection_score=92, anomaly_score=None,
    )
    assert score == 8
    assert "Critical" in summary
    assert "injection" in summary


def test_multiple_scores_worst_wins():
    # risk 72 is worse than drift 55 → reliability = 100 - 72 = 28
    score, summary = Decision.compute_reliability_score(
        risk_score=72, injection_score=None, anomaly_score=None,
        drift_score=55,
    )
    assert score == 28
    assert "Critical" in summary or "Degraded" in summary
    assert "risk" in summary


def test_caution_range():
    # anomaly 25 → reliability 75 → Caution band
    score, summary = Decision.compute_reliability_score(
        risk_score=None, injection_score=None, anomaly_score=25,
    )
    assert score == 75
    assert "Caution" in summary
    assert "anomaly" in summary


def test_summary_format_is_plain_english():
    _, summary = Decision.compute_reliability_score(
        risk_score=80, injection_score=None, anomaly_score=None,
    )
    # No code-style identifiers, no underscores, no JSON
    assert "_" not in summary
    assert "{" not in summary
    assert ":" in summary  # "Critical: ..." style


def test_degraded_band_boundary():
    # 60 → reliability 40 → Degraded
    score, summary = Decision.compute_reliability_score(
        risk_score=60, injection_score=None, anomaly_score=None,
    )
    assert score == 40
    assert "Degraded" in summary


def test_healthy_just_above_threshold():
    # 10 → reliability 90 → Healthy
    score, summary = Decision.compute_reliability_score(
        risk_score=10, injection_score=None, anomaly_score=None,
    )
    assert score == 90
    assert summary == "Healthy"


def test_loop_score_supported():
    score, summary = Decision.compute_reliability_score(
        risk_score=None, injection_score=None, anomaly_score=None,
        drift_score=None, loop_score=85,
    )
    assert score == 15
    assert "loop" in summary
