from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from venom_module_brand_studio.api.routes import router
from venom_module_brand_studio.services import service as service_module

AUTH_HEADERS = {"X-Authenticated-User": "mpieniak", "X-Autonomy-Level": "20"}


@pytest.fixture(autouse=True)
def isolated_runtime_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FEATURE_BRAND_STUDIO", "true")
    monkeypatch.setenv("BRAND_STUDIO_DISCOVERY_MODE", "stub")
    monkeypatch.setenv("BRAND_STUDIO_STATE_FILE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("BRAND_STUDIO_CACHE_FILE", str(tmp_path / "candidates-cache.json"))
    service_module._service = service_module.BrandStudioService()


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
    response = client.get(
        "/api/v1/brand-studio/sources/candidates",
        params={"limit": 2, "min_score": 0.0},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["count"] == len(payload["items"])
    assert payload["count"] <= 2
    assert payload["count"] >= 1


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
        headers=AUTH_HEADERS,
    )
    assert draft_response.status_code == 200
    draft_payload = draft_response.json()
    assert draft_payload["candidate_id"] == candidate_id
    assert len(draft_payload["variants"]) == 4

    queue_response = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "x", "account_id": "default-x"},
        headers=AUTH_HEADERS,
    )
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    assert queue_payload["status"] == "queued"

    publish_response = client.post(
        f"/api/v1/brand-studio/queue/{queue_payload['item_id']}/publish",
        json={"confirm_publish": True},
        headers=AUTH_HEADERS,
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
        headers=AUTH_HEADERS,
    ).json()
    queue_payload = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "x"},
        headers=AUTH_HEADERS,
    ).json()

    response = client.post(
        f"/api/v1/brand-studio/queue/{queue_payload['item_id']}/publish",
        json={"confirm_publish": False},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "confirm_publish must be true"


def test_404_errors_for_missing_resources() -> None:
    client = build_client()

    draft_response = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": "missing", "channels": ["x"], "languages": ["pl"]},
        headers=AUTH_HEADERS,
    )
    assert draft_response.status_code == 404

    queue_response = client.post(
        "/api/v1/brand-studio/drafts/draft-missing/queue",
        json={"target_channel": "x"},
        headers=AUTH_HEADERS,
    )
    assert queue_response.status_code == 404

    publish_response = client.post(
        "/api/v1/brand-studio/queue/queue-missing/publish",
        json={"confirm_publish": True},
        headers=AUTH_HEADERS,
    )
    assert publish_response.status_code == 404


def test_queue_draft_returns_404_for_unknown_account_id() -> None:
    client = build_client()
    candidate_id = client.get("/api/v1/brand-studio/sources/candidates").json()["items"][0]["id"]

    draft_payload = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": candidate_id, "channels": ["devto"], "languages": ["pl"]},
        headers=AUTH_HEADERS,
    ).json()

    response = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "devto", "account_id": "missing-account"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"


def test_publish_conflict_when_item_already_published() -> None:
    client = build_client()

    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]
    draft_payload = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": candidate_id, "channels": ["x"], "languages": ["pl"]},
        headers=AUTH_HEADERS,
    ).json()
    queue_payload = client.post(
        f"/api/v1/brand-studio/drafts/{draft_payload['draft_id']}/queue",
        json={"target_channel": "x"},
        headers=AUTH_HEADERS,
    ).json()
    item_id = queue_payload["item_id"]

    first_publish = client.post(
        f"/api/v1/brand-studio/queue/{item_id}/publish",
        json={"confirm_publish": True},
        headers=AUTH_HEADERS,
    )
    assert first_publish.status_code == 200

    second_publish = client.post(
        f"/api/v1/brand-studio/queue/{item_id}/publish",
        json={"confirm_publish": True},
        headers=AUTH_HEADERS,
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

    account_response = client.post(
        "/api/v1/brand-studio/channels/devto/accounts",
        json={"display_name": "No auth account"},
    )
    assert account_response.status_code == 401


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
        headers={"X-Authenticated-User": "blocked-user", "X-Autonomy-Level": "20"},
    )
    assert response.status_code == 403

    account_response = client.post(
        "/api/v1/brand-studio/channels/devto/accounts",
        json={"display_name": "Blocked account"},
        headers={"X-Authenticated-User": "blocked-user", "X-Autonomy-Level": "20"},
    )
    assert account_response.status_code == 403


