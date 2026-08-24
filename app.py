import asyncio
import hmac
import io
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.jobs import cleanup_old_jobs, create_upload_job, get_upload_job, update_upload_job
from core.bootstrap import ensure_graphiti_ready
from core.conversation_buffer import clear_user_buffer
from core.graphiti_client import get_graphiti_client
from core.identity import ensure_user_identity_entity
from core.memory_ops import clear_recent_memories
from experience import ExperienceIngestRequest
from experience.retrieval import get_antipatterns, get_success_patterns
from experience.writer import ingest_experience
from knowledge.ingest import ingest_text_document, resolve_group_id
from knowledge.retrieval import search_knowledge

if TYPE_CHECKING:
    from graphiti_core import Graphiti

logger = logging.getLogger(__name__)
background_tasks: set[asyncio.Task] = set()
_init_lock = asyncio.Lock()
_initialized = False


def _api_token() -> str:
    return (os.getenv("FRACTAL_API_TOKEN") or "").strip()


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Fail closed for every data-bearing API route."""
    token = _api_token()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="FRACTAL_API_TOKEN is not configured; protected API is disabled",
        )
    expected = f"Bearer {token}"
    if not hmac.compare_digest(authorization or "", expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


async def get_graphiti_dep() -> "Graphiti":
    return await get_graphiti_client().ensure_ready()


def register_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


async def run_ingest_job(
    job_id: str,
    content: str,
    source_description: str | None,
    memory_type: str = "knowledge",
    user_id: str = "sergey",
) -> None:
    try:
        graphiti = await get_graphiti_client().ensure_ready()
        ingest_started_at = datetime.now(timezone.utc)
        job = get_upload_job(job_id)
        if job and "timing" in job:
            job["timing"]["ingest_started_at"] = ingest_started_at

        update_upload_job(job_id, stage="ingest")
        result = await ingest_text_document(
            graphiti,
            content,
            source_description=source_description,
            user_id=user_id,
            group_id=resolve_group_id(memory_type),
            job_id=job_id,
        )

        ingest_finished_at = datetime.now(timezone.utc)
        job = get_upload_job(job_id)
        if job and "timing" in job:
            job["timing"]["ingest_finished_at"] = ingest_finished_at

        warnings = result.get("warnings", [])
        update_upload_job(
            job_id,
            stage="done_with_warnings" if warnings else "done",
            warnings=warnings,
        )
    except Exception as exc:  # noqa: BLE001
        error_msg = f"Job failed: {type(exc).__name__}: {exc}"
        update_upload_job(job_id, stage="error", error=error_msg)
        logger.exception("Upload job %s failed", job_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_old_jobs()
    yield
    if not background_tasks:
        return
    done, pending = await asyncio.wait(background_tasks, timeout=30)
    if pending:
        logger.warning("Cancelling %d pending app background tasks", len(pending))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


app = FastAPI(
    title="Fractal Memory API",
    description="Graphiti-backed memory, retrieval, chat, and experience API.",
    version="2.1.0",
    lifespan=lifespan,
)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    user_id: str = Field(..., min_length=1, description="User identifier")


class ChatResponse(BaseModel):
    reply: str
    duration_ms: float | None = None
    timing: dict | None = None


class BufferClearRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


class RememberRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source_description: str | None = None
    memory_type: str | None = None
    user_id: str = Field(..., min_length=1)


class DeleteRequest(BaseModel):
    uuid: str = Field(..., min_length=1)
    hard: bool = False


async def ensure_agent_ready() -> None:
    global _initialized
    if _initialized:
        return
    async with _init_lock:
        if _initialized:
            return
        await ensure_graphiti_ready()
        bootstrap_user_id = (os.getenv("FRACTAL_BOOTSTRAP_USER_ID") or "sergey").strip()
        if bootstrap_user_id:
            try:
                await ensure_user_identity_entity(bootstrap_user_id)
            except Exception:  # noqa: BLE001
                logger.exception("Identity bootstrap failed for %s", bootstrap_user_id)
        _initialized = True


@app.get("/")
async def root():
    index = static_dir / "index.html"
    return FileResponse(index) if index.exists() else RedirectResponse(url="/docs")


@app.get("/health", tags=["System"])
async def health_check():
    try:
        graphiti = await get_graphiti_dep()
        result = await graphiti.driver.execute_query("RETURN 'health_check' AS status LIMIT 1")
        healthy = bool(result.records and result.records[0]["status"] == "health_check")
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
    request_id = str(uuid4())[:8]
    t0 = perf_counter()
    try:
        graphiti = await get_graphiti_dep()
        from core.llm import get_async_client
        from core.memory_ops import MemoryOps
        from simple_chat_agent import SimpleChatAgent

        llm_client = get_async_client()
        if not llm_client:
            raise HTTPException(status_code=503, detail="LLM client unavailable")

        agent = SimpleChatAgent(llm_client, MemoryOps(graphiti, req.user_id))
        t1 = perf_counter()
        reply, _, _ = await agent.answer_core(req.message)
        answer_ms = (perf_counter() - t1) * 1000
        total_ms = (perf_counter() - t0) * 1000
        logger.info(
            "Chat completed",
            extra={"request_id": request_id, "user_id": req.user_id, "duration_ms": total_ms},
        )
        return ChatResponse(
            reply=reply,
            duration_ms=total_ms,
            timing={"answer_ms": answer_ms, "total_ms": total_ms},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chat failed", extra={"request_id": request_id, "user_id": req.user_id})
        raise HTTPException(status_code=502, detail="chat processing failed") from exc


@app.post("/remember", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def remember(req: RememberRequest):
    from core.memory_ops import MemoryOps

    graphiti = await get_graphiti_dep()
    try:
        return await MemoryOps(graphiti, req.user_id).remember_text(
            req.text,
            memory_type=req.memory_type,
            source_description=req.source_description or "user_chat",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/upload", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def upload_file(
    file: UploadFile = File(...),
    source_description: str = Form("uploaded_file"),
    memory_type: str = Form("knowledge"),
    user_id: str = Form(...),
):
    if memory_type not in {"personal", "project", "knowledge", "experience"}:
        raise HTTPException(status_code=400, detail="invalid memory_type")

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

    register_background_task(
        run_ingest_job(job_id, content, source_description, memory_type, user_id)
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
        return {"status": "ok" if result.records else "not_found", "deleted": bool(result.records), "mode": "hard"}

    result = await graphiti.driver.execute_query(
        """
        MATCH (n {uuid:$uuid})
        SET n.deleted=true, n.deleted_at=$ts
        RETURN 1 AS done
        """,
        uuid=req.uuid,
        ts=datetime.now(timezone.utc).isoformat(),
    )
    return {"status": "ok" if result.records else "not_found", "deleted": bool(result.records), "mode": "soft"}


@app.post("/buffer/clear", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def clear_buffer(req: BufferClearRequest):
    return {
        "status": "ok",
        "user_id": req.user_id,
        "cleared": {
            "conversation_buffer": clear_user_buffer(req.user_id),
            "recent_memories": clear_recent_memories(req.user_id),
        },
    }


@app.post("/clear_memory", tags=["Memory"], dependencies=[Depends(require_api_token)])
async def clear_memory(x_fractal_confirm: str | None = Header(default=None)):
    if os.getenv("FRACTAL_ALLOW_CLEAR_ALL") != "1":
        raise HTTPException(status_code=403, detail="clear-all is disabled")
    if not hmac.compare_digest(x_fractal_confirm or "", "CLEAR_ALL_MEMORY"):
        raise HTTPException(status_code=400, detail="missing destructive confirmation header")

    graphiti = await get_graphiti_dep()
    result = await graphiti.driver.execute_query(
        "MATCH (n) WITH count(n) AS total MATCH (m) DETACH DELETE m RETURN total AS deleted_count"
    )
    deleted = result.records[0]["deleted_count"] if result.records else 0
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
    limit = max(1, min(50, limit))
    return {
        "items": await get_success_patterns(
            await get_graphiti_dep(),
            task_type=task_type,
            context_hash=context_hash,
            limit=limit,
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
    limit = max(1, min(50, limit))
    return {
        "items": await get_antipatterns(
            await get_graphiti_dep(),
            task_type=task_type,
            context_hash=context_hash,
            limit=limit,
        )
    }


@app.get(
    "/diagnostics/memory-conflicts",
    tags=["Diagnostics"],
    dependencies=[Depends(require_api_token)],
)
async def diagnose_memory_conflicts(entity_name: str, limit: int = 20):
    limit = max(1, min(50, limit))
    graphiti = await get_graphiti_dep()
    entities_result = await graphiti.driver.execute_query(
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
        limit=limit,
    )

    return {
        "entity_name": entity_name,
        "entities_found": [
            {
                "name": record["entity_name"],
                "summary": record["entity_summary"],
                "episodes": record["episodes"],
            }
            for record in entities_result.records
        ],
    }
