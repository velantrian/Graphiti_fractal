"""Claim lifecycle of the canonical ingest path, on a real disposable Neo4j.

Only Graphiti's ``add_episode`` is replaced by a recording stub that sees the
exact call; claims, identity finalization, episode lookup and the HTTP layer run
for real. "Reached Graphiti" is read from ``stub.calls`` and from the durable
write-attempt marker (``write_attempts``/``write_attempted_at``) on the claim.
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from graphiti_core.llm_client.errors import RateLimitError

from core.ingest_atomicity import (
    IngestClaimLost,
    acquire_ingest_claim,
    mark_ingest_claim_write_attempt,
    read_ingest_claim,
    release_ingest_claim,
)
from core.text_utils import fingerprint
from knowledge.ingest import ingest_text_document
from tests.support.graphiti_stub import (
    RaisingSemaphore, StubDriver, StubGraphiti, raise_without_writing, write_degraded,
    write_then_raise,
)

ROOT = Path(__file__).resolve().parents[2]
GROUP = "claim-lifecycle"


def _text(label: str) -> str:
    return f"Synthetic claim lifecycle fact {label}: the archive room key is kept in drawer {label}. " * 3


@pytest.fixture
def stub(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    return StubGraphiti(StubDriver(isolated_graph._raw, neo4j_isolation.database))


async def _claim(stub, text):
    rows = (await stub.driver.execute_query(
        "MATCH (c:FractalIngestClaim {claim_key:$k}) "
        "RETURN c.state AS state, c.token AS token, c.write_attempts AS attempts, "
        "c.write_attempted_at IS NOT NULL AS marked, c.episode_uuid AS episode, "
        "c.ingest_quality AS quality", k=f"{GROUP}:{fingerprint(text.strip())}")).records
    return dict(rows[0]) if rows else None


async def _episodes(stub, text):
    rows = (await stub.driver.execute_query(
        "MATCH (e:Episodic {group_id:$g}) WHERE e.content = $c RETURN count(e) AS n",
        g=GROUP, c=text.strip())).records
    return rows[0]["n"]


async def _expire(stub, text):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    await stub.driver.execute_query(
        "MATCH (c:FractalIngestClaim {claim_key:$k}) SET c.expires_at = datetime($past)",
        k=f"{GROUP}:{fingerprint(text.strip())}", past=past)


async def _ingest(stub, text, **kw):
    return await ingest_text_document(stub, text, group_id=GROUP, user_id="owner", **kw)


# --- proven failure before anything reached Graphiti --------------------------

@pytest.mark.asyncio
async def test_pre_send_failure_releases_own_claim_and_retry_writes_once(stub, monkeypatch):
    text = _text("pre-send")
    monkeypatch.setattr("knowledge.ingest.get_write_semaphore",
                        lambda: RaisingSemaphore(RuntimeError("pre-send failure")))
    first = await _ingest(stub, text)
    assert first["completeness"] == "failed" and first["failed_chunks"] == 1
    assert first["unconfirmed_writes"] == 0
    assert len(stub.calls) == 0                      # first attempt never called Graphiti
    assert await _claim(stub, text) is None           # only its own claim, released
    assert await _episodes(stub, text) == 0

    monkeypatch.setattr("knowledge.ingest.get_write_semaphore", lambda: asyncio.Semaphore(1))
    retry = await _ingest(stub, text)
    assert retry["completeness"] == "complete" and retry["added"] == 1
    assert len(stub.calls) == 1
    assert (await _claim(stub, text))["state"] == "COMMITTED"
    assert await _episodes(stub, text) == 1


@pytest.mark.asyncio
async def test_cancellation_before_send_releases_and_after_send_keeps_for_reconciliation(stub, monkeypatch):
    held = asyncio.Semaphore(1)
    await held.acquire()                              # the write path cannot start
    monkeypatch.setattr("knowledge.ingest.get_write_semaphore", lambda: held)
    text = _text("cancel-before")
    task = asyncio.create_task(_ingest(stub, text))
    for _ in range(100):
        if await _claim(stub, text):
            break
        await asyncio.sleep(0.05)
    assert (await _claim(stub, text))["state"] == "PENDING"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(stub.calls) == 0 and await _claim(stub, text) is None
    held.release()
    retry = await _ingest(stub, text)
    assert retry["completeness"] == "complete" and len(stub.calls) == 1

    # Cancelled inside the call: the write may have landed -> kept, not released.
    monkeypatch.setattr("knowledge.ingest.get_write_semaphore", lambda: asyncio.Semaphore(1))
    text2 = _text("cancel-after")
    stub.plan = [write_then_raise(asyncio.CancelledError())]
    with pytest.raises(asyncio.CancelledError):
        await _ingest(stub, text2)
    claim = await _claim(stub, text2)
    assert claim["state"] == "RECONCILIATION_REQUIRED" and claim["attempts"] == 1


# --- possible external write ----------------------------------------------------

@pytest.mark.asyncio
async def test_ambiguous_outcome_is_kept_and_retry_neither_duplicates_nor_claims_complete(stub):
    text = _text("ambiguous")
    stub.plan = [write_then_raise(TimeoutError("response lost after commit"))]
    first = await _ingest(stub, text)
    assert first["completeness"] == "unconfirmed" and first["unconfirmed_writes"] == 1
    claim = await _claim(stub, text)
    assert claim["state"] == "RECONCILIATION_REQUIRED" and claim["marked"] and claim["attempts"] == 1
    assert await _episodes(stub, text) == 1           # the upstream write did land

    for _ in range(2):                                # repeated retries
        retry = await _ingest(stub, text)
        assert retry["completeness"] == "unconfirmed" and retry["added"] == 0
    assert len(stub.calls) == 1 and await _episodes(stub, text) == 1
    assert (await read_ingest_claim(stub, group_id=GROUP,
                                    fingerprint=fingerprint(text.strip())))["status"] == "unconfirmed"


@pytest.mark.asyncio
async def test_error_from_graphiti_without_a_write_is_still_unconfirmed(stub):
    """The caller cannot tell a raised error from a lost response: keep it."""
    text = _text("error-no-write")
    stub.plan = [raise_without_writing(RuntimeError("provider error"))]
    first = await _ingest(stub, text)
    assert first["completeness"] == "unconfirmed" and first["failed_chunks"] == 0
    assert (await _claim(stub, text))["state"] == "RECONCILIATION_REQUIRED"
    retry = await _ingest(stub, text)
    assert retry["completeness"] == "unconfirmed" and len(stub.calls) == 1


@pytest.mark.asyncio
async def test_rate_limit_retry_counts_each_attempt_and_can_complete(stub):
    text = _text("rate-limit")
    stub.plan = [raise_without_writing(RateLimitError("Please try again in 0.01s."))]
    result = await _ingest(stub, text)
    assert result["completeness"] == "complete" and result["added"] == 1
    assert len(stub.calls) == 2
    claim = await _claim(stub, text)
    assert claim["state"] == "COMMITTED" and claim["attempts"] == 2


@pytest.mark.asyncio
async def test_degraded_write_keeps_its_quality_on_retry(stub):
    text = _text("degraded")
    stub.plan = [write_degraded()]
    first = await _ingest(stub, text)
    assert first["degraded_chunks"] == 1 and first["completeness"] != "complete"
    assert (await _claim(stub, text))["quality"] == "degraded"
    retry = await _ingest(stub, text)
    assert retry["completeness"] != "complete" and retry["added"] == 0
    assert len(stub.calls) == 1 and await _episodes(stub, text) == 1
    claim = await _claim(stub, text)
    assert claim["state"] == "COMMITTED" and claim["quality"] == "degraded"   # not upgraded
    assert (await read_ingest_claim(stub, group_id=GROUP,
                                    fingerprint=fingerprint(text.strip())))["status"] == "committed_degraded"


# --- concurrency, foreign tokens, old records --------------------------------------

@pytest.mark.asyncio
async def test_concurrent_identical_ingests_write_exactly_once(stub):
    text = _text("concurrent")

    async def slow_write(s, kwargs):
        await asyncio.sleep(0.3)
        return await s.write(kwargs)

    stub.plan = [slow_write]
    results = await asyncio.gather(*[_ingest(stub, text) for _ in range(6)])
    assert sum(r["added"] for r in results) == 1
    assert len(stub.calls) == 1 and await _episodes(stub, text) == 1
    assert all(r["completeness"] in {"complete", "unconfirmed"} for r in results)
    assert (await _claim(stub, text))["state"] == "COMMITTED"


@pytest.mark.asyncio
async def test_release_and_attempt_marker_only_work_for_the_owning_token(stub):
    fp = "owner-token"
    mine = await acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp)
    assert await release_ingest_claim(stub, group_id=GROUP, fingerprint=fp,
                                      token="someone-elses-token", confirmed_no_write=True) is False
    with pytest.raises(IngestClaimLost):
        await mark_ingest_claim_write_attempt(stub, group_id=GROUP, fingerprint=fp, token="someone-elses-token")
    with pytest.raises(PermissionError):
        await release_ingest_claim(stub, group_id=GROUP, fingerprint=fp, token=mine)
    rows = (await stub.driver.execute_query(
        "MATCH (c:FractalIngestClaim {claim_key:$k}) RETURN c.token AS t, c.state AS s, "
        "c.write_attempts AS a", k=f"{GROUP}:{fp}")).records
    assert (rows[0]["t"], rows[0]["s"], rows[0]["a"]) == (mine, "PENDING", None)

    assert await mark_ingest_claim_write_attempt(stub, group_id=GROUP, fingerprint=fp, token=mine) == 1
    # With a recorded attempt even the owner cannot release: a write may exist.
    assert await release_ingest_claim(stub, group_id=GROUP, fingerprint=fp, token=mine,
                                      confirmed_no_write=True) is False
    await _expire_key(stub, fp)
    assert await acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp) is None


async def _expire_key(stub, fp):
    await stub.driver.execute_query(
        "MATCH (c:FractalIngestClaim {claim_key:$k}) SET c.expires_at = datetime($p)",
        k=f"{GROUP}:{fp}", p=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())


@pytest.mark.asyncio
async def test_expired_unattempted_claim_is_taken_over_without_harming_the_new_owner(stub):
    fp = "takeover"
    stale = await acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp)
    assert await acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp) is None   # live claim
    await _expire_key(stub, fp)
    fresh = await acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp)
    assert fresh and fresh != stale
    # The slow former owner can neither write nor delete the new owner's claim.
    with pytest.raises(IngestClaimLost):
        await mark_ingest_claim_write_attempt(stub, group_id=GROUP, fingerprint=fp, token=stale)
    assert await release_ingest_claim(stub, group_id=GROUP, fingerprint=fp, token=stale,
                                      confirmed_no_write=True) is False
    rows = (await stub.driver.execute_query(
        "MATCH (c:FractalIngestClaim {claim_key:$k}) RETURN c.token AS t", k=f"{GROUP}:{fp}")).records
    assert [r["t"] for r in rows] == [fresh]


@pytest.mark.asyncio
async def test_concurrent_takeover_of_an_expired_claim_has_a_single_winner(stub):
    fp = "takeover-race"
    await acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp)
    await _expire_key(stub, fp)
    tokens = await asyncio.gather(*[
        acquire_ingest_claim(stub, group_id=GROUP, fingerprint=fp) for _ in range(8)])
    assert len([t for t in tokens if t]) == 1


@pytest.mark.asyncio
async def test_old_records_without_the_new_metadata_are_never_taken_over(stub):
    text = _text("legacy")
    fp = fingerprint(text.strip())
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # Written by code that predates the marker: no attempt_protocol, no marker.
    await stub.driver.execute_query(
        "CREATE (:FractalIngestClaim {claim_key:$k, group_id:$g, fingerprint:$f, token:'old', "
        "state:'PENDING', created_at:datetime($p), expires_at:datetime($p)})",
        k=f"{GROUP}:{fp}", g=GROUP, f=fp, p=past)
    result = await _ingest(stub, text)
    assert result["completeness"] == "unconfirmed" and result["skipped_claim_held"] == 1
    assert len(stub.calls) == 0 and await _episodes(stub, text) == 0

    # A legacy episode (no fingerprint/claim/quality): reconciliation, never "complete".
    text2 = _text("legacy-episode")
    await stub.driver.execute_query(
        "CREATE (:Episodic {uuid:'legacy-ep', content:$c, group_id:$g, source_description:'uploaded_text'})",
        c=text2.strip(), g=GROUP)
    result = await _ingest(stub, text2)
    assert result["completeness"] == "unconfirmed" and result["added"] == 0
    assert len(stub.calls) == 0 and await _episodes(stub, text2) == 1


# --- process crash ---------------------------------------------------------------------

def _child(mode, text):
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}
    return subprocess.run(
        [sys.executable, "-m", "tests.support.ingest_crash_child", mode, GROUP, text.strip()],
        cwd=ROOT, env={**env, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=120)


@pytest.mark.asyncio
async def test_crash_before_send_is_recoverable_after_ttl_and_crash_inside_send_is_not(stub, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_write_semaphore", lambda: asyncio.Semaphore(1))
    before, inside = _text("crash-before"), _text("crash-inside")

    assert _child("before-send", before).returncode == 137
    claim = await _claim(stub, before)
    assert claim["state"] == "PENDING" and not claim["marked"]
    assert (await _ingest(stub, before))["completeness"] == "unconfirmed"       # TTL not over: live claim
    await _expire(stub, before)
    recovered = await _ingest(stub, before)
    assert recovered["completeness"] == "complete" and len(stub.calls) == 1

    assert _child("inside-send", inside).returncode == 137
    claim = await _claim(stub, inside)
    assert claim["state"] == "PENDING" and claim["marked"]                     # attempt marker survived
    await _expire(stub, inside)
    again = await _ingest(stub, inside)
    assert again["completeness"] == "unconfirmed" and again["added"] == 0
    assert len(stub.calls) == 1                                                 # only the recovered one above
    assert await _episodes(stub, inside) == 0                                   # (child died before writing)


# --- public interface -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_remember_reports_failed_then_complete_for_the_same_text(stub, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app import app

    monkeypatch.setenv("FRACTAL_API_TOKEN", "claim-lifecycle-token")
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    async def stub_dep():
        return stub

    # app.py calls get_graphiti_dep() directly, so replace the function itself.
    monkeypatch.setattr("app.get_graphiti_dep", stub_dep)
    text = _text("http")
    headers = {"Authorization": "Bearer claim-lifecycle-token"}
    body = {"text": text, "memory_type": "knowledge"}
    from core.config import get_config
    group = get_config().memory.knowledge_group_id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        monkeypatch.setattr("knowledge.ingest.get_write_semaphore",
                            lambda: RaisingSemaphore(RuntimeError("pre-send")))
        first = await client.post("/remember", json=body, headers=headers)
        assert first.status_code == 200
        assert first.json()["completeness"] == "failed" and len(stub.calls) == 0

        monkeypatch.setattr("knowledge.ingest.get_write_semaphore", lambda: asyncio.Semaphore(1))
        second = await client.post("/remember", json=body, headers=headers)
        assert second.json()["completeness"] == "complete" and len(stub.calls) == 1

        stub.plan = [write_then_raise(TimeoutError("lost"))]
        other = {"text": _text("http-ambiguous"), "memory_type": "knowledge"}
        third = await client.post("/remember", json=other, headers=headers)
        assert third.json()["completeness"] == "unconfirmed"
        fourth = await client.post("/remember", json=other, headers=headers)
        assert fourth.json()["completeness"] == "unconfirmed" and len(stub.calls) == 2
    rows = (await stub.driver.execute_query(
        "MATCH (e:Episodic) WHERE e.content IN [$a, $b] RETURN count(e) AS n",
        a=text.strip(), b=_text("http-ambiguous").strip())).records
    assert rows[0]["n"] == 2 and group
