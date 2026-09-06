"""The scaffold contract: the package imports and reports its version.

Replaced by real contract tests as W0 lands; until then it is what makes
`pytest` a meaningful signal on an otherwise empty suite.
"""

import dndsim


def test_package_reports_a_version() -> None:
    assert dndsim.__version__
