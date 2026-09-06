"""The extension point every feature plugs into: the registry, the contexts, the
decorator, and the dispatcher.

Importing anything from this package **defines the 39 hooks**, because this
module imports `definitions` for its side effect. That is deliberate and it is
the only import of its kind in the project: `registry.hook_named()` has to raise
on a typo whatever the caller happened to import first, and a registry that is
complete only when some other module got imported earlier would make
`@hook("on_hitt")` fail or pass depending on collection order.

Names are otherwise not re-exported here — one name, one import path, as in
`rules/` and `core/`: `from dndsim.hooks.definitions import ON_HIT`.
"""

from dndsim.hooks import definitions  # noqa: F401  (the import is the point)
