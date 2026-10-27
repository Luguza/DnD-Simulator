"""The exceptions the engine raises at its own contract boundaries.

Each is a contract violation — the caller did something nobody may do — never a
modelling outcome: a miss, a creature at 0 hit points, a spell slot that is not
available are not errors. The layering is DESIGN.md § Failing loudly.
"""

__all__ = [
    "DndSimError",
    "EconomyError",
    "EngineAssemblyError",
    "FeatureContractError",
    "FeatureDeclarationError",
    "PhaseError",
]


class DndSimError(Exception):
    """The base of every error the simulator raises deliberately."""


class PhaseError(DndSimError):
    """A context was mutated out of its phase band or after its hook closed.

    `docs/decisions.md` § w0f.
    """


class EconomyError(DndSimError):
    """Something was spent that the creature did not have to give.

    Raised for an overdrawn action economy and an overdrawn resource pool
    (`docs/decisions.md` § w1a, § w1b).
    """


class FeatureContractError(DndSimError):
    """A feature broke the contract every handler signs.

    The named case is a handler returning anything but a `HookResult`
    (DESIGN.md § Failing loudly).
    """


class FeatureDeclarationError(FeatureContractError, ImportError):
    """A class said something impossible about itself while being created.

    Raised at class creation, so it surfaces while the module is imported. It
    is an `ImportError` as well as a `FeatureContractError` (`docs/decisions.md`
    § w0g).
    """


class EngineAssemblyError(DndSimError):
    """A handler declared machinery the engine it is running in does not have.

    For example `costs=` with no action economy or `gate=` with no action list.
    The feature is not at fault, so this is not a `FeatureContractError`
    (`docs/decisions.md` § w0g).
    """
