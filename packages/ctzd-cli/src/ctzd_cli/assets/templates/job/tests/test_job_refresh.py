from pathlib import Path

import ctzd

from app.jobs.{{job}} import main


def test_job_publishes_its_table(tmp_path: Path) -> None:
    with ctzd.testing.fake_store(tmp_path):
        assert main() == 0
        assert not ctzd.read_table("{{job}}").empty
