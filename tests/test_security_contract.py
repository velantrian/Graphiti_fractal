from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app import FractalStaticFiles, app, require_api_token
from visualization.visualization_export import CANONICAL_PUBLIC_GRAPH_DATA, validate_export_path


@pytest.mark.asyncio
async def test_protected_api_is_disabled_without_configured_token(monkeypatch):
    monkeypatch.delenv("FRACTAL_API_TOKEN", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        await require_api_token(None)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_protected_api_rejects_missing_and_wrong_token(monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", "expected-token")

    with pytest.raises(HTTPException) as missing:
        await require_api_token(None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        await require_api_token("Bearer wrong-token")
    assert wrong.value.status_code == 401


@pytest.mark.asyncio
async def test_protected_api_accepts_exact_bearer_token(monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", "expected-token")
    assert await require_api_token("Bearer expected-token") is None


@pytest.mark.asyncio
async def test_visualization_graph_data_route_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", "expected-token")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/visualization/graph_data.json")
        wrong = await client.get(
            "/visualization/graph_data.json",
            headers={"Authorization": "Bearer wrong-token"},
        )
        authenticated = await client.get(
            "/visualization/graph_data.json",
            headers={"Authorization": "Bearer expected-token"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert authenticated.status_code == 404


@pytest.mark.asyncio
async def test_public_static_mount_never_serves_graph_data(tmp_path):
    (tmp_path / "graph_data.json").write_text('{"secret": true}', encoding="utf-8")
    static = FractalStaticFiles(directory=tmp_path)

    response = await static.get_response(
        "graph_data.json",
        {"type": "http", "method": "GET", "path": "/static/graph_data.json", "headers": []},
    )

    assert response.status_code == 404


def test_visualization_export_rejects_alternate_public_static_alias():
    alternate = CANONICAL_PUBLIC_GRAPH_DATA.parent / "owner_graph_export.json"
    with pytest.raises(ValueError):
        validate_export_path(str(alternate))


def test_visualization_export_allows_only_canonical_public_static_path():
    assert validate_export_path(str(CANONICAL_PUBLIC_GRAPH_DATA)) == CANONICAL_PUBLIC_GRAPH_DATA


def test_visualization_export_allows_non_public_operator_path(tmp_path):
    operator_path = tmp_path / "owner_graph_export.json"
    assert validate_export_path(str(operator_path)) == Path(operator_path).resolve()
