"""How far each portfolio has drifted from its targets: pure functions of the allocation reports' rows."""

import pandas as pd


def drift(allocations: pd.DataFrame) -> pd.DataFrame:
    """Each row with `drift`, its weight less its target, and each portfolio's `largest_drift`, the largest absolute
    drift of any of its asset classes."""
    drifted = allocations.assign(drift=allocations["weight"] - allocations["target_weight"])
    return drifted.assign(largest_drift=drifted.groupby("portfolio_id")["drift"].transform(lambda column: column.abs().max()))
