FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WORK_DIR=/data/work

WORKDIR /app
RUN groupadd --system worker && useradd --system --gid worker --home /app worker
COPY pyproject.toml README.md ./
COPY worker ./worker
RUN pip install --no-cache-dir . \
    && mkdir -p /data/work \
    && chown -R worker:worker /app /data

USER worker
ENTRYPOINT ["rpa-worker"]
