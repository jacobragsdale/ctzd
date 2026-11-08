"""`ctzd.dataset`: what a React app's page reads, declared in `src/app/api/datasets.py`.

A dataset is read-only: its function's arguments are the page's parameters, it
returns a frame, and the page gets that frame's rows, as Arrow for parallax or as
JSON. Its row type names each column and how the page shows it:

    class SectorShare(ctzd.Row):
        category: str
        value: ctzd.Number
        share: ctzd.Pct

    @ctzd.dataset(SectorShare)
    def sector_shares() -> pd.DataFrame:
        return with_share(load_demo())

Every number is a float, `None` where it is missing, and each column kind says
how the page rounds it: `Number` (two decimals), `Count` (none), `Price`,
`Price32` (32nds), `Pct` and `Bps` (both fractions: 0.0525 is 5.25% and 525 bp),
and `money("USD")`. `ctzd.api` serves the datasets; this module only declares
them, so a job that imports the app's logic never needs the web stack.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FunctionType
from typing import Annotated

from basis.api import format as formats
from basis.api.format import Formatted
from pydantic import BaseModel, ConfigDict

from ctzd._errors import CtzdError

DEFAULT_DEADLINE_S = 30.0


def money(currency: str, *, decimals: int | None = None) -> Formatted:
    """An amount in `currency`, such as `amount: Annotated[float | None, ctzd.money("USD")]`."""
    return formats.money(currency, decimals)


# basis-api writes each kind into the API's document as `x-basis-format` and into the Arrow stream's field
# metadata as `basis.format`, which parallax and @basis/ui's number components format each column by.
Number = Annotated[float | None, formats.number(2)]
Count = Annotated[float | None, formats.number(0)]
Price = Annotated[float | None, formats.price()]
Price32 = Annotated[float | None, formats.price32()]
Pct = Annotated[float | None, formats.pct()]
Bps = Annotated[float | None, formats.bps()]


class Row(BaseModel):
    """A dataset's row: one field per column the page reads, in the order it shows them."""

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class Dataset:
    """A registered dataset: its function, its row type, and how long a request may take."""

    function: FunctionType
    row: type[Row]
    deadline_s: float

    @property
    def name(self) -> str:
        return str(self.function.__name__)

    @property
    def path(self) -> str:
        return "/datasets/" + self.name.replace("_", "-")


_registered: dict[tuple[str, str], Dataset] = {}
"""Every dataset declared so far, by its module and its name."""


def dataset[F: FunctionType](row: type[Row], *, deadline_s: float = DEFAULT_DEADLINE_S) -> Callable[[F], F]:
    """Register the decorated function as a dataset whose rows are `row`s; the function itself is unchanged."""
    if not (isinstance(row, type) and issubclass(row, Row)):
        msg = f"ctzd.dataset takes a ctzd.Row subclass, not {row!r}."
        raise CtzdError(msg)

    def register(function: F) -> F:
        declared = Dataset(function=function, row=row, deadline_s=deadline_s)
        _registered[str(function.__module__), declared.name] = declared
        return function

    return register


def registered(module: str) -> Mapping[str, Dataset]:
    """The datasets `module` declares, by name."""
    return {name: declared for (owner, name), declared in _registered.items() if owner == module}
