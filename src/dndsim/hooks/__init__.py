"""The extension point every feature plugs into: the registry, the contexts, the
decorator, and the dispatcher.

Importing anything from this package defines the 39 hooks, because this module
imports `definitions` for its side effect (`docs/decisions.md` § w0e). Names are
not re-exported here: `from dndsim.hooks.definitions import ON_HIT`.
"""

from dndsim.hooks import definitions  # noqa: F401  (the import is the point)
