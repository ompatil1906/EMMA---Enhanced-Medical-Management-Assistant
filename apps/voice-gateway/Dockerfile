# EMMA voice gateway — minimal, reproducible image (uv for dependency resolution).
# Simulation mode needs no external services or paid APIs.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

# Resolve dependencies first for build caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm

RUN groupadd --system emma && useradd --system --gid emma --create-home emma

WORKDIR /srv
COPY --from=builder /build/.venv /srv/.venv
COPY --from=builder /build/app /srv/app

ENV PATH="/srv/.venv/bin:$PATH" \
    APP_ENV=production \
    LOG_LEVEL=INFO

USER emma
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
