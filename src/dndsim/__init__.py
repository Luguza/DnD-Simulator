"""dndsim — a Monte Carlo D&D 5e (2024 / XPHB) combat simulator.

The public surface is `dndsim.api`; `dndsim.cli` is a thin adapter over it and
holds no simulation logic. See DESIGN.md § Module layout.
"""

from importlib.metadata import version

__version__ = version("dnd-simulator")

__all__ = ["__version__"]
