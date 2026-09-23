"""Live API integration test for the dashboard flow.

Unlike ``test_agent_relay.py`` (TestClient + scratch DB that gets dropped),
this hits the real running relay over HTTP and only creates data — it never
drops tables, so it is safe against the live DB.

Flow (mirrors dashboard.html + SPEC scenario 1):
register sender + viewer -> sender sends task -> viewer sees it via the same
endpoints the dashboard uses -> viewer claims/completes -> sender reads result.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("RELAY_LIVE_BASE_URL", "http://127.0.0.1:18473")


def _client() -> httpx.Client:
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as probe:
            probe.get("/health").raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"live relay not reachable at {BASE_URL}")
    return httpx.Client(base_url=BASE_URL, timeout=10.0)


def _register(client: httpx.Client, name: str) -> tuple[dict, dict[str, str]]:
    response = client.post("/api/v1/agents", json={"name": name})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["agent_id"] and data["token"]
    return data, {"Authorization": f"Bearer {data['token']}"}


def test_dashboard_flow_send_claim_complete_and_read():
    suffix = uuid.uuid4().hex[:8]
    with _client() as client:
        # Liveness + dashboard asset (what the browser loads).
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}
        page = client.get("/")
        assert page.status_code == 200
        assert "sessionStorage" in page.text

        # Dashboard with no token -> 401, which is why tables stay empty.
        assert client.get("/api/v1/agents").status_code == 401

        sender, sender_headers = _register(client, f"live-sender-{suffix}")
        viewer, viewer_headers = _register(client, f"live-viewer-{suffix}")

        sent = client.post(
            "/api/v1/tasks",
            headers=sender_headers,
            json={"to": viewer["agent_id"], "input": f"dashboard test task {suffix}"},
        )
        assert sent.status_code == 201, sent.text
        task_id = sent.json()["task_id"]

        # Same endpoints dashboard.html uses.
        agents = client.get("/api/v1/agents?limit=100", headers=viewer_headers)
        assert agents.status_code == 200
        ids = {a["agent_id"] for a in agents.json()["items"]}
        assert {sender["agent_id"], viewer["agent_id"]} <= ids
        assert all("token" not in a for a in agents.json()["items"])

        received = client.get(
            "/api/v1/tasks?direction=received&limit=100", headers=viewer_headers
        )
        assert received.status_code == 200
        assert any(t["task_id"] == task_id for t in received.json()["items"])

        claim = client.post(
            "/api/v1/tasks/claim",
            headers=viewer_headers,
            json={"worker_id": f"live-worker-{suffix}", "wait_seconds": 5},
        )
        assert claim.status_code == 200, claim.text
        claim_data = claim.json()
        assert claim_data["task_id"] == task_id
        assert claim_data["from"] == sender["agent_id"]

        complete = client.post(
            f"/api/v1/tasks/{task_id}/complete",
            headers=viewer_headers,
            json={"claim_token": claim_data["claim_token"], "output": f"done {suffix}"},
        )
        assert complete.status_code == 200, complete.text

        # Sender reads the result.
        got = client.get(f"/api/v1/tasks/{task_id}", headers=sender_headers)
        assert got.status_code == 200
        body = got.json()
        assert body["status"] == "completed"
        assert body["output"] == f"done {suffix}"
        assert body["attempt_count"] == 1

        attempts = client.get(f"/api/v1/tasks/{task_id}/attempts", headers=sender_headers)
        assert attempts.status_code == 200
        assert attempts.json()["items"][0]["outcome"] == "completed"
        assert "claim_token" not in attempts.json()["items"][0]

        # Unrelated agent sees nothing (SPEC scenario 9).
        stranger, stranger_headers = _register(client, f"live-stranger-{suffix}")
        assert stranger["agent_id"] not in {sender["agent_id"], viewer["agent_id"]}
        assert client.get(f"/api/v1/tasks/{task_id}", headers=stranger_headers).status_code == 404
