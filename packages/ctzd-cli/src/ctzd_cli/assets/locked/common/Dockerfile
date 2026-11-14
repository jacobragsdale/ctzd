# syntax=docker/dockerfile:1
# Platform-owned: ctzd sync rewrites this file. Jenkins passes BASE_IMAGE.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY app.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
