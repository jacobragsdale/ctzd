# syntax=docker/dockerfile:1
# Base image for every ctzd app: Python, uv, the system libraries mssql-python needs, and a non-root user.
# Build: docker build -t contosoctzd.azurecr.io/ctzd/base:py3.14 images/base
ARG PYTHON_IMAGE=python:3.14-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6
FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE}
# libltdl7 and the Kerberos libraries are what mssql-python's bundled ODBC driver loads at connect time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=uv /uv /uvx /usr/local/bin/
# Apps install into /app/.venv with the system Python; uv never downloads its own.
# Package indexes come from each app's pyproject.toml and uv.lock, the same as on laptops.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
