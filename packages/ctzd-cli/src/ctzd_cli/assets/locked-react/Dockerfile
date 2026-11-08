# syntax=docker/dockerfile:1
# Platform-owned: ctzd sync rewrites this file. Jenkins passes BASE_IMAGE; NODE_IMAGE builds the pages.
ARG BASE_IMAGE
ARG NODE_IMAGE=node:26-slim
FROM ${NODE_IMAGE} AS pages
WORKDIR /app/web
RUN npm install --global pnpm@12.18.0
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY web ./
RUN pnpm exec vp build

FROM ${BASE_IMAGE}
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY app.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
COPY --from=pages /app/web/dist ./web/dist
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
