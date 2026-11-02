"""icon-quantize: shrink ICON netCDF output by verified precision reduction.

Copyright (C) 2026  Dr. Jaydeep Singh (GUF) <tindori.jaydeep@gmail.com>
Licensed under the GNU General Public License v3 or later; see LICENSE.
"""
__version__ = "1.0.1"

from .rules import (  # noqa: E402
    DEFAULT_RULE,
    EXACT,
    available_presets,
    build_table,
    load_preset,
    load_rules,
    parse_rule,
)
from .core import Settings, check, copy_quantized, describe, process_file  # noqa: E402

__all__ = [
    "__version__",
    "DEFAULT_RULE",
    "EXACT",
    "Settings",
    "available_presets",
    "build_table",
    "check",
    "copy_quantized",
    "describe",
    "load_preset",
    "load_rules",
    "parse_rule",
    "process_file",
]
