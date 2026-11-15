# What the package index and the site must hold

Written by `uv run python scripts/index_check.py --write docs/package-index.md` from PyPI on the day of the
commit; run it again after changing a pin. Check the firm's index with
`uv run python scripts/index_check.py --index URL`: it resolves the same list there and names what is missing.

Every app installs from one index, wheels only, on Python 3.14: on the Windows desktops and in the Linux image.
The platform's own packages (`ctzd`, `ctzd-kit`, `ctzd-common`, the kits and their libraries) are published to
the index by the platform and are not listed. A newer version on the index is fine where the requirement allows it.

## Packages

| Package | Windows desktop | Linux image |
|---|---|---|
| `annotated-doc` | 0.0.6 | 0.0.6 |
| `annotated-types` | 0.8.0 | 0.8.0 |
| `anyio` | 4.16.0 | 4.16.0 |
| `azure-core` | 1.41.0 | 1.41.0 |
| `azure-identity` | 1.27.0 | 1.27.0 |
| `blinker` | 1.9.0 | 1.9.0 |
| `certifi` | 2026.10.23 | 2026.10.23 |
| `cffi` | 2.1.1 | 2.1.1 |
| `cfgv` | 3.5.0 | 3.5.0 |
| `charset-normalizer` | 3.5.2 | 3.5.2 |
| `click` | 8.5.1 | 8.5.1 |
| `colorama` | 0.4.6 | - |
| `comm` | 0.2.3 | 0.2.3 |
| `coverage` | 7.17.3 | 7.17.3 |
| `croniter` | 7.0.0 | 7.0.0 |
| `cryptography` | 50.0.2 | 50.0.2 |
| `dash` | 4.5.0 | 4.5.0 |
| `dash-ag-grid` | 35.3.0 | 35.3.0 |
| `dash-mantine-components` | 3.0.0 | 3.0.0 |
| `distlib` | 0.4.4 | 0.4.4 |
| `et-xmlfile` | 2.0.0 | 2.0.0 |
| `fastapi` | 0.163.1 | 0.163.1 |
| `filelock` | 4.2.10 | 4.2.10 |
| `flask` | 3.1.3 | 3.1.3 |
| `grimp` | 3.20 | 3.20 |
| `h11` | 0.16.0 | 0.16.0 |
| `httpcore` | 1.0.9 | 1.0.9 |
| `httpcore2` | 2.16.0 | 2.16.0 |
| `httptools` | 0.9.0 | 0.9.0 |
| `httpx` | 0.28.1 | 0.28.1 |
| `httpx2` | 2.16.0 | 2.16.0 |
| `identify` | 2.6.20 | 2.6.20 |
| `idna` | 3.20 | 3.20 |
| `import-linter` | 2.15 | 2.15 |
| `importlib-metadata` | 10.2.0 | 10.2.0 |
| `iniconfig` | 2.3.1 | 2.3.1 |
| `itsdangerous` | 2.2.0 | 2.2.0 |
| `janus` | 2.0.0 | 2.0.0 |
| `jaraco-classes` | 3.4.0 | 3.4.0 |
| `jaraco-context` | 6.1.2 | 6.1.2 |
| `jaraco-functools` | 4.6.0 | 4.6.0 |
| `jeepney` | - | 0.9.0 |
| `jinja2` | 3.1.6 | 3.1.6 |
| `keyring` | 25.7.0 | 25.7.0 |
| `lazy-importer` | 0.1.0 | 0.1.0 |
| `markdown-it-py` | 4.3.0 | 4.3.0 |
| `markupsafe` | 3.0.4 | 3.0.4 |
| `mdurl` | 0.1.2 | 0.1.2 |
| `more-itertools` | 11.2.1 | 11.2.1 |
| `msal` | 1.39.0 | 1.39.0 |
| `msal-extensions` | 1.3.1 | 1.3.1 |
| `mssql-python` | 1.17.0 | 1.17.0 |
| `mssql-python-odbc` | 18.6.2.2 | 18.6.2.2 |
| `mssql-python-rs` | 0.4.0 | 0.4.0 |
| `narwhals` | 2.28.0 | 2.28.0 |
| `nest-asyncio` | 1.6.0 | 1.6.0 |
| `nodeenv` | 1.12.0 | 1.12.0 |
| `numpy` | 2.5.4 | 2.5.4 |
| `openpyxl` | 3.1.5 | 3.1.5 |
| `opentelemetry-api` | 1.45.2 | 1.45.2 |
| `oracledb` | 26.1.0 | 26.1.0 |
| `orjson` | 3.13.0 | 3.13.0 |
| `packaging` | 26.3 | 26.3 |
| `pandas` | 3.1.1 | 3.1.1 |
| `pandas-stubs` | 3.1.0.261029 | 3.1.0.261029 |
| `platformdirs` | 4.14.5 | 4.14.5 |
| `plotly` | 7.1.0 | 7.1.0 |
| `pluggy` | 1.7.0 | 1.7.0 |
| `pre-commit` | 4.7.0 | 4.7.0 |
| `pyarrow` | 26.0.1 | 26.0.1 |
| `pycparser` | 3.12 | 3.12 |
| `pydantic` | 2.14.3 | 2.14.3 |
| `pydantic-core` | 2.50.4 | 2.50.4 |
| `pygments` | 2.21.0 | 2.21.0 |
| `pyjwt` | 2.15.1 | 2.15.1 |
| `pytest` | 9.2.0 | 9.2.0 |
| `pytest-cov` | 7.1.0 | 7.1.0 |
| `python-dateutil` | 4.0.0 | 4.0.0 |
| `python-discovery` | 1.9.5 | 1.9.5 |
| `python-dotenv` | 1.2.4 | 1.2.4 |
| `pywin32-ctypes` | 0.2.3 | - |
| `pyyaml` | 6.0.3 | 6.0.3 |
| `requests` | 2.34.2 | 2.34.2 |
| `retrying` | 1.4.2 | 1.4.2 |
| `rich` | 15.0.0 | 15.0.0 |
| `ruff` | 0.17.3 | 0.17.3 |
| `secretstorage` | - | 3.5.0 |
| `setuptools` | 84.2.1 | 84.2.1 |
| `sqlglot` | 30.31.0 | 30.31.0 |
| `starlette` | 1.10.0 | 1.10.0 |
| `tomlkit` | 0.15.2 | 0.15.2 |
| `truststore` | 0.10.5 | 0.10.5 |
| `ty` | 0.0.95 | 0.0.95 |
| `typing-extensions` | 4.16.0 | 4.16.0 |
| `typing-inspection` | 0.4.5 | 0.4.5 |
| `tzdata` | 2026.6 | 2026.6 |
| `urllib3` | 2.8.0 | 2.8.0 |
| `uv-build` | 0.13.8 | 0.13.8 |
| `uvicorn` | 0.54.0 | 0.54.0 |
| `uvloop` | - | 0.23.0 |
| `virtualenv` | 21.15.6 | 21.15.6 |
| `watchfiles` | 1.3.0 | 1.3.0 |
| `websockets` | 17.4 | 17.4 |
| `werkzeug` | 3.1.9 | 3.1.9 |
| `zipp` | 4.1.2 | 4.1.2 |

## The site's downloads

| Path | What |
|---|---|
| `/downloads/uv-x86_64-pc-windows-msvc.zip` | uv 0.13.6's Windows build: https://github.com/astral-sh/uv/releases/download/0.13.6/uv-x86_64-pc-windows-msvc.zip |
| `/downloads/python/20261026/cpython-3.14.10%2B20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz` | Python 3.14.10 for Windows, from python-build-standalone (https://releases.astral.sh/github/python-build-standalone/releases/download/20261026/cpython-3.14.10%2B20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz); uv asks for it under its release tag, so mirror the release folder |
| `/downloads/MinGit-64-bit.zip` | Git for Windows' MinGit, for desktops with no git: https://github.com/git-for-windows/git/releases |
| `/install.ps1` | `ctzd ci site --out DIR` writes it from platform.toml and build.toml |
