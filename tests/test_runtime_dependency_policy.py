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


def test_reviewed_ci_constraints_pin_direct_runtime_dependencies():
    constraints = (ROOT / "constraints-ci.txt").read_text(encoding="utf-8")
    required_pins = (
        "graphiti_core==0.29.3",
        "python-dotenv==1.2.3",
        "neo4j==5.28.5",
        "pytest==9.1.1",
        "pytest-asyncio==1.4.0",
        "pydantic==2.13.5",
        "pydantic-settings==2.15.0",
        "fastapi==0.141.1",
        "uvicorn==0.52.4",
        "python-multipart==0.0.32",
        "openai==3.6.0",
        "httpx==0.28.1",
        "pathspec==1.1.1",
    )
    for pin in required_pins:
        assert pin in constraints


def test_automated_validation_installs_reviewed_constraints():
    workflow_paths = (
        ".github/workflows/ci.yml",
        ".github/workflows/neo4j-integration.yml",
        ".github/workflows/provider-e2e.yml",
        ".github/workflows/provenance-dry-run.yml",
    )
    for workflow_path in workflow_paths:
        workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
        assert "-c constraints-ci.txt -r requirements.txt" in workflow
        assert "python -m pip check" in workflow
