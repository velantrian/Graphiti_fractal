"""Provider-free contract for how an unfinished chunk settles its claim.

Live Cypher behavior is covered in tests/integration/test_ingest_claim_lifecycle.py.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from knowledge import ingest

KW = dict(group_id="g", fingerprint="fp", token="t", error_type="X")


def _patched(release=True, failed=None):
    return (
        patch("knowledge.ingest.release_ingest_claim", new=AsyncMock(return_value=release)),
        patch("knowledge.ingest.mark_ingest_claim_failed", new=AsyncMock(side_effect=failed)),
    )


@pytest.mark.asyncio
async def test_no_attempt_and_deleted_claim_is_released_only():
    rel, fail = _patched(release=True)
    with rel as release, fail as failed:
        assert await ingest._settle_unfinished_claim(object(), write_started=False, **KW) == "released"
    assert release.await_args.kwargs["confirmed_no_write"] is True
    assert release.await_args.kwargs["token"] == "t"
    failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_possible_write_never_releases():
    rel, fail = _patched()
    with rel as release, fail as failed:
        assert await ingest._settle_unfinished_claim(object(), write_started=True, **KW) == "reconciliation"
    release.assert_not_awaited()
    failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_refused_by_the_database_falls_back_to_reconciliation():
    # e.g. an acknowledged-ambiguous marker commit: the DB, not the caller, has the last word
    rel, fail = _patched(release=False)
    with rel, fail as failed:
        assert await ingest._settle_unfinished_claim(object(), write_started=False, **KW) == "reconciliation"
    failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_settlement_outage_never_raises_and_never_invents_a_conclusion():
    rel = patch("knowledge.ingest.release_ingest_claim", new=AsyncMock(side_effect=ConnectionError("db down")))
    with rel:
        assert await ingest._settle_unfinished_claim(object(), write_started=False, **KW) == "pending"
    rel = patch("knowledge.ingest.mark_ingest_claim_failed", new=AsyncMock(side_effect=ConnectionError("db down")))
    with rel:
        assert await ingest._settle_unfinished_claim(object(), write_started=True, **KW) == "reconciliation"
