"""The exceptions the engine raises at its own contract boundaries.

Errors live in one module below everything else because they are the vocabulary
of DESIGN.md § Failing loudly — layered, and that section is a single story told
at four moments (import, load, runtime, report). Spreading the types across the
modules that happen to raise them would make the layering readable only by
grepping for `class .*Error`.

Everything here is a *contract* violation — the caller did something the design
says nobody may do — never a modelling outcome. A creature at 0 hit points, a
spell slot that is not available, an attack that misses: none of those are
errors, and none of them belong here.

`PhaseError` arrives with `hooks/contexts.py` (w0f) because a context is the
first object in the project with a lifetime shorter than the run.
`FeatureContractError` — the handler that returns something other than a
`HookResult` — arrives with the dispatcher (w0g), which is the first code able
to observe a return value at all.
"""

__all__ = [
    "DndSimError",
    "PhaseError",
]


class DndSimError(Exception):
    """The base of every error the simulator raises deliberately.

    It exists so that a caller embedding the simulator can distinguish "the
    simulator refused" from "Python broke", which is the difference between a
    bug report worth reading and a traceback.
    """


class PhaseError(DndSimError):
    """A context was mutated at a moment its own lifecycle had passed.

    Two mistakes reach here, and they are the same mistake seen at two scales
    (DESIGN.md § Runtime invariants):

    - **A mutator called out of band.** `ctx.veto()` belongs to `EARLY` and
      `FINAL`, the two bands the design gives a handler permission to halt the
      chain in; a `NORMAL` handler calling it is claiming an authority its phase
      does not carry.
    - **A mutator called after the moment.** A context is live only while its
      hook is dispatching. Because contexts are pooled and reused (DESIGN.md §
      Levers), a handler that stashes one and writes to it later is not merely
      writing to a finished event — it is writing to whichever event is using
      that object now.

    It is not an `AttributeError`: the attribute exists and the mutator is the
    right one to call, which is precisely why the timing is worth naming
    separately from "you assigned to a field".
    """
