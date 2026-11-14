class KitError(Exception):
    """Base class for every error a kit raises on purpose; the message ends with the fix."""


class SourceError(KitError):
    """A source is missing, misconfigured, or used with the wrong call."""


class DataError(KitError):
    """A frame does not have the columns or types the caller relies on."""


class StoreError(KitError):
    """The table store refused a read or write."""
