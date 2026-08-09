"""The ``review.xlsx`` human review surface.

Import the submodules directly — :mod:`photoarchive.review.model`,
:mod:`photoarchive.review.excel`, :mod:`photoarchive.review.builder`.

This package deliberately re-exports nothing. ``builder`` depends on the
catalog, and the catalog's learning depends on ``review.model``; a convenience
re-export here turns that into a genuine import cycle whose failure depends on
which module a test happens to import first.
"""
