from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_graphiti_runtime_is_pinned_to_current_reviewed_release():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "graphiti_core==0.29.3" in requirements


def test_neo4j_docker_is_pinned_to_reviewed_526_lts_patch():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: neo4j:5.26.29-community" in compose
    assert "image: neo4j:5.26-community" not in compose


def test_research_technologies_are_not_silently_runtime_dependencies():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").lower()
    active = requirements + "\n" + compose

    research_dependencies = (
        "psycopg",
        "pgvector",
        "graphrag",
        "openspg",
        "dowhy",
        "causal-learn",
        "causal_learn",
        "graphdatascience",
        "kuzu",
        "ladybugdb",
        "ladybug-db",
    )
    for research_dependency in research_dependencies:
        assert research_dependency not in active
