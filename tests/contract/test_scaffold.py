"""The scaffold contract: the package imports and reports its version."""

import dndsim


def test_package_reports_a_version() -> None:
    assert dndsim.__version__
