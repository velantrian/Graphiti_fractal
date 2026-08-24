import pytest

from core.instance import get_instance_user_id, require_instance_user_id


def test_instance_owner_is_configured_from_env(monkeypatch):
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    assert get_instance_user_id() == "owner"
    assert require_instance_user_id("owner") == "owner"


def test_instance_owner_rejects_other_identity(monkeypatch):
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    with pytest.raises(PermissionError):
        require_instance_user_id("other")


def test_instance_owner_rejects_empty_config(monkeypatch):
    monkeypatch.setenv("FRACTAL_USER_ID", "   ")
    with pytest.raises(RuntimeError):
        get_instance_user_id()
