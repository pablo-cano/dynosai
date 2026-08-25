# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Public package API for DynosAI."""

from .version import __version__
from .engine import DynosAI

__all__ = ["DynosAI", "__version__"]
