# One image for both the API and the worker (different start commands).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps:/app \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/opt/hf-cache

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first (cached layer), then source.
COPY pyproject.toml uv.lock ./
# Cache mount keeps uv's download cache out of the image layer (~1.4 GB smaller).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# Non-root user; it owns the model cache so the download below doesn't need a later chown (which duplicates layers).
RUN useradd --create-home --uid 10001 app && mkdir -p /opt/hf-cache && chown app /opt/hf-cache
USER app

# Bake the small local models into the image so containers start without downloading them.
ARG PRELOAD_MODELS=1
RUN if [ "$PRELOAD_MODELS" = "1" ]; then python -c "\
from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-small-en-v1.5'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"; fi

COPY apps/api apps/api
COPY worker worker
COPY migrations migrations
COPY eval eval

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
