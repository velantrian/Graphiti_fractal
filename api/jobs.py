"""
Upload Job Management Module

Handles background job tracking for file uploads.
Extracted from api.py to fix circular imports.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from uuid import uuid4

from core.types import UploadJobStatus, TimingProfile, JobTiming, JobStage

logger = logging.getLogger(__name__)

# Global job storage
_upload_jobs: Dict[str, UploadJobStatus] = {}


def create_upload_job() -> str:
    """
    Create a new upload job with initial status.
    
    Returns:
        Job ID string
    """
    job_id = str(uuid4())
    now = datetime.now(timezone.utc)
    
    _upload_jobs[job_id] = {
        "stage": JobStage.PENDING.value,
        "total_chunks": None,
        "processed_chunks": 0,
        "started_at": now.isoformat(),
        "error": None,
        "warnings": [],
        "profile": {
            "chunking_time": 0,
            "embedding_calls": 0,
            "embedding_time": 0,
            "graph_time": 0,
            "total_time": 0
        },
        "timing": {
            "job_created_at": now,
            "upload_request_started_at": None,
            "ingest_started_at": None,
            "ingest_finished_at": None,
            "per_chunk": []
        }
    }
    
    logger.debug(f"Created upload job {job_id}")
    return job_id


def update_upload_job(job_id: str, **fields) -> bool:
    """
    Update job status fields.
    
    Args:
        job_id: Job identifier
        **fields: Fields to update
        
    Returns:
        True if job was found and updated
    """
    if job_id not in _upload_jobs:
        logger.warning(f"Attempted to update non-existent job {job_id}")
        return False
    
    _upload_jobs[job_id].update(fields)
    logger.debug(f"Updated job {job_id}: {list(fields.keys())}")
    return True


def finish_upload_job(job_id: str, result: dict, **fields) -> bool:
    """Publish explicit completeness and counters, not inferred HTTP success."""
    completeness = result.get("completeness")
    if completeness not in {"complete", "partial", "failed", "unconfirmed"}:
        completeness = "unknown"
    warnings = result.get("warnings", [])
    stage = ("error" if completeness == "failed" or result.get("status") == "error"
             else "done" if completeness == "complete" and not warnings
             else "done_with_warnings")
    counters = {key: result[key] for key in (
        "chunks", "added", "skipped_existing", "skipped_claim_held",
        "unconfirmed_existing", "unconfirmed_writes", "failed_chunks", "degraded_chunks"
    ) if key in result}
    if "chunks" in result:
        fields.update(total_chunks=result["chunks"], processed_chunks=result["chunks"])
    return update_upload_job(job_id, stage=stage, completeness=completeness,
                             counters=counters, warnings=warnings, **fields)


def get_upload_job(job_id: str) -> Optional[UploadJobStatus]:
    """
    Get job status by ID.
    
    Args:
        job_id: Job identifier
        
    Returns:
        Job status dict or None if not found
    """
    return _upload_jobs.get(job_id)


def delete_upload_job(job_id: str) -> bool:
    """
    Delete a job from storage.
    
    Args:
        job_id: Job identifier
        
    Returns:
        True if job was found and deleted
    """
    if job_id in _upload_jobs:
        del _upload_jobs[job_id]
        logger.debug(f"Deleted job {job_id}")
        return True
    return False


def cleanup_old_jobs(max_age_hours: int = 24) -> int:
    """
    Remove jobs older than max_age_hours.
    
    Args:
        max_age_hours: Maximum age in hours
        
    Returns:
        Number of jobs removed
    """
    now = datetime.now(timezone.utc)
    to_remove = []
    
    for job_id, job in _upload_jobs.items():
        started_at = job.get("started_at")
        if started_at:
            try:
                job_time = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                age_hours = (now - job_time).total_seconds() / 3600
                if age_hours > max_age_hours:
                    to_remove.append(job_id)
            except (ValueError, TypeError):
                pass
    
    for job_id in to_remove:
        del _upload_jobs[job_id]
    
    if to_remove:
        logger.info(f"Cleaned up {len(to_remove)} old jobs")
    
    return len(to_remove)


def get_all_jobs() -> Dict[str, UploadJobStatus]:
    """
    Get all jobs (for debugging/admin).
    
    Returns:
        Dict of all jobs
    """
    return _upload_jobs.copy()


def get_job_count() -> int:
    """
    Get total number of tracked jobs.
    
    Returns:
        Job count
    """
    return len(_upload_jobs)
