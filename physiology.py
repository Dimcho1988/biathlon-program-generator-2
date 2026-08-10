"""Deprecated compatibility import for the canonical physiology module.

The implementation lives only in :mod:`biathlon.physiology`; keeping this
wrapper avoids a second, diverging calculation path for older imports.
"""

from biathlon.physiology import *  # noqa: F401,F403
