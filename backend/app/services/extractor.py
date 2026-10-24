"""Backwards-compatible entry point: the extraction engine lives in the ``app.services.extraction`` package."""

from app.services.extraction import *  # noqa: F403
from app.services.extraction import __all__  # noqa: F401
