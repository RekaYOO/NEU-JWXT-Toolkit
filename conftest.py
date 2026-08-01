"""Isolate every pytest run from the application's real runtime data."""

from __future__ import annotations

import logging
import os
import tempfile


# Application dependencies create storage and log handlers while test modules are
# imported.  A fixture would run too late, so the override must happen while this
# root conftest is imported.  Always replace an inherited value: accidentally
# pointing pytest at a development or production data directory is never useful.
_ORIGINAL_DATA_DIR = os.environ.get("NEU_JWXT_DATA_DIR")
_TEST_DATA_DIR = tempfile.TemporaryDirectory(
    prefix="neu-jwxt-tests-",
    ignore_cleanup_errors=True,
)
os.environ["NEU_JWXT_DATA_DIR"] = _TEST_DATA_DIR.name


def pytest_unconfigure(config) -> None:
    """Close application log files, remove test data, and restore the environment."""
    logging.shutdown()
    _TEST_DATA_DIR.cleanup()
    if _ORIGINAL_DATA_DIR is None:
        os.environ.pop("NEU_JWXT_DATA_DIR", None)
    else:
        os.environ["NEU_JWXT_DATA_DIR"] = _ORIGINAL_DATA_DIR
