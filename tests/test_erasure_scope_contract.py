"""Provider-free contracts for variant B's erasure containment (live behavior: tests/integration)."""
import subprocess
from pathlib import Path

from core import erasure_guard as guard
from core.erasure_guard import blocked_node, token_hashes, unknown_lineage, visible_edge, visible_node

ROOT = Path(__file__).resolve().parents[1]


def test_no_runtime_code_has_a_global_quarantine_or_a_whole_graph_deleted_scan():
    offenders = []
    for path in list(ROOT.glob("*.py")) + [p for d in ("core", "layers", "knowledge", "queries", "visualization",
                                                      "mcp_server", "experience", "api") for p in (ROOT / d).rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if "MATCH (d) WHERE d.deleted" in text or "NOT EXISTS { MATCH (:FractalErasure) }" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_predicates_are_null_safe_and_use_unique_variable_names():
    fragment = blocked_node("x")
    assert "coalesce(x.episode_kind,'')" in fragment and "coalesce(x.origin_class,'')" in fragment
    assert "x.episode_kind IN" not in fragment.replace("coalesce(x.episode_kind,'') IN", "")
    # EXISTS sub-patterns must not reuse names a caller binds (edge endpoints use s/t/r/n/m/e)
    assert "fz_t" in fragment and "(t:FractalErasure" not in fragment
    assert visible_node("x").startswith("NOT ") and "NOT EXISTS" in visible_edge("r", "s", "t")
    assert "size(coalesce(x.derived_source_ids,[])) = 0" in unknown_lineage("x")


def test_tombstone_keeps_hashed_tokens_not_the_erased_text():
    hashes = token_hashes("The vault passphrase ZORBLAX-ALPHA stays private")
    assert hashes and all(len(h) == 16 for h in hashes)
    assert not any("zorblax" in h.lower() for h in hashes)
    assert token_hashes("") == []


def test_erase_never_deletes_a_tombstone_and_state_is_not_a_scan():
    source = (ROOT / "core/erasure_guard.py").read_text(encoding="utf-8")
    assert "DELETE t" not in source and "DELETE fz" not in source
    assert "MATCH (n) WHERE" not in source          # no label-less scans in the guard
    assert guard.OPEN_TOMBSTONE.startswith("(t.status IS NULL")


def test_a_token_screen_is_never_recorded_as_evidence_and_rebuild_needs_an_extractor_to_reopen_edges():
    source = (ROOT / "core/erasure_scope.py").read_text(encoding="utf-8")
    assert ":content_screen:" not in source                       # no reopening evidence of that kind exists
    assert ":re_extracted:" in source and ":rebuilt_from_clean_edges:" in source
    assert "if extractor is None:" in source                       # without it edges stay held
    assert "async def mark_late_write" in source


def test_rebuild_never_widens_a_named_event_and_never_decides_from_one_source():
    source = (ROOT / "core/erasure_scope.py").read_text(encoding="utf-8")
    assert "or opens" not in source and "unknown_or_closed_event" in source   # no silent fallback to other events
    assert "live[0]" not in source                                            # every live source is consulted
    assert 'c["source"] == r["s"] and c["target"] == r["t"]' in source        # direction is part of the match


def test_only_an_exact_statement_confirms_the_stored_one():
    from core.erasure_scope import confirms
    ok = "Mira Venn is employed by the Harbor Office."
    assert confirms(ok, ok)
    assert not confirms(ok, "Mira Venn is not employed by the Harbor Office.")           # negation, both ways
    assert not confirms("Mira Venn is not employed by the Harbor Office.", ok)
    assert not confirms("The balance changed by -5 units.", "The balance changed by 5 units.")   # sign
    assert not confirms("The balance changed by \u22125 units.", "The balance changed by 5 units.")
    assert not confirms("The balance changed by -5 units.", "The balance changed by \u22125 units.")  # two minus signs
    assert not confirms("Mira Venn owns the Harbor Office.", "Mira Venn owns the harbor office.")      # case
    assert not confirms("Mira Venn owns the Harbor Office.", "Mira Venn owns the Harbor Office")       # punctuation
    assert not confirms("Mira Venn owns the Harbor Office.", " Mira Venn owns the Harbor Office.")     # whitespace
    assert not confirms(None, ok) and not confirms(ok, None)


def test_no_normalization_or_quantity_heuristic_decides_an_edge():
    source = (ROOT / "core/erasure_scope.py").read_text(encoding="utf-8")
    assert "_norm" not in source and "_quantities" not in source and "_MONTHS" not in source
    assert "held_reworded_edges" in source and "confirms(r[\"fact\"]" in source
    assert '"fact": r["fact"]' in source                      # the stored text is kept, never rewritten


# --- idempotent erasure, atomic closing, unsettled writes (static; live behaviour in tests/integration) ---

def test_erase_node_fixes_event_identity_only_on_create_and_never_resets_status():
    text = (ROOT / "core/erasure_guard.py").read_text(encoding="utf-8")
    start = text.index("_NEW_EVENT = ")
    end = text.index("async def erase_node")
    block = text[start:end]
    on_create = block[block.index("ON CREATE SET"):block.index("WITH n,gate,t,(t.event_id")]
    for field in ("t.event_id=$event_id", "t.revision=", "gate.revision=", "t.seed_entities="):
        assert field in on_create, field
    import re
    writes = re.compile(r"t\.(status|event_id|revision|seed_\w+|token_hashes)\s*=(?!=)")
    after_create = block[block.index("OPTIONAL MATCH (run:TaskRun)"):]
    assert not writes.search(after_create), "identity, seed and status are written ON CREATE only"
    known = text[text.index("_KNOWN_EVENT = "):end]
    assert not writes.search(known) and "gate.revision=" not in known    # a repeat never rewrites nor bumps


def test_event_close_and_late_write_registration_are_single_locked_statements():
    from core import erasure_scope as scope
    import inspect
    close = inspect.getsource(scope._close_event)
    assert close.count("execute_query") + close.count("_rows(") == 1 and "LOCK_ERASURE_CONTROL" in close
    assert "UNSETTLED_INGEST" in close and "UNSETTLED_CHAT" in close
    late = inspect.getsource(scope.mark_late_write)
    assert late.count("_rows(") == 1 and "LOCK_ERASURE_CONTROL" in late and "execute_query" not in late


def test_group_write_gate_reads_durable_attempt_records():
    source = inspect_source(guard.require_group_writable)
    assert "FractalIngestClaim" in source and "FractalChatWriteAttempt" in source and "ingest_unsettled" in source


def inspect_source(obj):
    import inspect
    return inspect.getsource(obj)


def test_readers_hold_derived_objects_of_a_group_with_an_unsettled_write():
    fragment = blocked_node("x")
    assert "FractalIngestClaim" in fragment and "FractalChatWriteAttempt" in fragment
    assert "fz_c" in fragment and "(t:FractalErasure" not in fragment


def test_settlement_is_never_called_by_runtime_code_paths():
    # lifting a pause is a person's decision: only tests and the settlement module reference it
    offenders = [str(p.relative_to(ROOT)) for d in ("core", "knowledge", "api", "mcp_server", "layers")
                 for p in (ROOT / d).rglob("*.py") if "settle_unsettled_write" in p.read_text(encoding="utf-8")
                 and p.name not in ("settlement.py", "late_writes.py", "erasure_guard.py", "erasure_dry_run.py")]
    offenders += [p for p in ("app.py", "simple_chat_agent.py") if "settle_unsettled_write" in (ROOT / p).read_text(encoding="utf-8")]
    assert offenders == []
