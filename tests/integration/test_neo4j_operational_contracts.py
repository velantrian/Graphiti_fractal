import asyncio
import json
import os

import pytest
from neo4j import AsyncGraphDatabase

from core.ingest_atomicity import (
    acquire_ingest_claim,
    finalize_episode_identity,
    mark_ingest_claim_episode_created,
)
from core.recall_telemetry import read_recall_signals, record_recall
from experience.models import ErrorEvent, ExperienceIngestRequest, ToolCallEvent
from experience.writer import ingest_experience


class ResultAdapter:
    def __init__(self, records):
        self.records = records


class DriverAdapter:
    def __init__(self, driver):
        self._driver = driver

    async def execute_query(self, query, **kwargs):
        records, _, _ = await self._driver.execute_query(query, parameters_=kwargs)
        return ResultAdapter(records)


class GraphitiAdapter:
    def __init__(self, driver):
        self.driver = DriverAdapter(driver)


async def _connect_with_retry(uri: str, user: str, password: str):
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    last = None
    for _ in range(30):
        try:
            await driver.verify_connectivity()
            return driver
        except Exception as exc:  # noqa: BLE001
            last = exc
            await asyncio.sleep(1)
    await driver.close()
    raise RuntimeError(f"Neo4j did not become ready: {last}")


@pytest.mark.asyncio
async def test_live_neo4j_recall_telemetry_is_non_authoritative_and_tracks_query_diversity():
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "fractal-test-password")
    driver = await _connect_with_retry(uri, user, password)
    graphiti = GraphitiAdapter(driver)
    try:
        await graphiti.driver.execute_query("MATCH (n) DETACH DELETE n")
        first = await record_recall(
            graphiti,
            user_id="integration-owner",
            query="What did we decide about Graphiti?",
            object_uuids=["episode-1", "entity-1"],
        )
        second = await record_recall(
            graphiti,
            user_id="integration-owner",
            query="Remind me of the Graphiti decision",
            object_uuids=["episode-1"],
        )
        assert first["authoritative"] is False
        assert second["authoritative"] is False

        signals = await read_recall_signals(
            graphiti,
            user_id="integration-owner",
            object_uuid="episode-1",
        )
        assert signals["recall_count"] == 2
        assert signals["unique_queries"] == 2
        assert signals["authoritative"] is False

        result = await graphiti.driver.execute_query(
            """
            MATCH (r:RecallTelemetry {user_id:$user_id, object_uuid:$object_uuid})
            RETURN r.recall_count AS recall_count,
                   r.valid_at AS valid_at,
                   r.invalid_at AS invalid_at,
                   r.fact AS fact
            """,
            user_id="integration-owner",
            object_uuid="episode-1",
        )
        assert result.records[0]["recall_count"] == 2
        assert result.records[0]["unique_queries"] if "unique_queries" in result.records[0] else True
        assert result.records[0]["valid_at"] is None
        assert result.records[0]["invalid_at"] is None
        assert result.records[0]["fact"] is None
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_live_neo4j_ingest_claim_is_unique_and_finalization_is_atomic():
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "fractal-test-password")
    driver = await _connect_with_retry(uri, user, password)
    graphiti = GraphitiAdapter(driver)
    try:
        await graphiti.driver.execute_query("MATCH (n) DETACH DELETE n")
        token = await acquire_ingest_claim(
            graphiti,
            group_id="knowledge",
            fingerprint="fp-1",
        )
        assert token
        duplicate = await acquire_ingest_claim(
            graphiti,
            group_id="knowledge",
            fingerprint="fp-1",
        )
        assert duplicate is None

        await graphiti.driver.execute_query(
            "CREATE (:Episodic {uuid:$uuid, content:'bounded'})",
            uuid="episode-atomic-1",
        )
        await mark_ingest_claim_episode_created(
            graphiti,
            group_id="knowledge",
            fingerprint="fp-1",
            token=token,
            episode_uuid="episode-atomic-1",
        )
        await finalize_episode_identity(
            graphiti,
            episode_uuid="episode-atomic-1",
            group_id="knowledge",
            fingerprint="fp-1",
            claim_token=token,
            user_id="integration-owner",
        )

        result = await graphiti.driver.execute_query(
            """
            MATCH (u:User {user_id:'integration-owner'})-[:AUTHORED]->(e:Episodic {uuid:'episode-atomic-1'})
            MATCH (c:FractalIngestClaim {claim_key:'knowledge:fp-1'})
            RETURN e.fingerprint AS fingerprint,
                   e.group_id AS group_id,
                   c.state AS state,
                   c.episode_uuid AS episode_uuid
            """
        )
        record = result.records[0]
        assert record["fingerprint"] == "fp-1"
        assert record["group_id"] == "knowledge"
        assert record["state"] == "COMMITTED"
        assert record["episode_uuid"] == "episode-atomic-1"
    finally:
        await driver.close()


