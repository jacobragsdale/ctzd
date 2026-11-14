"""Every test runs on the sample rows, with the store and the outbox in scratch folders, as `ctzd check` runs them."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CTZD_ENV", "local")
os.environ.setdefault("CTZD_FAKE", "1")
os.environ.setdefault("CTZD_FIXTURES", str(ROOT / "fixtures"))
os.environ.setdefault("CTZD_STORE_URL", str(ROOT / ".ctzd" / "out" / "test-store"))
os.environ.setdefault("CTZD_OUTBOX", str(ROOT / ".ctzd" / "out" / "test-outbox"))
os.environ.setdefault("CTZD_APP", "{{app}}")
os.environ.setdefault("CTZD_SBU", "{{sbu}}")
