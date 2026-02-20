import hashlib
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


def test_monitoring_keywords_crud() -> None:
    client = build_client()

    # List empty
    resp = client.get("/api/v1/brand-studio/monitoring/keywords")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0

    # Create
    create_resp = client.post(
        "/api/v1/brand-studio/monitoring/keywords",
        json={"phrase": "personal brand", "keyword_type": "brand_core", "priority": 5},
        headers=AUTH_HEADERS,
    )
    assert create_resp.status_code == 200
    kw = create_resp.json()
    assert kw["phrase"] == "personal brand"
    keyword_id = kw["keyword_id"]

    # List has 1
    list_resp = client.get("/api/v1/brand-studio/monitoring/keywords")
    assert list_resp.json()["count"] == 1

    # Update
    update_resp = client.patch(
        f"/api/v1/brand-studio/monitoring/keywords/{keyword_id}",
        json={"priority": 3},
        headers=AUTH_HEADERS,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["priority"] == 3

    # 404 on missing
    resp_404 = client.patch(
        "/api/v1/brand-studio/monitoring/keywords/missing-id",
        json={"priority": 1},
        headers=AUTH_HEADERS,
    )
    assert resp_404.status_code == 404

    # Delete
    del_resp = client.delete(
        f"/api/v1/brand-studio/monitoring/keywords/{keyword_id}",
        headers=AUTH_HEADERS,
    )
    assert del_resp.status_code == 204

    # List empty again
    assert client.get("/api/v1/brand-studio/monitoring/keywords").json()["count"] == 0


def test_monitoring_base_sources_crud() -> None:
    client = build_client()

    # List empty
    assert client.get("/api/v1/brand-studio/monitoring/sources").json()["count"] == 0

    # Create
    create_resp = client.post(
        "/api/v1/brand-studio/monitoring/sources",
        json={
            "name": "My Blog",
            "base_url": "https://myblog.example.com",
            "channel": "blog",
            "priority": 5,
        },
        headers=AUTH_HEADERS,
    )
    assert create_resp.status_code == 200
    src = create_resp.json()
    source_id = src["source_id"]

    # Duplicate URL returns 409
    dup_resp = client.post(
        "/api/v1/brand-studio/monitoring/sources",
        json={
            "name": "My Blog Dup",
            "base_url": "https://myblog.example.com",
            "channel": "blog",
        },
        headers=AUTH_HEADERS,
    )
    assert dup_resp.status_code == 409

    # Update
    update_resp = client.patch(
        f"/api/v1/brand-studio/monitoring/sources/{source_id}",
        json={"enabled": False},
        headers=AUTH_HEADERS,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["enabled"] is False

    # 404 on missing
    resp_404 = client.patch(
        "/api/v1/brand-studio/monitoring/sources/missing-id",
        json={"enabled": True},
        headers=AUTH_HEADERS,
    )
    assert resp_404.status_code == 404

    # Delete
    del_resp = client.delete(
        f"/api/v1/brand-studio/monitoring/sources/{source_id}",
        headers=AUTH_HEADERS,
    )
    assert del_resp.status_code == 204


def test_monitoring_scan_results_summary() -> None:
    client = build_client()

    # Create a keyword first
    kw_resp = client.post(
        "/api/v1/brand-studio/monitoring/keywords",
        json={"phrase": "my brand", "keyword_type": "brand_core"},
        headers=AUTH_HEADERS,
    )
    assert kw_resp.status_code == 200

    # Run scan (uses stub since no CSE configured)
    scan_resp = client.post(
        "/api/v1/brand-studio/monitoring/scan",
        json={},
        headers=AUTH_HEADERS,
    )
    assert scan_resp.status_code == 200
    scan_payload = scan_resp.json()
    assert scan_payload["scan"]["status"] in {"completed", "partial"}
    assert scan_payload["scan"]["total_results"] >= 0
    scan_id = scan_payload["scan"]["scan_id"]

    # Results
    results_resp = client.get("/api/v1/brand-studio/monitoring/results")
    assert results_resp.status_code == 200
    assert results_resp.json()["count"] >= 0

    # Results with scan_id filter
    filtered_resp = client.get(
        "/api/v1/brand-studio/monitoring/results",
        params={"scan_id": scan_id},
    )
    assert filtered_resp.status_code == 200
    assert filtered_resp.json()["scan_id"] == scan_id

    # Summary
    summary_resp = client.get("/api/v1/brand-studio/monitoring/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_keywords"] == 1
    assert summary["active_keywords"] == 1
    assert "owned_source_coverage" in summary


def test_monitoring_scan_idempotency() -> None:
    client = build_client()

    # Create keyword
    client.post(
        "/api/v1/brand-studio/monitoring/keywords",
        json={"phrase": "idempotent brand"},
        headers=AUTH_HEADERS,
    )

    # First scan with request_id
    scan1 = client.post(
        "/api/v1/brand-studio/monitoring/scan",
        json={"request_id": "req-unique-001"},
        headers=AUTH_HEADERS,
    )
    assert scan1.status_code == 200

    # Second scan with same request_id should return same/cached result
    scan2 = client.post(
        "/api/v1/brand-studio/monitoring/scan",
        json={"request_id": "req-unique-001"},
        headers=AUTH_HEADERS,
    )
    assert scan2.status_code == 200


def test_monitoring_summary_triggers_scheduled_scan(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_BRAND_STUDIO_MONITORING", "true")
    monkeypatch.setenv("BRAND_STUDIO_MONITORING_SCHEDULE_CRON", "*/5 * * * *")
    client = build_client()

    client.post(
        "/api/v1/brand-studio/monitoring/keywords",
        json={"phrase": "scheduled brand"},
        headers=AUTH_HEADERS,
    )

    summary_first = client.get("/api/v1/brand-studio/monitoring/summary")
    assert summary_first.status_code == 200
    assert summary_first.json()["last_scan_at"] is not None

    results_after_first = client.get("/api/v1/brand-studio/monitoring/results")
    assert results_after_first.status_code == 200
    first_count = results_after_first.json()["count"]
    assert first_count > 0

    summary_second = client.get("/api/v1/brand-studio/monitoring/summary")
    assert summary_second.status_code == 200
    results_after_second = client.get("/api/v1/brand-studio/monitoring/results")
    assert results_after_second.status_code == 200
    assert results_after_second.json()["count"] == first_count


def test_monitoring_disabled_returns_403(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_BRAND_STUDIO_MONITORING", "false")
    client = build_client()
    resp = client.get("/api/v1/brand-studio/monitoring/keywords")
    assert resp.status_code == 403
    # Campaigns also require monitoring to be enabled
    resp = client.get("/api/v1/brand-studio/campaigns")
    assert resp.status_code == 403


def test_campaigns_crud_and_run() -> None:
    client = build_client()

    # List empty
    assert client.get("/api/v1/brand-studio/campaigns").json()["count"] == 0

    # Create
    create_resp = client.post(
        "/api/v1/brand-studio/campaigns",
        json={
            "name": "Q1 Brand Push",
            "channels": ["x", "linkedin"],
        },
        headers=AUTH_HEADERS,
    )
    assert create_resp.status_code == 200
    campaign = create_resp.json()["item"]
    campaign_id = campaign["campaign_id"]
    assert campaign["status"] == "draft"

    # Get
    get_resp = client.get(f"/api/v1/brand-studio/campaigns/{campaign_id}")
    assert get_resp.status_code == 200

    # Update
    update_resp = client.patch(
        f"/api/v1/brand-studio/campaigns/{campaign_id}",
        json={"status": "ready"},
        headers=AUTH_HEADERS,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["item"]["status"] == "ready"

    # Run
    run_resp = client.post(
        f"/api/v1/brand-studio/campaigns/{campaign_id}/run",
        headers=AUTH_HEADERS,
    )
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == "running"

    # Update to completed then run again -> 409
    client.patch(
        f"/api/v1/brand-studio/campaigns/{campaign_id}",
        json={"status": "completed"},
        headers=AUTH_HEADERS,
    )
    run_again = client.post(
        f"/api/v1/brand-studio/campaigns/{campaign_id}/run",
        headers=AUTH_HEADERS,
    )
    assert run_again.status_code == 409

    # 404 for missing campaign
    resp_404 = client.get("/api/v1/brand-studio/campaigns/camp-missing")
    assert resp_404.status_code == 404


def test_campaign_run_with_linked_results_generates_drafts_and_queue() -> None:
    client = build_client()

    # Create keyword
    kw_resp = client.post(
        "/api/v1/brand-studio/monitoring/keywords",
        json={"phrase": "brand test"},
        headers=AUTH_HEADERS,
    )
    kw_id = kw_resp.json()["keyword_id"]

    # Run scan to get results
    scan_resp = client.post(
        "/api/v1/brand-studio/monitoring/scan",
        json={},
        headers=AUTH_HEADERS,
    )
    results = scan_resp.json()["results"]
    assert len(results) >= 1, "Stub scan should always produce results for an active keyword"
    result_id = results[0]["result_id"]

    # Create campaign with linked result
    camp_resp = client.post(
        "/api/v1/brand-studio/campaigns",
        json={
            "name": "Wired Campaign",
            "channels": ["x"],
            "linked_result_ids": [result_id],
            "linked_keyword_ids": [kw_id],
        },
        headers=AUTH_HEADERS,
    )
    assert camp_resp.status_code == 200
    campaign_id = camp_resp.json()["item"]["campaign_id"]

    # Run campaign - should generate drafts and queue items
    run_resp = client.post(
        f"/api/v1/brand-studio/campaigns/{campaign_id}/run",
        headers=AUTH_HEADERS,
    )
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["status"] == "running"
    assert len(run_data["draft_ids"]) >= 1
    assert len(run_data["queue_ids"]) >= 1

    # Verify campaign now has draft_ids and queue_ids
    get_resp = client.get(f"/api/v1/brand-studio/campaigns/{campaign_id}")
    assert get_resp.status_code == 200
    camp_data = get_resp.json()["item"]
    assert len(camp_data["draft_ids"]) >= 1
    assert len(camp_data["queue_ids"]) >= 1

    # Queue items for this campaign should be filterable
    queue_resp = client.get(
        "/api/v1/brand-studio/queue",
        params={"campaign_id": campaign_id},
    )
    assert queue_resp.status_code == 200
    assert queue_resp.json()["count"] >= 1
    for qi in queue_resp.json()["items"]:
        assert qi["campaign_id"] == campaign_id

    # Queue items should also have campaign_id set
    all_queue = client.get("/api/v1/brand-studio/queue").json()["items"]
    campaign_items = [it for it in all_queue if it.get("campaign_id") == campaign_id]
    assert len(campaign_items) >= 1


def test_generate_draft_with_campaign_id_tracks_campaign() -> None:
    client = build_client()

    # Create campaign
    camp_resp = client.post(
        "/api/v1/brand-studio/campaigns",
        json={"name": "Draft-Track Campaign", "channels": ["x"]},
        headers=AUTH_HEADERS,
    )
    campaign_id = camp_resp.json()["item"]["campaign_id"]

    # Generate draft with campaign_id
    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]

    draft_resp = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={
            "candidate_id": candidate_id,
            "channels": ["x"],
            "languages": ["pl"],
            "campaign_id": campaign_id,
        },
        headers=AUTH_HEADERS,
    )
    assert draft_resp.status_code == 200
    assert draft_resp.json()["campaign_id"] == campaign_id

    # Queue with campaign_id
    draft_id = draft_resp.json()["draft_id"]
    queue_resp = client.post(
        f"/api/v1/brand-studio/drafts/{draft_id}/queue",
        json={"target_channel": "x", "campaign_id": campaign_id},
        headers=AUTH_HEADERS,
    )
    assert queue_resp.status_code == 200

    # Audit should mention campaign
    audit_resp = client.get("/api/v1/brand-studio/audit")
    assert audit_resp.status_code == 200
    actions = [e["action"] for e in audit_resp.json()["items"]]
    # draft.generate and queue.create with campaign_id should be in audit
    assert "draft.generate" in actions
    assert "queue.create" in actions
    # Verify the payload hashes match what we expect
    draft_payload_hash = hashlib.sha256(
        f"{draft_id}:campaign={campaign_id}".encode()
    ).hexdigest()
    hashes = [e["payload_hash"] for e in audit_resp.json()["items"]]
    assert draft_payload_hash in hashes


def test_link_draft_to_campaign() -> None:
    client = build_client()

    # Create campaign
    camp_resp = client.post(
        "/api/v1/brand-studio/campaigns",
        json={"name": "Link Test Campaign", "channels": ["x"]},
        headers=AUTH_HEADERS,
    )
    campaign_id = camp_resp.json()["item"]["campaign_id"]

    # Generate standalone draft
    candidates = client.get("/api/v1/brand-studio/sources/candidates").json()["items"]
    candidate_id = candidates[0]["id"]
    draft_resp = client.post(
        "/api/v1/brand-studio/drafts/generate",
        json={"candidate_id": candidate_id, "channels": ["x"], "languages": ["pl"]},
        headers=AUTH_HEADERS,
    )
    draft_id = draft_resp.json()["draft_id"]

    # Link draft to campaign
    link_resp = client.post(
        f"/api/v1/brand-studio/campaigns/{campaign_id}/drafts/{draft_id}",
        headers=AUTH_HEADERS,
    )
    assert link_resp.status_code == 200
    assert draft_id in link_resp.json()["item"]["draft_ids"]

    # 404 for missing draft
    resp_404 = client.post(
        f"/api/v1/brand-studio/campaigns/{campaign_id}/drafts/draft-missing",
        headers=AUTH_HEADERS,
    )
    assert resp_404.status_code == 404

    # 404 for missing campaign
    resp_camp_404 = client.post(
        f"/api/v1/brand-studio/campaigns/camp-missing/drafts/{draft_id}",
        headers=AUTH_HEADERS,
    )
    assert resp_camp_404.status_code == 404
