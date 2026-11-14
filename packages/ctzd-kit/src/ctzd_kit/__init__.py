"""What every department's kit builds on, and what only kits import: the loader seam, the fakes, the store, the drivers
and the source explorer. An app imports its kit (`ctzd_fi`), never this package.

A kit's own code uses:

    from ctzd_kit import loader, query          # sources/NAME/loaders.py of a database source
    from ctzd_kit.drivers.rest import rest      # a REST source's client, inside its loaders
    from ctzd_kit.drivers import files          # a file source's files, inside its loaders

and its `__init__.py` gives apps `query` (`app_query`), `store`, `sample` and each source's loaders by its name.
"""

from ctzd_kit import store
from ctzd_kit.cache import cached
from ctzd_kit.errors import DataError, KitError, SourceError, StoreError
from ctzd_kit.loaders import app_query, loader, query
from ctzd_kit.sample import loaders as sample

__all__ = ["DataError", "KitError", "SourceError", "StoreError", "app_query", "cached", "loader", "query", "sample", "store"]
