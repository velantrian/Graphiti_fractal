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
