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
