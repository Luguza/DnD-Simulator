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
`FeatureContractError` and its two companions arrive with the dispatcher (w0g),
which is the first code able to observe a handler at all — one at the moment the
handler's class is created, one at the moment it returns, and one for the
machinery it declared that nobody wired in.
"""

__all__ = [
    "DndSimError",
    "EngineAssemblyError",
    "FeatureContractError",
    "FeatureDeclarationError",
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


class FeatureContractError(DndSimError):
    """A feature broke the contract every handler signs.

    The named case in DESIGN.md § Failing loudly is the return value: a handler
    is a function from a context to a `HookResult`, and one that returns `None`
    — the shape of a handler whose author forgot the `return` — would otherwise
    be indistinguishable from one that deliberately did nothing. That
    distinction is what the no-op detector and the `once_per` budget both read,
    so absorbing it would make two other mechanisms quietly wrong rather than
    loudly broken.
    """


class FeatureDeclarationError(FeatureContractError, ImportError):
    """A class said something impossible about itself while being created.

    A bad handler signature, an annotation naming the wrong context, an
    override that forgot to re-declare its hook, a malformed id: all of them are
    mistakes visible at class-creation time, which is the earliest moment the
    information exists (DESIGN.md § Fail at the earliest moment). Raised from
    `HookSource.__init_subclass__`, it therefore surfaces while the feature
    module is being imported and nothing has run yet.

    It **is** an `ImportError`, and that is the point rather than a flourish
    (ROADMAP w0g, "bad signature is an `ImportError`"): a batch of ~630 content
    modules is loaded by a tool that already distinguishes "this module would
    not import" from "this module misbehaved", and a declaration mistake belongs
    on the first side of that line. It is also a `FeatureContractError`, because
    the contract broken is the same one — only noticed earlier.
    """


class EngineAssemblyError(DndSimError):
    """A handler declared machinery the engine it is running in does not have.

    `costs=ActionCost.REACTION` needs an action economy to spend from and
    `gate="apl.id"` needs an action list to ask, and both arrive later than the
    dispatcher does (w1a, w2f). Until they are wired in, a handler that declares
    one must raise rather than run: a reaction that fires without being paid for
    and a gate that is treated as open are both silent corruptions of every
    number the simulation produces, and silence is the one failure this design
    refuses everywhere else.

    It is not a `FeatureContractError`: the feature declared something
    legitimate, and it is the assembly around it that is incomplete.
    """
