from core.provenance import build_provenance_record, provenance_id


def test_provenance_id_is_deterministic_and_order_independent():
    a = provenance_id(kind="summary", source_ids=["e2", "e1"], activity="summarize")
    b = provenance_id(kind="summary", source_ids=["e1", "e2"], activity="summarize")
    assert a == b
    assert a.startswith("prov:summary:")


def test_provenance_record_keeps_sources_and_is_non_authoritative():
    record = build_provenance_record(
        kind="l3_profile",
        source_ids=["community-1", "community-2"],
        activity="l3_synthesis",
        agent="fractal",
        payload="derived text",
    )
    assert record["source_ids"] == ["community-1", "community-2"]
    assert record["payload_sha256"]
    assert record["authoritative_fact"] is False
