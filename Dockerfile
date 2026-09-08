FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt constraints-ci.txt ./
RUN pip install --no-cache-dir -c constraints-ci.txt -r requirements.txt \
    && pip check

# Neo4j 5.26+ supports the dynamic label syntax used by current Graphiti.
# Do not mutate installed graphiti_core sources at image-build time.
COPY . .

CMD ["bash"]
