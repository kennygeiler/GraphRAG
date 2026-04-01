# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="graphrag-narrative-mri"
LABEL org.opencontainers.image.description="Streamlit Narrative Timeline Analyzer (Neo4j-backed)"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer (cache-friendly)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# Application
COPY . .

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --shell /bin/bash --uid 1000 app \
    && chown -R app:app /app
USER app

# Render / Fly / Railway set PORT; default 8501 for local docker run
EXPOSE 8501

SHELL ["/bin/sh", "-c"]
CMD exec streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port="${PORT:-8501}" \
  --server.headless=true
