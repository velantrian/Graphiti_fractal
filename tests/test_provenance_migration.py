from core.provenance_migration import plan_legacy_artifact


def test_chat_summary_with_real_sources_is_ready():
    plan = plan_legacy_artifact({
        "uuid": "summary-1",
        "episode_kind": "chat_summary",
        "content": "summary",
        "summarized_turns": ["t1", "t2"],
        "provenance_id": None,
    })
    assert plan["status"] == "READY"
    assert plan["source_ids"] == ["t1", "t2"]
    assert plan["writes_performed"] is False
    assert plan["provenance"]["authoritative_fact"] is False


def test_legacy_l3_without_source_ids_fails_closed():
    plan = plan_legacy_artifact({
        "uuid": "l3-legacy",
        "episode_kind": "l3_profile",
        "content": "profile",
        "derived_source_ids": None,
        "provenance_id": None,
    })
    assert plan["status"] == "BLOCKED_MISSING_SOURCE_IDS"
    assert plan["writes_performed"] is False


def test_existing_provenance_is_never_rewritten():
    plan = plan_legacy_artifact({
        "uuid": "summary-2",
        "episode_kind": "chat_summary",
        "content": "summary",
        "summarized_turns": ["t1"],
        "provenance_id": "prov:chat_summary:existing",
    })
    assert plan["status"] == "ALREADY_MIGRATED"
    assert plan["writes_performed"] is False
