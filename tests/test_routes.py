from fastapi import FastAPI
from fastapi.testclient import TestClient

from venom_module_brand_studio.api.routes import router


def build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_health_route() -> None:
    client = build_client()
    response = client.get("/api/v1/brand-studio/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "module": "brand_studio"}


def test_list_candidates_route() -> None:
    client = build_client()
    response = client.get("/api/v1/brand-studio/sources/candidates", params={"limit": 2})
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["count"] == 2
    assert len(payload["items"]) == 2


def test_generate_queue_publish_and_audit_flow() -> None:
    client = build_client()

    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]

    draft_response = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={
            "candidate_id": candidate_id,
            "channels": ["x", "github"],
            "languages": ["pl", "en"],
            "tone": "expert",
        },
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert draft_response.status_code == 200
    draft_payload = draft_response.json()
    assert draft_payload["candidate_id"] == candidate_id
    assert len(draft_payload["variants"]) == 4

    queue_response = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "x"},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    assert queue_payload["status"] == "queued"

    publish_response = client.post(
        f"/api/v1/brand-studio/queue/{queue_payload['item_id']}/publish",
        json={"confirm_publish": True},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert publish_response.status_code == 200
    publish_payload = publish_response.json()
    assert publish_payload["success"] is True
    assert publish_payload["status"] == "published"

    queue_list = client.get("/api/v1/brand-studio/queue")
    assert queue_list.status_code == 200
    assert queue_list.json()["count"] >= 1

    audit_list = client.get("/api/v1/brand-studio/audit")
    assert audit_list.status_code == 200
    assert audit_list.json()["count"] >= 3


def test_publish_requires_confirm_publish_true() -> None:
    client = build_client()
    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]
    draft_payload = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={
            "candidate_id": candidate_id,
            "channels": ["x"],
            "languages": ["pl"],
        },
        headers={"X-Authenticated-User": "mpieniak"},
    ).json()
    queue_payload = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "x"},
        headers={"X-Authenticated-User": "mpieniak"},
    ).json()

    response = client.post(
        f"/api/v1/brand-studio/queue/{queue_payload['item_id']}/publish",
        json={"confirm_publish": False},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "confirm_publish must be true"


def test_404_errors_for_missing_resources() -> None:
    client = build_client()

    draft_response = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": "missing", "channels": ["x"], "languages": ["pl"]},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert draft_response.status_code == 404

    queue_response = client.post(
        "/api/v1/brand-studio/drafts/draft-missing/queue",
        json={"target_channel": "x"},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert queue_response.status_code == 404

    publish_response = client.post(
        "/api/v1/brand-studio/queue/queue-missing/publish",
        json={"confirm_publish": True},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert publish_response.status_code == 404


def test_publish_conflict_when_item_already_published() -> None:
    client = build_client()

    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]
    draft_payload = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": candidate_id, "channels": ["x"], "languages": ["pl"]},
        headers={"X-Authenticated-User": "mpieniak"},
    ).json()
    queue_payload = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "x"},
        headers={"X-Authenticated-User": "mpieniak"},
    ).json()
    item_id = queue_payload["item_id"]

    first_publish = client.post(
        f"/api/v1/brand-studio/queue/{item_id}/publish",
        json={"confirm_publish": True},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert first_publish.status_code == 200

    second_publish = client.post(
        f"/api/v1/brand-studio/queue/{item_id}/publish",
        json={"confirm_publish": True},
        headers={"X-Authenticated-User": "mpieniak"},
    )
    assert second_publish.status_code == 409
    assert second_publish.json()["detail"] == "Queue item already published"


def test_401_for_mutating_endpoint_without_actor_header() -> None:
    client = build_client()
    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]

    response = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": candidate_id, "channels": ["x"], "languages": ["pl"]},
    )
    assert response.status_code == 401


def test_403_when_feature_disabled(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_BRAND_STUDIO", "false")
    client = build_client()
    response = client.get("/api/v1/brand-studio/sources/candidates")
    assert response.status_code == 403


def test_403_when_actor_not_in_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("BRAND_STUDIO_ALLOWED_USERS", "allowed-user")
    client = build_client()
    response = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": "cand-1", "channels": ["x"], "languages": ["pl"]},
        headers={"X-Authenticated-User": "blocked-user"},
    )
    assert response.status_code == 403
