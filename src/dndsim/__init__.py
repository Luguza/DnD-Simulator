"""dndsim — a Monte Carlo D&D 5e (2024 / XPHB) combat simulator.

Holds `__version__`. The module layout is DESIGN.md § Module layout.
"""

from importlib.metadata import version

__version__ = version("dnd-simulator")

__all__ = ["__version__"]
