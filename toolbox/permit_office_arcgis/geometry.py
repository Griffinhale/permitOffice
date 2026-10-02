"""Compatibility re-export for the old geometry module.

The code now lives in proposals.py, city_features.py, and map_layers.py.
Import from those; this module stays for one release.
"""

from __future__ import annotations

from . import city_features, map_layers, proposals

_MODULES = (proposals, city_features, map_layers)


def __getattr__(name):
    for module in _MODULES:
        if name in vars(module):
            return vars(module)[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