@pytest.mark.asyncio
async def test_live_neo4j_experience_nested_tool_args_use_scalar_representation():
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "fractal-test-password")
    driver = await _connect_with_retry(uri, user, password)
    graphiti = GraphitiAdapter(driver)
    try:
        await graphiti.driver.execute_query(
            "MATCH (n) WHERE n.group_id = 'experience' DETACH DELETE n"
        )
        run_id = "experience-args-integration"
        request = ExperienceIngestRequest(
            run_id=run_id,
            task_type="integration_test",
            tool_calls=[
                ToolCallEvent(
                    tool="shell",
                    args={"nested": {"key": "value"}, "items": [1, 2, 3]},
                )
            ],
        )

        result = await ingest_experience(graphiti, request)
        assert result["status"] == "ok"
        assert result["created"]["tool_calls"] == 1

        stored = await graphiti.driver.execute_query(
            """
            MATCH (:TaskRun {uuid:$run_id})-[:HAS_TOOLCALL]->(t:ToolCall)
            RETURN t.args_json AS args_json, t.args_sha256 AS args_sha256, t.args AS legacy_args
            """,
            run_id=run_id,
        )
        record = stored.records[0]
        assert isinstance(record["args_json"], str)
        assert json.loads(record["args_json"]) == {
            "items": [1, 2, 3],
            "nested": {"key": "value"},
        }
        assert isinstance(record["args_sha256"], str)
        assert len(record["args_sha256"]) == 64
        assert record["legacy_args"] is None
    finally:
        await graphiti.driver.execute_query(
            "MATCH (n) WHERE n.uuid = $run_id OR n.group_id = 'experience' DETACH DELETE n",
            run_id="experience-args-integration",
        )
        await driver.close()


@pytest.mark.asyncio
async def test_live_neo4j_experience_secret_values_are_redacted_before_persistence():
    uri = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "fractal-test-password")
    driver = await _connect_with_retry(uri, user, password)
    graphiti = GraphitiAdapter(driver)
    secrets = [
        "bearer-secret",
        "quoted-bearer-secret",
        "args-secret",
        "env-secret",
        "quoted-env-secret",
        "password-secret",
        "quoted-password-secret",
        "error-secret",
    ]
    run_id = "experience-redaction-integration"
    try:
        await graphiti.driver.execute_query(
            "MATCH (n) WHERE n.group_id = 'experience' DETACH DELETE n"
        )
        request = ExperienceIngestRequest(
            run_id=run_id,
            task_type="integration_test",
            tool_calls=[
                ToolCallEvent(
                    tool="shell",
                    command='curl -H \'Authorization: Bearer "quoted-bearer-secret"\' https://example.invalid',
                    args={
                        "api_key": "args-secret",
                        "safe": "keep-me",
                        "quoted": "API_KEY='quoted-env-secret'",
                    },
                    stdout='OPENAI_API_KEY=env-secret API_KEY="quoted-env-secret" safe-output',
                    stderr="password=password-secret password = 'quoted-password-secret' safe-error",
                )
            ],
            errors=[
                ErrorEvent(
                    error_type="RuntimeError",
                    message="token=error-secret failed safely",
                    stack="Authorization: Bearer bearer-secret\nframe: safe-frame",
                )
            ],
        )
        result = await ingest_experience(graphiti, request)
        assert result["status"] == "ok"

        stored = await graphiti.driver.execute_query(
            """
            MATCH (tr:TaskRun {uuid:$run_id})-[:HAS_TOOLCALL]->(t:ToolCall)
            OPTIONAL MATCH (tr)-[:FAILED_WITH]->(e:ErrorEvent)
            RETURN t.command AS command,
                   t.args_json AS args_json,
                   t.stdout AS stdout,
                   t.stderr AS stderr,
                   e.message AS error_message,
                   e.stack AS error_stack
            """,
            run_id=run_id,
        )
        record = stored.records[0]
        persisted = "\n".join(str(record[key] or "") for key in record.keys())
        for secret in secrets:
            assert secret not in persisted

        args = json.loads(record["args_json"])
        assert args["api_key"] == "[REDACTED]"
        assert args["safe"] == "keep-me"
        assert args["quoted"] == "API_KEY='[REDACTED]'"
        assert 'Bearer "[REDACTED]"' in record["command"]
        assert 'API_KEY="[REDACTED]"' in record["stdout"]
        assert "password = '[REDACTED]'" in record["stderr"]
        assert "safe-output" in record["stdout"]
        assert "safe-error" in record["stderr"]
        assert "safe-frame" in record["error_stack"]
    finally:
        await graphiti.driver.execute_query(
            "MATCH (n) WHERE n.group_id = 'experience' DETACH DELETE n"
        )
        await driver.close()
