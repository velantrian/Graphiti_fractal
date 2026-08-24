import hmac
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.jobs import cleanup_old_jobs, create_upload_job, get_upload_job, update_upload_job
from core.conversation_buffer import clear_user_buffer
from core.graphiti_client import get_graphiti_client
from core.instance import get_instance_user_id, require_instance_user_id
from core.memory_ops import clear_recent_memories
from core.task_registry import drain, spawn
from experience import ExperienceIngestRequest
from experience.retrieval import get_antipatterns, get_success_patterns
from experience.writer import ingest_experience
from knowledge.ingest import ingest_text_document, resolve_group_id
from knowledge.retrieval import search_knowledge

if TYPE_CHECKING:
    from graphiti_core import Graphiti

logger = logging.getLogger(__name__)
static_dir = Path(__file__).parent / "static"


def _api_token() -> str:
    return (os.getenv("FRACTAL_API_TOKEN") or "").strip()


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Fail closed for every data-bearing route."""
    configured = _api_token()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="FRACTAL_API_TOKEN is not configured; protected API is disabled",
        )
    if not hmac.compare_digest(authorization or "", f"Bearer {configured}"):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def _owner(requested_user_id: str) -> str:
    try:
        return require_instance_user_id(requested_user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def get_graphiti_dep() -> "Graphiti":
    return await get_graphiti_client().ensure_ready()


async def run_ingest_job(
    job_id: str,
    content: str,
    source_description: str | None,
    memory_type: str,
    user_id: str,
) -> None:
    try:
        graphiti = await get_graphiti_client().ensure_ready()
        job = get_upload_job(job_id)
        if job and "timing" in job:
            job["timing"]["ingest_started_at"] = datetime.now(timezone.utc)

        result = await ingest_text_document(
            graphiti,
            content,
            source_description=source_description or "uploaded_file",
            user_id=user_id,
            group_id=resolve_group_id(memory_type),
            job_id=job_id,
        )

        job = get_upload_job(job_id)
        if job and "timing" in job:
            job["timing"]["ingest_finished_at"] = datetime.now(timezone.utc)

        warnings = result.get("warnings", [])
        if result.get("status") == "error":
            update_upload_job(job_id, stage="error", warnings=warnings)
        else:
            update_upload_job(
                job_id,
                stage="done_with_warnings" if warnings else "done",
                warnings=warnings,
            )
    except Exception as exc:  # noqa: BLE001
        update_upload_job(
            job_id,
            stage="error",
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.exception("Upload job %s failed", job_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_old_jobs()
    yield
    done, cancelled = await drain(timeout=30.0)
    if done or cancelled:
        logger.info(
            "Background task drain complete",
            extra={"done": done, "cancelled": cancelled},
        )


app = FastAPI(
    title="Fractal Memory API",
    description="Local single-tenant Graphiti-backed memory service.",
    version="2.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    reply: str
    duration_ms: float | None = None
    timing: dict | None = None


class BufferClearRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


class RememberRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source_description: str | None = None
    memory_type: Literal["personal", "project", "knowledge", "experience"] | None = None
    user_id: str = Field(..., min_length=1)


class DeleteRequest(BaseModel):
    uuid: str = Field(..., min_length=1)
    hard: bool = False


@app.get("/")
async def root():
    index = static_dir / "index.html"
    return FileResponse(index) if index.exists() else RedirectResponse(url="/docs")


@app.get("/visualization/visualization.html", include_in_schema=False)
async def visualization_view():
    path = static_dir / "visualization.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="visualization view not found")
    return FileResponse(path)


@app.get("/visualization/graph_data.json", include_in_schema=False)
async def visualization_data():
    path = static_dir / "graph_data.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="graph_data.json not generated; run python main.py viz-export",
        )
    return FileResponse(path)


@app.get("/health", tags=["System"])
async def health_check():
    try:
        graphiti = await get_graphiti_dep()
        result = await graphiti.driver.execute_query("RETURN 1 AS ok")
        healthy = bool(result.records and result.records[0]["ok"] == 1)
        return {
            "status": "healthy" if healthy else "unhealthy",
            "neo4j": "connected" if healthy else "query_failed",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health check failed: %s", type(exc).__name__)
        return {"status": "unhealthy", "neo4j": "disconnected"}


@app.post("/transcribe", tags=["Chat"], dependencies=[Depends(require_api_token)])
async def transcribe_audio(file: UploadFile = File(...)):
    from core.llm import get_async_client

    client = get_async_client()
    if not client:
        raise HTTPException(status_code=503, detail="LLM client unavailable")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio file")
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="audio file too large")

    audio = io.BytesIO(raw)
    audio.name = file.filename or "voice.webm"
    model = (os.getenv("WHISPER_MODEL") or os.getenv("TRANSCRIBE_MODEL") or "whisper-1").strip()
    try:
        response = await client.audio.transcriptions.create(
            model=model,
            file=audio,
            language="ru",
        )
        return {"text": getattr(response, "text", None) or ""}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transcription failed")
        raise HTTPException(status_code=502, detail="transcription provider failed") from exc


@app.post(
    "/chat",
    response_model=ChatResponse,
    tags=["Chat"],
    dependencies=[Depends(require_api_token)],
)
async def chat(req: ChatRequest):
    user_id = _owner(req.user_id)
    request_id = str(uuid4())[:8]
    started = perf_counter()

    graphiti = await get_graphiti_dep()
    from core.llm import get_async_client
    from core.memory_ops import MemoryOps
    from simple_chat_agent import SimpleChatAgent

    llm_client = get_async_client()
    if not llm_client:
        raise HTTPException(status_code=503, detail="LLM client unavailable")

    agent = SimpleChatAgent(llm_client, MemoryOps(graphiti, user_id))
    answer_started = perf_counter()
    reply, _, _ = await agent.answer_core(req.message)
    answer_ms = (perf_counter() - answer_started) * 1000
    total_ms = (perf_counter() - started) * 1000

    logger.info(
        "Chat completed",
        extra={"request_id": request_id, "user_id": user_id, "duration_ms": total_ms},
    )
    return ChatResponse(
        reply=reply,
        duration_ms=total_ms,
        timing={"answer_ms": answer_ms, "total_ms": total_ms},
    )


@app.post("/remember", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def remember(req: RememberRequest):
    from core.memory_ops import MemoryOps

    user_id = _owner(req.user_id)
    graphiti = await get_graphiti_dep()
    return await MemoryOps(graphiti, user_id).remember_text(
        req.text,
        memory_type=req.memory_type,
        source_description=req.source_description or "user_chat",
    )


@app.post("/upload", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def upload_file(
    file: UploadFile = File(...),
    source_description: str = Form("uploaded_file"),
    memory_type: Literal["personal", "project", "knowledge", "experience"] = Form("knowledge"),
    user_id: str = Form(...),
):
    user_id = _owner(user_id)
    raw = await file.read()
    max_upload_bytes = int(os.getenv("FRACTAL_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > max_upload_bytes:
        raise HTTPException(status_code=413, detail="file too large")

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1251", errors="replace")

    cleanup_old_jobs()
    job_id = create_upload_job()
    job = get_upload_job(job_id)
    if job and "timing" in job:
        job["timing"]["upload_request_started_at"] = datetime.now(timezone.utc)

    spawn(
        run_ingest_job(job_id, content, source_description, memory_type, user_id),
        name=f"upload:{job_id}",
    )
    return {"job_id": job_id}


@app.get(
    "/upload/status/{job_id}",
    tags=["Memory"],
    dependencies=[Depends(require_api_token)],
)
async def upload_status(job_id: str):
    status = get_upload_job(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status


@app.post("/delete", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def delete_node(req: DeleteRequest):
    if req.hard and os.getenv("FRACTAL_ALLOW_HARD_DELETE") != "1":
        raise HTTPException(status_code=403, detail="hard delete is disabled")

    graphiti = await get_graphiti_dep()
    if req.hard:
        result = await graphiti.driver.execute_query(
            "MATCH (n {uuid:$uuid}) DETACH DELETE n RETURN 1 AS done",
            uuid=req.uuid,
        )
        return {
            "status": "ok" if result.records else "not_found",
            "deleted": bool(result.records),
            "mode": "hard",
        }

    result = await graphiti.driver.execute_query(
        """
        MATCH (n {uuid:$uuid})
        SET n.deleted=true, n.deleted_at=$ts
        RETURN 1 AS done
        """,
        uuid=req.uuid,
        ts=datetime.now(timezone.utc).isoformat(),
    )
    return {
        "status": "ok" if result.records else "not_found",
        "deleted": bool(result.records),
        "mode": "soft",
    }


@app.post("/buffer/clear", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def clear_buffer(req: BufferClearRequest):
    user_id = _owner(req.user_id)
    return {
        "status": "ok",
        "user_id": user_id,
        "cleared": {
            "conversation_buffer": clear_user_buffer(user_id),
            "recent_memories": clear_recent_memories(user_id),
        },
    }


@app.post("/clear_memory", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def clear_memory(x_fractal_confirm: str | None = Header(default=None)):
    if os.getenv("FRACTAL_ALLOW_CLEAR_ALL") != "1":
        raise HTTPException(status_code=403, detail="clear-all is disabled")
    if not hmac.compare_digest(x_fractal_confirm or "", "CLEAR_ALL_MEMORY"):
        raise HTTPException(status_code=400, detail="missing destructive confirmation header")

    graphiti = await get_graphiti_dep()
    count_result = await graphiti.driver.execute_query("MATCH (n) RETURN count(n) AS total")
    deleted = int(count_result.records[0]["total"]) if count_result.records else 0
    await graphiti.driver.execute_query("MATCH (n) DETACH DELETE n")
    return {"status": "ok", "deleted_nodes": deleted}


@app.get(
    "/knowledge/search",
    tags=["Knowledge"],
    dependencies=[Depends(require_api_token)],
)
async def knowledge_search(
    q: str,
    limit: int = 10,
    group_id: str | None = None,
    graphiti: "Graphiti" = Depends(get_graphiti_dep),
):
    limit = max(1, min(50, limit))
    return {"items": await search_knowledge(graphiti, q, limit=limit, group_id=group_id)}


@app.post(
    "/experience/ingest",
    tags=["Experience"],
    dependencies=[Depends(require_api_token)],
)
async def experience_ingest(req: ExperienceIngestRequest):
    return await ingest_experience(await get_graphiti_dep(), req)


@app.get(
    "/experience/success",
    tags=["Experience"],
    dependencies=[Depends(require_api_token)],
)
async def experience_success(
    task_type: str | None = None,
    context_hash: str | None = None,
    limit: int = 5,
):
    return {
        "items": await get_success_patterns(
            await get_graphiti_dep(),
            task_type=task_type,
            context_hash=context_hash,
            limit=max(1, min(50, limit)),
        )
    }


@app.get(
    "/experience/antipatterns",
    tags=["Experience"],
    dependencies=[Depends(require_api_token)],
)
async def experience_antipatterns(
    task_type: str | None = None,
    context_hash: str | None = None,
    limit: int = 5,
):
    return {
        "items": await get_antipatterns(
            await get_graphiti_dep(),
            task_type=task_type,
            context_hash=context_hash,
            limit=max(1, min(50, limit)),
        )
    }


@app.get(
    "/diagnostics/memory-conflicts",
    tags=["Diagnostics"],
    dependencies=[Depends(require_api_token)],
)
async def diagnose_memory_conflicts(entity_name: str, limit: int = 20):
    graphiti = await get_graphiti_dep()
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Entity)
        WHERE toLower(e.name) CONTAINS toLower($entity_name)
          AND coalesce(e.deleted, false) = false
        OPTIONAL MATCH (e)<-[:MENTIONS]-(ep:Episodic)
        WHERE coalesce(ep.deleted, false) = false
        RETURN e.name AS entity_name,
               e.summary AS entity_summary,
               collect({
                   uuid: ep.uuid,
                   content: substring(coalesce(ep.content, ep.episode_body, ''), 0, 200),
                   created_at: toString(ep.created_at),
                   group_id: ep.group_id,
                   source_description: ep.source_description
               }) AS episodes
        ORDER BY size(episodes) DESC
        LIMIT $limit
        """,
        entity_name=entity_name,
        limit=max(1, min(50, limit)),
    )
    return {
        "entity_name": entity_name,
        "owner": get_instance_user_id(),
        "entities_found": [
            {
                "name": record["entity_name"],
                "summary": record["entity_summary"],
                "episodes": record["episodes"],
            }
            for record in result.records
        ],
    }
