FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY usage_demo ./usage_demo
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" DEMO_DATA_DIR=/data
EXPOSE 8000 8001 4318
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["usage_demo.gateway:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
