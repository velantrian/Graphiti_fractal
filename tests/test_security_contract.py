import pytest
from fastapi import HTTPException

from app import require_api_token


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
