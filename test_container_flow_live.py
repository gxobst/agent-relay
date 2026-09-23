"""Live API integration test for SPEC scenario 1 on the container relay.

Hits the real containerized API over HTTP (default http://127.0.0.1:18480,
overridable via RELAY_DOCKER_BASE_URL) and only creates data — never drops
tables, so it is safe against the live container DB.

Scenario 1: register two agents, one sends a task, the other claims and
completes it, the sender reads the result.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("RELAY_DOCKER_BASE_URL", "http://127.0.0.1:18480")


def _client() -> httpx.Client:
    try:
        with httpx.Client(base_url=BASE_URL, timeout=10.0) as probe:
            probe.get("/health").raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"container relay not reachable at {BASE_URL}")
    return httpx.Client(base_url=BASE_URL, timeout=10.0)


def test_container_scenario1_exchange_task_and_result():
    suffix = uuid.uuid4().hex[:8]
    with _client() as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}

        sender = client.post("/api/v1/agents", json={"name": f"alice-{suffix}"})
        assert sender.status_code == 201, sender.text
        sender_data = sender.json()
        recipient = client.post("/api/v1/agents", json={"name": f"bob-{suffix}"})
        assert recipient.status_code == 201, recipient.text
        recipient_data = recipient.json()

        sender_headers = {"Authorization": f"Bearer {sender_data['token']}"}
        recipient_headers = {"Authorization": f"Bearer {recipient_data['token']}"}

        sent = client.post(
            "/api/v1/tasks",
            headers=sender_headers,
            json={"to": recipient_data["agent_id"], "input": f"review add() {suffix}"},
        )
        assert sent.status_code == 201, sent.text
        task_id = sent.json()["task_id"]
        assert sent.json()["status"] == "queued"

        claim = client.post(
            "/api/v1/tasks/claim",
            headers=recipient_headers,
            json={"worker_id": f"bob-docker-{suffix}", "wait_seconds": 5},
        )
        assert claim.status_code == 200, claim.text
        claim_data = claim.json()
        assert claim_data["task_id"] == task_id
        assert claim_data["from"] == sender_data["agent_id"]
        assert claim_data["attempt"] == 1

        complete = client.post(
            f"/api/v1/tasks/{task_id}/complete",
            headers=recipient_headers,
            json={"claim_token": claim_data["claim_token"], "output": f"looks good {suffix}"},
        )
        assert complete.status_code == 200, complete.text

        got = client.get(f"/api/v1/tasks/{task_id}", headers=sender_headers)
        assert got.status_code == 200
        body = got.json()
        assert body["status"] == "completed"
        assert body["output"] == f"looks good {suffix}"
        assert body["attempt_count"] == 1
