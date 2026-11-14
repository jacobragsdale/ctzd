"""Job `{{job}}`: runs on its schedule in app.toml. Exit code 0 means success."""

import logging

import ctzd

from app.data.loaders import load_demo
from app.logic.summary import with_share

_LOG = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    frame = with_share(load_demo())
    version = ctzd.write_table(frame, "{{job}}")
    _LOG.info("wrote %d rows to table {{job}} as version %s", len(frame), version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
