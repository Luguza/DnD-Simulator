"""Creatures: what they are made of, what they carry, and what is happening to
them.

Everything here sits *above* `hooks/` — a creature owns a `Dispatcher` and the
sources attached to it, so the dependency runs one way and nothing in `hooks/`
or `core/` ever imports a creature. `w0g2` built the minimum the performance
spike and the walking skeleton needed; `w1a` completed `creature.py` and
`stats.py` and added `economy.py` beside them, and `w1b`/`w1c` add resources and
effects next.

Names are not re-exported: one name, one import path.
"""
