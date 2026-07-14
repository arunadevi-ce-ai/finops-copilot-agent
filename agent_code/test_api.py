"""
Tests api.py. /health and /anomalies need no Redis (FastAPI's
TestClient, real detector.py underneath). /propose and /confirm are
skipped unless REDIS_URL is set in the test environment — consistent
with "routine pytest doesn't need live infrastructure," same spirit as
test_evals.py's EVAL_TRACK_HISTORY gating.
"""

import os

import pytest
from fastapi.testclient import TestClient

import api

client = TestClient(api.app)

requires_redis = pytest.mark.skipif(
    not os.environ.get("REDIS_URL"),
    reason="REDIS_URL not set — /propose and /confirm need real Redis",
)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_anomalies_returns_real_findings():
    response = client.get("/anomalies")
    assert response.status_code == 200

    anomalies = response.json()["anomalies"]
    resource_names = {a["resource_name"] for a in anomalies}
    assert len(anomalies) == 7
    assert "pd-payments-dr-volume-01" in resource_names


def test_anomalies_filters_by_team():
    response = client.get("/anomalies", params={"team": "payments"})
    anomalies = response.json()["anomalies"]

    assert len(anomalies) == 1
    assert anomalies[0]["resource_name"] == "pd-payments-dr-volume-01"


def test_metrics_endpoint_is_mounted():
    response = client.get("/metrics")
    assert response.status_code == 200
    # confirms metrics.py's counters are actually registered on this
    # ASGI-mounted endpoint, not just importable
    assert b"finops_anomalies_detected_total" in response.content


@requires_redis
def test_propose_then_confirm_dr_volume_trap():
    propose_response = client.post("/propose", json={"resource_name": "pd-payments-dr-volume-01"})
    assert propose_response.status_code == 200

    body = propose_response.json()
    print(f"\n/propose -> {body['recommendation']}")
    assert body["recommendation"]["requires_manual_review"] is True
    token = body["approval_token"]
    assert token

    confirm_response = client.post("/confirm", json={
        "token": token, "approved": True, "approver": "test-user",
    })
    assert confirm_response.status_code == 200

    confirmed = confirm_response.json()
    print(f"/confirm -> {confirmed['human_decision']}")
    assert confirmed["human_decision"] == {"approved": True, "approver": "test-user"}
    assert confirmed["status"] == "resumed"


@requires_redis
def test_confirm_rejects_reused_token():
    propose_response = client.post("/propose", json={"resource_name": "vm-ml-training-gpu-01"})
    token = propose_response.json()["approval_token"]

    first = client.post("/confirm", json={"token": token, "approved": True, "approver": "test-user"})
    assert first.status_code == 200

    second = client.post("/confirm", json={"token": token, "approved": True, "approver": "test-user"})
    assert second.status_code == 410


def test_propose_unknown_resource_returns_404():
    response = client.post("/propose", json={"resource_name": "does-not-exist"})
    assert response.status_code == 404


@requires_redis
def test_confirm_unknown_token_returns_410():
    response = client.post("/confirm", json={"token": "not-a-real-token", "approved": True, "approver": "x"})
    assert response.status_code == 410
