#!/usr/bin/env python3
"""Manual smoke client for the local Fractal Memory HTTP API."""

import os
import sys

import httpx

API_BASE = os.getenv("FRACTAL_API_BASE", "http://127.0.0.1:8000")
API_TOKEN = (os.getenv("FRACTAL_API_TOKEN") or "").strip()
USER_ID = (os.getenv("FRACTAL_USER_ID") or "sergey").strip()


def _headers() -> dict[str, str]:
    if not API_TOKEN:
        raise RuntimeError("FRACTAL_API_TOKEN is required")
    return {"Authorization": f"Bearer {API_TOKEN}"}


def remember(text: str, memory_type: str = "personal") -> dict:
    response = httpx.post(
        f"{API_BASE}/remember",
        headers=_headers(),
        json={
            "text": text,
            "memory_type": memory_type,
            "source_description": "manual_api_smoke",
            "user_id": USER_ID,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def chat(message: str) -> dict:
    response = httpx.post(
        f"{API_BASE}/chat",
        headers=_headers(),
        json={"message": message, "user_id": USER_ID},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def interactive() -> None:
    print(f"🌐 Fractal Memory manual API smoke (owner={USER_ID})")
    print("/remember <text> | /chat <message> | /quit")
    while True:
        try:
            command = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not command:
            continue
        if command in {"/quit", "quit", "exit"}:
            return
        if command.startswith("/remember "):
            print(remember(command.removeprefix("/remember ").strip()))
        elif command.startswith("/chat "):
            print(chat(command.removeprefix("/chat ").strip()).get("reply", ""))
        else:
            print("Unknown command")


if __name__ == "__main__":
    try:
        interactive()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
