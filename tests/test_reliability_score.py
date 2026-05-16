"""Unit tests for the unified reliability score."""
from __future__ import annotations

import json

from agentgate.models import Decision


def _parse(summary: str) -> dict:
    """Every reliability_summary is a JSON-encoded 4-dimension dict."""
    data = json.loads(summary)
    assert {"overall", "safety", "consistency", "caution"} <= data.keys()
    for dim in data.values():
        assert "score" in dim and "label" in dim
        assert 0 <= dim["score"] <= 100
        assert isinstance(dim["label"], str) and dim["label"]
    return data


def test_all_healthy_returns_100():
    score, summary = Decision.compute_reliability_score(None, None, None, None, None)
    assert score == 100
    data = _parse(summary)
    assert data["overall"]["score"] == 100
    assert data["overall"]["label"] == "Agent is operating reliably"


def test_all_zero_scores_return_100():
    score, summary = Decision.compute_reliability_score(0, 0, 0, 0, 0)
    assert score == 100
    data = _parse(summary)
    assert data["overall"]["score"] == 100


def test_critical_injection_score():
    score, summary = Decision.compute_reliability_score(
        risk_score=None, injection_score=92, anomaly_score=None,
    )
    assert score == 8
    data = _parse(summary)
    assert "Critical" in data["overall"]["label"]
    assert "injection" in data["overall"]["label"]
    # Injection drives the safety dimension into the critical band.
    assert data["safety"]["score"] == 8
    assert "injection" in data["safety"]["label"]


def test_multiple_scores_worst_wins():
    # risk 72 is worse than drift 55 → reliability = 100 - 72 = 28
    score, summary = Decision.compute_reliability_score(
        risk_score=72, injection_score=None, anomaly_score=None,
        drift_score=55,
    )
    assert score == 28
    data = _parse(summary)
    label = data["overall"]["label"]
    assert "Critical" in label or "Degraded" in label
    assert "risk" in label
    # Risk drives the caution dimension; drift drives safety.
    assert data["caution"]["score"] == 28
    assert data["safety"]["score"] == 45  # 100 - 55


def test_caution_range():
    # anomaly 25 → reliability 75 → Caution band
    score, summary = Decision.compute_reliability_score(
        risk_score=None, injection_score=None, anomaly_score=25,
    )
    assert score == 75
    data = _parse(summary)
    assert "Caution" in data["overall"]["label"]
    assert "anomaly" in data["overall"]["label"]
    # Anomaly drives the consistency dimension.
    assert data["consistency"]["score"] == 75
    assert "anomaly" in data["consistency"]["label"]


def test_summary_is_valid_json_with_four_dimensions():
    _, summary = Decision.compute_reliability_score(
        risk_score=80, injection_score=None, anomaly_score=None,
    )
    data = _parse(summary)
    # Plain-English labels — no code-style identifiers in the overall label.
    assert "_" not in data["overall"]["label"]


def test_degraded_band_boundary():
    # 60 → reliability 40 → Degraded
    score, summary = Decision.compute_reliability_score(
        risk_score=60, injection_score=None, anomaly_score=None,
    )
    assert score == 40
    data = _parse(summary)
    assert "Degraded" in data["overall"]["label"]


def test_healthy_just_above_threshold():
    # 10 → reliability 90 → Healthy
    score, summary = Decision.compute_reliability_score(
        risk_score=10, injection_score=None, anomaly_score=None,
    )
    assert score == 90
    data = _parse(summary)
    assert data["overall"]["label"] == "Agent is operating reliably"


def test_loop_score_supported():
    score, summary = Decision.compute_reliability_score(
        risk_score=None, injection_score=None, anomaly_score=None,
        drift_score=None, loop_score=85,
    )
    assert score == 15
    data = _parse(summary)
    assert "loop" in data["overall"]["label"]
    # Loop drives the consistency dimension into critical.
    assert data["consistency"]["score"] == 15
    assert "loop" in data["consistency"]["label"]
