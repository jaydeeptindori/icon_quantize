"""Precision rules: parsing, presets and the per-variable decision.

A rule is a tuple:
  ("dec", N)      absolute: error <= 0.5*10**-N in the variable's units
  ("sig", N)      relative: N significant digits at any magnitude
  ("exact", None) no rounding, lossless compression only

Rules files and presets (presets/*.rules) share one format, one variable per
line, '#' starts a comment, names are case-insensitive:
  qv       sig 4
  w        dec 3
  soiltyp  exact
"""
from importlib import resources

import numpy as np

EXACT = ("exact", None)
DEFAULT_RULE = ("sig", 6)      # unknown float variables (float32 has ~7 digits)
DEFAULT_PRESETS = ("icon",)
FLOAT32_DIGITS = 7             # a 'sig' rule above this keeps float32 data exact
RULE_ATTR = "icon_quantize_rule"  # variable attribute recording the rule applied


def parse_rule(tokens, where=""):
    """['sig', '4'] -> ('sig', 4); raises ValueError with a readable message."""
    tokens = [str(t) for t in tokens]
    kind = tokens[0].lower() if tokens else ""
    try:
        if kind == "exact" and len(tokens) == 1:
            return EXACT
        if kind == "dec" and len(tokens) == 2:
            n = int(tokens[1])
            if -10 <= n <= 20:
                return ("dec", n)
        if kind == "sig" and len(tokens) == 2:
            n = int(tokens[1])
            if 1 <= n <= 15:
                return ("sig", n)
    except ValueError:
        pass
    raise ValueError(f"bad rule {' '.join(tokens)!r} {where}"
                     "(use: dec N with -10<=N<=20 | sig N with 1<=N<=15 | exact)")


def parse_rules_text(text, source="<text>"):
    table = {}
    for i, line in enumerate(text.splitlines(), 1):
        parts = line.split("#", 1)[0].split()
        if parts:
            table[parts[0].lower()] = parse_rule(parts[1:], f"in {source}:{i} ")
    return table


def load_rules(path):
    """Read a user rules file."""
    with open(path) as fh:
        return parse_rules_text(fh.read(), path)


def _preset_dir():
    return resources.files("icon_quantize").joinpath("presets")


def available_presets():
    return sorted(p.name[:-len(".rules")] for p in _preset_dir().iterdir()
                  if p.name.endswith(".rules"))


def load_preset(name):
    path = _preset_dir().joinpath(f"{name}.rules")
    if not path.is_file():
        raise ValueError(f"unknown preset {name!r} (available: {', '.join(available_presets())})")
    return parse_rules_text(path.read_text(), f"preset {name}")


def build_table(presets=DEFAULT_PRESETS, rules_files=(), extra=None):
    """Merge presets, then rules files, then an explicit dict; later entries win."""
    table = {}
    for name in presets:
        table.update(load_preset(name))
    for path in rules_files:
        table.update(load_rules(path))
    if extra:
        table.update({k.lower(): v for k, v in extra.items()})
    return table


def coordinate_names(ds):
    """Dimension coordinates, cell bounds and auxiliary coordinates (clon, clat, ...)."""
    names = set(ds.dimensions)
    for var in ds.variables.values():
        for att in ("coordinates", "bounds"):
            names.update(str(getattr(var, att, "")).split())
    names.update(n for n in ds.variables if n.endswith("_bnds"))
    return names


def rule_for(name, var, coords, settings):
    """Return (rule, source) for one variable of an open netCDF4 dataset."""
    if var.dtype.kind != "f" or var.ndim == 0:
        return EXACT, "not float/scalar"
    if name in coords:
        return EXACT, "coordinate"
    if settings.lossless:
        return EXACT, "lossless"
    if name.lower() in settings.table:
        rule, source = settings.table[name.lower()], "table"
    else:
        rule, source = settings.default, "DEFAULT"
    stored32 = var.dtype == np.float32 or settings.float32
    if rule[0] == "sig" and stored32 and rule[1] >= FLOAT32_DIGITS:
        return EXACT, f"{source}: sig {rule[1]} >= float32 digits"
    return rule, source


def stored_rule(var):
    """Rule recorded in an output variable's attributes (EXACT if none).
    Also understands files written by the earlier nc_quantize.py script."""
    attrs = var.ncattrs()
    if RULE_ATTR in attrs:
        return parse_rule(str(var.getncattr(RULE_ATTR)).split())
    if "least_significant_digit" in attrs:
        return ("dec", int(var.least_significant_digit))
    q = [a for a in attrs if a.startswith("_Quantize")]
    return ("sig", int(var.getncattr(q[0]))) if q else EXACT


def rule_str(rule):
    return rule[0] if rule[1] is None else f"{rule[0]} {rule[1]}"
