from queries.dedupe import fingerprint
from queries.dedupe_entities import group_entities


def test_entity_dedupe_does_not_cross_namespace_boundary():
    entities = [
        {
            "uuid": "personal-1",
            "name": "Graphiti",
            "normalized_name": "graphiti",
            "group_id": "personal",
        },
        {
            "uuid": "project-1",
            "name": "Graphiti",
            "normalized_name": "graphiti",
            "group_id": "project",
        },
    ]
    groups = group_entities(entities)
    assert len(groups) == 2
    assert len(groups[("graphiti", "personal")]) == 1
    assert len(groups[("graphiti", "project")]) == 1


def test_episode_fingerprint_normalizes_whitespace_and_case():
    assert fingerprint("  Hello   World \n") == fingerprint("hello world")
