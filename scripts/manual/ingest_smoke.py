import asyncio
import sys
sys.path.append('.')
from core.graphiti_client import get_graphiti_client
from knowledge.ingest import ingest_text_document
from api import create_upload_job, run_ingest_job, get_upload_job

async def test_ingest():
    graphiti_client = get_graphiti_client()
    graphiti = await graphiti_client.ensure_ready()

    # Test direct ingest
    result = await ingest_text_document(
        graphiti,
        "Тестовый документ для проверки ingest",
        source_description="test",
        user_id="sergey",
        group_id="personal"
    )

    print("Direct ingest result:", result)

    # Test run_ingest_job
    job_id = create_upload_job()
    print(f"Created job: {job_id}")

    await run_ingest_job(job_id, "Тестовый документ через job", "test_job", "project")

    job_status = get_upload_job(job_id)
    print("Job status:", job_status)

if __name__ == "__main__":
    asyncio.run(test_ingest())