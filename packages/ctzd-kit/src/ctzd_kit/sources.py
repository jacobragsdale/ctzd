"""A kit and its sources: what `kit.toml` and each `sources/NAME/source.toml` say, read from the installed package.

A kit is the package `ctzd_<sbu>`, found through its `ctzd.kits` entry point. Its folder holds `kit.toml` (the
department), `NOTES.md` (its conventions, in its own words), `helpers/` (pure functions) and `sources/`, one folder per
source with `source.toml`, `loaders.py`, `fixtures/` and, for a database, `sql/`. Every kit also has `sample`, which
`ctzd_kit.sample` gives it, a source that exists only as sample rows.

A source's secret reaches the code the way its `source.toml` says (`delivery`):

- a source read with one of ctzd-kit's drivers (`oracle`, `mssql`, `rest`, `file`) gets the whole secret, a JSON
  object, as `CTZD_SOURCE_<NAME>`;
- a `package` source gets what its department package already reads: `[secret.env]` names each variable and the
  secret's field it holds, and `[secret.file]` names a variable that holds the path of a file the whole secret is
  written to.
"""

import functools
import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata, resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ctzd_kit.connections import variable
from ctzd_kit.errors import SourceError

type Kind = Literal["oracle", "mssql", "rest", "file", "package", "sample"]
DRIVERS: frozenset[str] = frozenset({"oracle", "mssql", "rest", "file"})
DIALECTS: Mapping[str, str] = {"oracle": "oracle", "mssql": "tsql"}
"""The SQL dialect of each kind of source an app may write its own SQL for, as sqlglot names it."""
SQL_KINDS: frozenset[str] = frozenset({"oracle", "mssql", "sample"})
"""The kinds an app may run its own SQL files against. `sample` answers them from the app's fixtures alone."""
ENTRY_POINTS = "ctzd.kits"
SAMPLE = "sample"
NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,127}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always", allow_inf_nan=False)


class SecretFile(_Model):
    variable: str = Field(description="the variable the package reads the file's path from")
    filename: str = Field(default="secret.json", pattern=r"^[A-Za-z0-9._-]+$")


