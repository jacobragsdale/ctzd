"""`ctzd docs ui [NAME]`: the firm's components, from the catalogue the app's own `@basis/ui` ships.

The catalogue is `web/node_modules/@basis/ui/catalogue.json`, written when
basis-ui builds the package, so it always describes the version the app
installed: each component's family, purpose, props, variants, and, for each
family, one example that type-checks. Without a name the command lists the
families and their components; with one, it gives that component in full.
"""

import difflib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ctzd_cli.errors import CliError, UsageError

CATALOGUE = Path("web") / "node_modules" / "@basis" / "ui" / "catalogue.json"
_FIRST_SENTENCE = re.compile(r"(.+?\.)(\s|$)")
_SUGGESTIONS = 3


class _Model(BaseModel):
    # The catalogue grows with basis-ui; fields this CLI does not know are left out, never refused.
    model_config = ConfigDict(extra="ignore", frozen=True)


class Prop(_Model):
    name: str
    type: str
    optional: bool = False
    description: str = ""


class Variant(_Model):
    default: str | None = None
    options: tuple[str, ...] = ()


class Component(_Model):
    name: str
    family: str
    purpose: str = ""
    extends: tuple[str, ...] = ()
    props: tuple[Prop, ...] = ()
    variants: dict[str, Variant] = {}
    example: str | None = None


class Catalogue(_Model):
    package: str
    components: tuple[Component, ...]
    icons: tuple[str, ...] = ()


def load(root: Path) -> Catalogue:
    path = root / CATALOGUE
    if not path.is_file():
        msg = "The page's packages are not installed, so there is no component catalogue to read."
        raise CliError(msg, hint="Run `ctzd dev` or `ctzd check`, which install them, then this command again.")
    try:
        return Catalogue.model_validate_json(path.read_bytes())
    except ValidationError as err:
        msg = f"{CATALOGUE.as_posix()} is not a catalogue this ctzd can read."
        raise CliError(msg, hint=f"Upgrade ctzd (`uv tool upgrade ctzd`). {str(err).splitlines()[0]}") from None


def _summary(text: str) -> str:
    found = _FIRST_SENTENCE.match(text)
    return text if found is None else str(found.group(1))


def overview(catalogue: Catalogue) -> dict[str, dict[str, object]]:
    """Each family: what its main component is for, and the names of its components."""
    families: dict[str, list[Component]] = {}
    for component in catalogue.components:
        families.setdefault(component.family, []).append(component)
    result: dict[str, dict[str, object]] = {}
    for family, members in sorted(families.items()):
        main = next((member for member in members if member.name.lower() == family.replace("-", "")), members[0])
        result[family] = {"purpose": _summary(main.purpose), "components": [member.name for member in members]}
    return result


def entry(catalogue: Catalogue, name: str) -> dict[str, object]:
    """One component in full, with its family's example when it has none of its own."""
    by_name = {component.name.lower(): component for component in catalogue.components}
    found = by_name.get(name.lower())
    if found is None:
        close = difflib.get_close_matches(name, [component.name for component in catalogue.components], n=_SUGGESTIONS)
        msg = f"@basis/ui has no component named {name!r}."
        raise UsageError(msg, hint=(f"Did you mean {', '.join(close)}? " if close else "Run `ctzd docs ui` for the list. ") + "Grids, pivots and charts are parallax's: see the ctzd-pages skill.")
    example = found.example or next((member.example for member in catalogue.components if member.family == found.family and member.example is not None), None)
    return {
        "name": found.name,
        "family": found.family,
        "purpose": found.purpose,
        "extends": list(found.extends),
        "props": [prop.model_dump() for prop in found.props],
        "variants": {key: variant.model_dump() for key, variant in found.variants.items()},
        "example": example,
        "family_components": [member.name for member in catalogue.components if member.family == found.family],
    }
