"""Scaffold smoke test — the package installs and imports (roadmap task w0a)."""

import dndsim


def test_package_is_importable() -> None:
    assert dndsim.__version__ == "0.1.0"
