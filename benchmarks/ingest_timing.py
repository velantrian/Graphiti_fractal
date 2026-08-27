#!/usr/bin/env python3
import asyncio
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api import create_upload_job, run_ingest_job

async def test_timing():
    print('=== Testing Detailed Timing System ===')

    # Create job
    job_id = create_upload_job()
    print(f'Created job: {job_id}')

    # Simulate upload request start (in real scenario this is set in /upload endpoint)
    from datetime import datetime, timezone
    from api import UPLOAD_JOBS
    UPLOAD_JOBS[job_id]["timing"]["upload_request_started_at"] = datetime.now(timezone.utc)

    # Read test file (find the unique one)
    import glob
    test_files = glob.glob('unique_timing_test_*.txt')
    if test_files:
        test_file = test_files[0]
    else:
        test_file = 'timing_test.txt'

    with open(test_file, 'r', encoding='utf-8') as f:
        content = f.read()

    print(f'Content length: {len(content)} characters')

    # Run processing
    try:
        await run_ingest_job(job_id, content, 'timing_test_final')
        print('✅ Processing completed successfully!')
    except Exception as e:
        print(f'❌ Processing failed: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_timing())