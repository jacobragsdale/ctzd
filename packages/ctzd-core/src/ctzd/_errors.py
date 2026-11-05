class CtzdError(Exception):
    """Base class for every error ctzd raises on purpose."""


class SourceError(CtzdError):
    """A data source is missing, misconfigured, or used with the wrong call."""


class DataError(CtzdError):
    """A frame does not have the columns or dtypes the caller required."""


class StoreError(CtzdError):
    """The table store refused a read or write."""
