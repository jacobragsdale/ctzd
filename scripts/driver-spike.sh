#!/usr/bin/env bash
# Phase 0 driver spike: start throwaway Oracle Free and SQL Server containers, run ctzd-core's
# integration tests against them (python-oracledb thin mode, mssql-python), then remove them.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
oracle=ctzd-spike-oracle
mssql=ctzd-spike-mssql
password="Ctzd-spike-$(date +%s)"
cleanup() { docker rm -f "$oracle" "$mssql" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$oracle" -p 127.0.0.1:15210:1521 -e ORACLE_PASSWORD="$password" \
    -e APP_USER=ctzd -e APP_USER_PASSWORD="$password" gvenzl/oracle-free:23-slim-faststart >/dev/null
docker run -d --name "$mssql" -p 127.0.0.1:15433:1433 -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD="$password" \
    mcr.microsoft.com/mssql/server:2022-latest >/dev/null
echo "waiting for the databases..."
until docker logs "$oracle" 2>&1 | grep -q "DATABASE IS READY TO USE"; do sleep 3; done
until docker logs "$mssql" 2>&1 | grep -q "SQL Server is now ready for client connections"; do sleep 3; done
sleep 5
export CTZD_SOURCE_SPIKE_ORACLE="{\"kind\":\"oracle\",\"dsn\":\"127.0.0.1:15210/FREEPDB1\",\"user\":\"ctzd\",\"password\":\"$password\"}"
export CTZD_SOURCE_SPIKE_MSSQL="{\"kind\":\"mssql\",\"server\":\"127.0.0.1,15433\",\"database\":\"master\",\"user\":\"sa\",\"password\":\"$password\",\"trust_server_certificate\":true}"
cd "$repo"
uv run pytest packages/ctzd-core -m integration -s -q -p no:randomly
