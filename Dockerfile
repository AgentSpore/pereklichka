FROM ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1 AS uv
FROM python:3.12-slim@sha256:eeb8088e67610b37583880c7627e3931f087cba55a35810819e34a398f624a47 AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
COPY pereklichka ./pereklichka
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim@sha256:eeb8088e67610b37583880c7627e3931f087cba55a35810819e34a398f624a47
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY deploy ./deploy
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 CMD ["python", "deploy/readiness.py"]
ENTRYPOINT ["python", "deploy/start.py"]
