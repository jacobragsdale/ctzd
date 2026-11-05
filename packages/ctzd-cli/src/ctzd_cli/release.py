"""Release records: each successful deploy is an annotated git tag `ctzd/vN` holding this JSON."""

import json
from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, ValidationError

TAG_PREFIX = "ctzd/v"


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)
    release: int
    commit: str
    image: str
    created_at: datetime
    app_toml: str


def tag_name(number: int) -> str:
    return f"{TAG_PREFIX}{number}"


def parse_tags(entries: Iterable[tuple[str, str]]) -> list[Release]:
    """Releases from (tag name, tag message) pairs, newest first; tags that are not release records are skipped."""
    releases: list[Release] = []
    for name, message in entries:
        if not name.startswith(TAG_PREFIX):
            continue
        try:
            releases.append(Release.model_validate_json(message))
        except ValidationError:
            continue
    return sorted(releases, key=lambda release: release.release, reverse=True)


def next_number(releases: Iterable[Release]) -> int:
    return max((release.release for release in releases), default=0) + 1


def message(release: Release) -> str:
    return json.dumps(release.model_dump(mode="json"), indent=2, sort_keys=True)
