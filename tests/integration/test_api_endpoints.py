"""Integration tests for the authenticated FastAPI surface."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="NEO4J_URI not set - skipping integration tests",
)

TEST_TOKEN = "integration-test-token"


@pytest_asyncio.fixture
async def graphiti_for_api_test():
    from core.graphiti_client import get_graphiti_client, reset_graphiti_client

    reset_graphiti_client()
    client = get_graphiti_client(force_new=True)
    graphiti = await client.ensure_ready()
    try:
        yield graphiti
    finally:
        try:
            driver = getattr(graphiti, "driver", None)
            if driver and hasattr(driver, "close"):
                await driver.close()
        finally:
            reset_graphiti_client()


@pytest_asyncio.fixture
async def async_client(graphiti_for_api_test, monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("FRACTAL_ALLOW_HARD_DELETE", raising=False)
    monkeypatch.delenv("FRACTAL_ALLOW_CLEAR_ALL", raising=False)

    from app import app, get_graphiti_dep

    async def override_graphiti():
        return graphiti_for_api_test

    app.dependency_overrides[get_graphiti_dep] = override_graphiti
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_endpoint_is_public(async_client):
    response = await async_client.get("/health", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json()["status"] in {"healthy", "unhealthy"}


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_missing_token(async_client):
    response = await async_client.post(
        "/buffer/clear",
        headers={"Authorization": ""},
        json={"user_id": "api_test_user"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_wrong_token(async_client):
    response = await async_client.post(
        "/buffer/clear",
        headers={"Authorization": "Bearer wrong-token"},
        json={"user_id": "api_test_user"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_remember_endpoint(async_client):
    response = await async_client.post(
        "/remember",
        json={
            "text": "Тестовый текст для API теста.",
            "user_id": "api_test_user",
            "memory_type": "knowledge",
            "source_description": "api_integration_test",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "success", "skipped"}


@pytest.mark.asyncio
async def test_remember_empty_text_is_validation_error(async_client):
    response = await async_client.post(
        "/remember",
        json={"text": "", "user_id": "api_test_user"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_knowledge_search_endpoint(async_client):
    response = await async_client.get(
        "/knowledge/search",
        params={"q": "test query", "limit": 5},
    )
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)


@pytest.mark.asyncio
async def test_buffer_clear_endpoint(async_client):
    response = await async_client.post(
        "/buffer/clear",
        json={"user_id": "api_test_user"},
    )
    assert response.status_code == 200
    assert "cleared" in response.json()


@pytest.mark.asyncio
async def test_upload_status_not_found(async_client):
    response = await async_client.get("/upload/status/nonexistent-job-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_hard_delete_is_deny_by_default(async_client):
    response = await async_client.post(
        "/delete",
        json={"uuid": "does-not-matter", "hard": True},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_clear_all_is_deny_by_default(async_client):
    response = await async_client.post(
        "/clear_memory",
        headers={
            "Authorization": f"Bearer {TEST_TOKEN}",
            "X-Fractal-Confirm": "CLEAR_ALL_MEMORY",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_openapi_docs_available(async_client):
    response = await async_client.get("/openapi.json", headers={"Authorization": ""})
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "Fractal Memory API"
