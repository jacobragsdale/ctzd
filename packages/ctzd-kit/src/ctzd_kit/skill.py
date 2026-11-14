"""The department's skill, `sbu-<sbu>`, written from the kit itself: each source, each loader with its columns and an
example call, and the department's own `NOTES.md`. So it cannot drift from the code, and `ctzd sources` shows the
same facts."""

from collections.abc import Mapping, Sequence

from ctzd_kit.sources import Kit


def _columns(loader: Mapping[str, object]) -> str:
    columns = loader.get("columns")
    if not isinstance(columns, list):
        return ""
    return ", ".join(f"`{column.get('name')}` ({column.get('type')})" for column in columns if isinstance(column, dict))


def render(kit: Kit, sources: Sequence[Mapping[str, object]]) -> str:
    """SKILL.md for `kit`, from `sources` as `ctzd_kit.explore.describe_source` gives them."""
    title, module, sbu = kit.spec.title, kit.module, kit.spec.sbu
    lines = [
        "---",
        f"name: sbu-{sbu}",
        (
            f"description: {title}'s data: every source and loader of the {module} kit, with its columns and an example call, and the department's own notes."
            " Read it before you read data, write SQL or name a source in app.toml."
        ),
        "---",
        "",
        f"# {title} data",
        "",
        f"Every read goes through this department's kit, `{module}`, in `src/app/data/`. An app calls a source's loaders,",
        "or runs its own SQL file with `query(SOURCE, FILE, **binds)` on a source whose kind is a database. Name every",
        "source the app reads in `[sources].use` of app.toml. `ctzd sources` prints what this page says, as JSON.",
        "",
        "```python",
        f"from {module} import query, sample, store  # and each source by its name, as below",
        "```",
        "",
    ]
    for source in sources:
        name = source.get("name")
        lines += [f"## {name}", "", f"{source.get('description')}", ""]
        facts = [f"Kind: {source.get('kind')}."]
        if source.get("refreshed"):
            facts.append(f"Current: {source.get('refreshed')}.")
        if source.get("dialect"):
            facts.append(f"Your own SQL for it is {source.get('dialect')} SQL in `src/app/data/sql/`.")
        if source.get("desktop") is False:
            facts.append("It cannot run on a desktop, so there it always serves its sample rows.")
        lines += [" ".join(facts), "", "```python", f"from {module} import {name}", "```", ""]
        loaders = source.get("loaders")
        for given in loaders if isinstance(loaders, list) else []:
            if not isinstance(given, dict):
                continue
            loader = {str(key): value for key, value in given.items()}
            lines += [f"### `{name}.{loader.get('signature')}`", "", str(loader.get("doc") or "").strip(), ""]
            lines += [f"Columns: {_columns(loader)}.", "", f"Example: `{loader.get('example')}`", ""]
    notes = kit.notes().strip()
    if notes:
        lines += [f"## {title}'s notes", "", notes, ""]
    return "\n".join(lines)
