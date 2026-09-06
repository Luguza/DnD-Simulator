"""The engine: the resolution orders everything else in the project defers to.

One file per order, and each order written exactly once — the damage steps live
in `damage.py` and nowhere else, the attack sequence in `attack.py` (w1f) and
nowhere else. That is the rule the whole design leans on hardest: a feature never
re-implements a resolution order, it contributes to one.

`w0g2` creates `damage.py` as the spike substrate; `w1e` completes it. Names are
not re-exported: one name, one import path.
"""
