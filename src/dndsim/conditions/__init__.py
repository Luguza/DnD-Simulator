"""The 15 conditions of the 2024 Rules Glossary, as `Effect` subclasses.

A condition is behaviour attached to a creature, never a flag the engine reads:
`rules/enums.py`'s `Condition` is the *name* and a class here is what it does
(DESIGN.md § Durations). One module per condition, `base.py` for the two shapes
they share and `registry.py` for the closed mapping from name to class.

Names are not re-exported: one name, one import path.
"""