def test_403_for_mutating_account_endpoint_when_autonomy_too_low() -> None:
    client = build_client()
    response = client.post(
        "/api/v1/brand-studio/channels/devto/accounts",
        json={"display_name": "Low autonomy account"},
        headers={"X-Authenticated-User": "mpieniak", "X-Autonomy-Level": "5"},
    )
    assert response.status_code == 403


def test_config_and_strategies_endpoints_flow() -> None:
    client = build_client()

    get_config = client.get("/api/v1/brand-studio/config")
    assert get_config.status_code == 200
    active_id = get_config.json()["active_strategy_id"]
    assert active_id

    update_config = client.put(
        "/api/v1/brand-studio/config",
        json={"min_score": 0.45, "limit": 15},
        headers=AUTH_HEADERS,
    )
    assert update_config.status_code == 200
    assert update_config.json()["active_strategy"]["min_score"] == 0.45

    create_strategy = client.post(
        "/api/v1/brand-studio/strategies",
        json={"name": "Tech Lead PL", "base_strategy_id": active_id},
        headers=AUTH_HEADERS,
    )
    assert create_strategy.status_code == 200
    created_id = create_strategy.json()["item"]["id"]

    update_strategy = client.put(
        f"/api/v1/brand-studio/strategies/{created_id}",
        json={"limit": 11},
        headers=AUTH_HEADERS,
    )
    assert update_strategy.status_code == 200
    assert update_strategy.json()["item"]["limit"] == 11

    activate = client.post(
        f"/api/v1/brand-studio/strategies/{created_id}/activate",
        headers=AUTH_HEADERS,
    )
    assert activate.status_code == 200
    assert activate.json()["active_strategy_id"] == created_id

    delete = client.delete(
        f"/api/v1/brand-studio/strategies/{created_id}",
        headers=AUTH_HEADERS,
    )
    assert delete.status_code == 204


def test_integrations_endpoints() -> None:
    client = build_client()

    list_response = client.get("/api/v1/brand-studio/integrations")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert any(item["id"] == "github_publish" for item in items)

    test_response = client.post(
        "/api/v1/brand-studio/integrations/rss/test",
        headers=AUTH_HEADERS,
    )
    assert test_response.status_code == 200
    assert "status" in test_response.json()


def test_channel_accounts_crud_flow() -> None:
    client = build_client()

    channels = client.get("/api/v1/brand-studio/channels")
    assert channels.status_code == 200
    assert any(item["id"] == "github" for item in channels.json()["items"])

    create = client.post(
        "/api/v1/brand-studio/channels/devto/accounts",
        json={"display_name": "Dev.to main", "target": "devto-user", "is_default": True},
        headers=AUTH_HEADERS,
    )
    assert create.status_code == 200
    account_id = create.json()["item"]["account_id"]

    listed = client.get("/api/v1/brand-studio/channels/devto/accounts")
    assert listed.status_code == 200
    assert any(item["account_id"] == account_id for item in listed.json()["items"])

    update = client.put(
        f"/api/v1/brand-studio/channels/devto/accounts/{account_id}",
        json={"display_name": "Dev.to updated"},
        headers=AUTH_HEADERS,
    )
    assert update.status_code == 200
    assert update.json()["item"]["display_name"] == "Dev.to updated"

    activate = client.post(
        f"/api/v1/brand-studio/channels/devto/accounts/{account_id}/activate",
        headers=AUTH_HEADERS,
    )
    assert activate.status_code == 200

    test_response = client.post(
        f"/api/v1/brand-studio/channels/devto/accounts/{account_id}/test",
        headers=AUTH_HEADERS,
    )
    assert test_response.status_code == 200
    assert test_response.json()["account_id"] == account_id

    delete = client.delete(
        f"/api/v1/brand-studio/channels/devto/accounts/{account_id}",
        headers=AUTH_HEADERS,
    )
    assert delete.status_code == 204
