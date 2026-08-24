"""Single-tenant instance identity helpers.

Fractal Memory is a local single-owner service. API/MCP callers may not switch
user identity per request; the configured instance owner is authoritative.
"""

import os


def get_instance_user_id() -> str:
    user_id = (os.getenv("FRACTAL_USER_ID") or "sergey").strip()
    if not user_id:
        raise RuntimeError("FRACTAL_USER_ID must not be empty")
    return user_id


def require_instance_user_id(requested_user_id: str) -> str:
    configured = get_instance_user_id()
    if requested_user_id != configured:
        raise PermissionError(
            f"user_id {requested_user_id!r} is not allowed for this single-tenant instance"
        )
    return configured
