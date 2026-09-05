"""The rules vocabulary: closed enumerations and the universal rules tables.

`enums` and `constants` are imported by every other package, so nothing in here
may import from anywhere else in `dndsim` — this is the bottom of the stack.

Names are deliberately *not* re-exported from this package. One name, one import
path: `from dndsim.rules.enums import DamageType`, never two spellings of the
same import for a reviewer to reconcile.
"""