class Secret(_Model):
    env: dict[str, str] = Field(default_factory=dict, description="each variable the package reads, and the secret's field it holds")
    file: SecretFile | None = None

    @field_validator("env")
    @classmethod
    def _variables(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if _VARIABLE.fullmatch(name) is None:
                msg = f"{name!r} is not an environment variable name such as FI_RISK_USER"
                raise ValueError(msg)
        return value


class SourceSpec(_Model):
    """`source.toml`."""

    description: str = Field(min_length=1, description="what the source holds, in business words")
    refreshed: str | None = Field(default=None, description="when its data is current, such as 'each business day by 07:00 London'")
    kind: Kind
    io: tuple[str, ...] = Field(default=(), description="the packages its loaders call that make their own connections, which apps may not import")
    desktop: bool = Field(default=True, description="false where it cannot run on a desktop, which then always serves sample rows there")
    max_concurrent: int = Field(default=4, ge=1, le=32, description="how many of its reads may run at once in one process")
    secret: Secret | None = None

    @property
    def dialect(self) -> str | None:
        return DIALECTS.get(self.kind)


class KitSpec(_Model):
    """`kit.toml`: the department."""

    sbu: str = Field(pattern=r"^[a-z][a-z0-9]{1,15}$")
    title: str = Field(min_length=1)
    team: str = Field(min_length=1, description="the GitHub team whose members may create apps on this kit")
    owners: tuple[str, ...] = Field(min_length=1, description="the kit's code owners, as GitHub handles or teams")


@dataclass(frozen=True)
class Source:
    name: str
    spec: SourceSpec
    package: str
    """The source's package, such as `ctzd_fi.sources.fi_pricing`, or `ctzd_kit.sample`."""

    @property
    def folder(self) -> Traversable:
        return resources.files(self.package)

    @property
    def loaders_module(self) -> str:
        return f"{self.package}.loaders"


@dataclass(frozen=True)
class Kit:
    module: str
    spec: KitSpec
    sources: Mapping[str, Source]

    @property
    def folder(self) -> Traversable:
        return resources.files(self.module)

    def source(self, name: str) -> Source:
        found = self.sources.get(name)
        if found is None:
            msg = f"The {self.spec.title} kit has no source {name!r}; it has {', '.join(sorted(self.sources))}. `ctzd sources` lists them."
            raise SourceError(msg)
        return found

    def notes(self) -> str:
        path = self.folder / "NOTES.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""


def _toml(path: Traversable) -> dict[str, object]:
    return dict(tomllib.loads(path.read_text(encoding="utf-8")))


def read_spec(path: Traversable, where: str) -> SourceSpec:
    try:
        return SourceSpec.model_validate(_toml(path))
    except (tomllib.TOMLDecodeError, ValidationError) as err:
        msg = f"{where} is invalid: {' '.join(str(err).split())}"
        raise SourceError(msg) from None


def sample_source() -> Source:
    return Source(SAMPLE, read_spec(resources.files("ctzd_kit.sample") / "source.toml", "ctzd_kit's sample source.toml"), "ctzd_kit.sample")


@functools.cache
def load_kit(module: str) -> Kit:
    """The kit whose package is `module`, from its installed files."""
    folder = resources.files(module)
    try:
        spec = KitSpec.model_validate(_toml(folder / "kit.toml"))
    except (FileNotFoundError, tomllib.TOMLDecodeError, ValidationError) as err:
        msg = f"{module}/kit.toml is missing or invalid: {' '.join(str(err).split())}"
        raise SourceError(msg) from None
    sources: dict[str, Source] = {SAMPLE: sample_source()}
    root = folder / "sources"
    for entry in sorted(root.iterdir(), key=lambda item: item.name) if root.is_dir() else ():
        if not entry.is_dir() or entry.name.startswith(("_", ".")):
            continue
        if NAME.fullmatch(entry.name) is None or entry.name == SAMPLE:
            msg = f"{module}/sources/{entry.name} is not a source name: lowercase letters, digits and underscores, starting with a letter, and not {SAMPLE!r}"
            raise SourceError(msg)
        sources[entry.name] = Source(entry.name, read_spec(entry / "source.toml", f"{module}/sources/{entry.name}/source.toml"), f"{module}.sources.{entry.name}")
    return Kit(module=module, spec=spec, sources=sources)


def installed() -> Kit:
    """The one kit installed beside the app."""
    found = sorted({entry.value for entry in metadata.entry_points(group=ENTRY_POINTS)})
    if len(found) != 1:
        msg = f"An app has exactly one kit installed, and here there are {len(found)}: {', '.join(found) or 'none'}. Its pyproject.toml lists the department's kit."
        raise SourceError(msg)
    return load_kit(found[0])


def source_of(package: str) -> Source:
    """The source whose package is `package`: `ctzd_fi.sources.fi_pricing`, or `ctzd_kit.sample`."""
    if package == "ctzd_kit.sample":
        return sample_source()
    module, marker, name = package.rpartition(".sources.")
    if not marker or "." in name:
        msg = f"{package} is not a source's package: a loader lives in sources/NAME/loaders.py of its kit."
        raise SourceError(msg)
    return load_kit(module).source(name)


@dataclass(frozen=True)
class Delivery:
    """How a source's secret reaches the code: variables, and files by name with the variable that holds each one's path."""

    env: Mapping[str, str]
    files: Mapping[str, str]


def delivery(source: Source, secret: str, *, folder: Path) -> Delivery:
    """What `source`'s secret, its JSON text, becomes, with any file written under `folder`."""
    spec = source.spec
    if spec.kind in DRIVERS or spec.secret is None:
        return Delivery(env={variable(source.name): secret}, files={})
    try:
        fields: object = json.loads(secret)
    except json.JSONDecodeError:
        fields = None
    if not isinstance(fields, dict):
        msg = f"The secret of {source.name} is not a JSON object of fields."
        raise SourceError(msg)
    values = {str(key): value for key, value in fields.items()}
    env: dict[str, str] = {}
    for name, field in spec.secret.env.items():
        if field not in values:
            msg = f"The secret of {source.name} has no field {field!r}, which source.toml gives {name}."
            raise SourceError(msg)
        env[name] = str(values[field])
    files: dict[str, str] = {}
    if spec.secret.file is not None:
        path = folder / source.name / spec.secret.file.filename
        files[path.as_posix()] = secret
        env[spec.secret.file.variable] = str(path)
    return Delivery(env=env, files=files)


def has_credentials(source: Source, environ: Mapping[str, str] | None = None) -> bool:
    """Whether the secret of `source` reached this process, in the form its `source.toml` says."""
    given = os.environ if environ is None else environ
    spec = source.spec
    if spec.kind == "sample":
        return False
    if spec.kind in DRIVERS:
        return bool(given.get(variable(source.name)))
    if spec.secret is None:
        return True
    present = all(given.get(name) for name in spec.secret.env)
    if spec.secret.file is not None:
        path = given.get(spec.secret.file.variable, "")
        present = present and bool(path) and Path(path).is_file()
    return present
