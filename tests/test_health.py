"""Smoke tests for the Phase 0 FastAPI app."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_opt_out_accepts_valid_email() -> None:
    response = client.post(
        "/privacy/opt-out",
        json={"email": "contact@example.com", "reason": "GDPR erasure"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_opt_out_rejects_invalid_email() -> None:
    response = client.post(
        "/privacy/opt-out",
        json={"email": "not-an-email"},
    )
    assert response.status_code == 422
