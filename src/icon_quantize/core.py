"""Quantized copy, verification and the per-file driver."""
import itertools
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import netCDF4 as nc

from . import __version__
from .rules import (DEFAULT_RULE, EXACT, RULE_ATTR, build_table, coordinate_names,
                    rule_for, rule_str, stored_rule)

TMP_SUFFIX = ".tmp_quant"
SLAB = 50_000_000  # max number of values read/written at once (memory control)
CAST_ATTRS = ("missing_value", "valid_min", "valid_max", "valid_range")
HISTORY_TAGS = ("icon-quantize", "nc_quantize.py")  # nc_quantize.py = pre-package script


@dataclass
class Settings:
    """How files are processed. 'table' maps lower-case variable names to rules."""
    table: dict = field(default_factory=build_table)
    default: tuple = DEFAULT_RULE
    lossless: bool = False
    float32: bool = False
    complevel: int = 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def slabs(shape):
    """Index tuples that cover a variable in pieces of at most ~SLAB values."""
    k = 1 if len(shape) > 1 else 0
    while k < len(shape) - 1 and math.prod(shape[k:]) > SLAB:
        k += 1
    for idx in itertools.product(*(range(n) for n in shape[:k])):
        yield idx + (Ellipsis,)


def fill_of(var):
    for att in ("_FillValue", "missing_value"):
        if att in var.ncattrs():
            return np.asarray(var.getncattr(att)).ravel()[0]
    return None


def out_dtype(var, rule, settings):
    if settings.float32 and rule[0] != "exact" and var.dtype == np.float64:
        return np.dtype(np.float32)
    return var.dtype


def _protected(x, fills):
    """Values that must never change: NaN, +-Inf and fill/missing values."""
    keep = ~np.isfinite(x)
    for f in fills:
        keep |= x == np.asarray(f).astype(x.dtype)
    return keep


def round_decimals(data, n, fills):
    """Round to n decimals: values snap to multiples of 2**-ceil(log2(10**n))
    (same algorithm as netCDF4's least_significant_digit, which would however
    also round e.g. ICON's -9.99e-08 boundary fill value to 0)."""
    scale = 2.0 ** np.ceil(np.log2(10.0 ** n))
    x = np.asarray(data, dtype=np.float64)
    with np.errstate(invalid="ignore", over="ignore"):
        out = np.around(scale * x) / scale
    keep = _protected(x, fills)
    out[keep] = x[keep]
    return out


def mantissa_bits(n):
    """Mantissa bits that guarantee n significant decimal digits:
    rounding error <= 2**-(k+1) <= 0.5*10**(1-n) relative."""
    return int(math.ceil((n - 1) * math.log2(10)))


def round_significant(data, n, fills):
    """Bit rounding (round to nearest, ties to even) of the mantissa to keep n
    significant digits at any magnitude. Done here instead of in libnetcdf,
    whose GranularBitRound (4.9.2) turns NaN and Inf into 0."""
    x = np.array(data, copy=True)
    if x.dtype == np.float32:
        uint, mbits = np.uint32, 23
    else:
        x = x.astype(np.float64)
        uint, mbits = np.uint64, 52
    drop = mbits - mantissa_bits(n)
    if drop <= 0:
        return x
    bits = x.view(uint)
    half_minus_1 = uint((1 << (drop - 1)) - 1)
    mask = uint(~((1 << drop) - 1) & np.iinfo(uint).max)
    rounded = ((bits + half_minus_1 + ((bits >> uint(drop)) & uint(1))) & mask).view(x.dtype)
    keep = _protected(x, fills)
    rounded[keep] = x[keep]
    return rounded


# ---------------------------------------------------------------------------
# copy with quantization
# ---------------------------------------------------------------------------
def copy_quantized(src_path, dst_path, settings):
    """Write a quantized/compressed copy of src_path to dst_path.
    Returns the names of variables that fell back to settings.default."""
    defaults = []
    with nc.Dataset(src_path) as src, nc.Dataset(dst_path, "w", format="NETCDF4") as dst:
        if src.groups:
            raise ValueError("files with netCDF groups are not supported")
        gattrs = {a: src.getncattr(a) for a in src.ncattrs()}
        mode = "lossless" if settings.lossless else "quantized"
        stamp = (f"{time.strftime('%Y-%m-%dT%H:%M:%S')}: icon-quantize v{__version__} "
                 f"({mode}, deflate {settings.complevel} + shuffle)")
        gattrs["history"] = stamp + ("\n" + str(gattrs["history"]) if "history" in gattrs else "")
        dst.setncatts(gattrs)
        for name, dim in src.dimensions.items():
            dst.createDimension(name, None if dim.isunlimited() else len(dim))

        coords = coordinate_names(src)
        for name, var in src.variables.items():
            rule, source = rule_for(name, var, coords, settings)
            if source == "DEFAULT":
                defaults.append(name)
            dtype = out_dtype(var, rule, settings)
            attrs = {a: var.getncattr(a) for a in var.ncattrs()}
            fill = attrs.pop("_FillValue", None)
            if dtype != var.dtype:
                for att in CAST_ATTRS:
                    if att in attrs:
                        attrs[att] = np.asarray(attrs[att]).astype(dtype)
            chunk = var.chunking()
            kw = dict(zlib=True, complevel=settings.complevel, shuffle=True,
                      chunksizes=None if chunk == "contiguous" else chunk,
                      fill_value=None if fill is None else np.asarray(fill).astype(dtype))
            out = dst.createVariable(name, dtype, var.dimensions, **kw)
            out.setncatts(attrs)
            if rule[0] != "exact":
                out.setncattr(RULE_ATTR, rule_str(rule))
                if rule[0] == "dec":  # netCDF4-python convention, understood by other tools
                    out.least_significant_digit = np.int32(rule[1])
            fills = [np.asarray(f).ravel()[0] for f in (fill, attrs.get("missing_value"))
                     if f is not None]
            var.set_auto_maskandscale(False)
            out.set_auto_maskandscale(False)
            if var.ndim == 0:
                out.assignValue(var.getValue())
                continue
            for idx in slabs(var.shape):
                data = np.asarray(var[idx]).astype(dtype, copy=False)
                if rule[0] == "dec":
                    data = round_decimals(data, rule[1], fills).astype(dtype)
                elif rule[0] == "sig":
                    data = round_significant(data, rule[1], fills)
                out[idx] = data
    return defaults


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------
def split_special(x, y):
    """NaN/Inf must be identical in x and y. Returns (ok, finite x, finite y)."""
    nan_x, inf_x = np.isnan(x), np.isinf(x)
    ok = (np.array_equal(nan_x, np.isnan(y)) and np.array_equal(inf_x, np.isinf(y))
          and np.array_equal(x[inf_x], y[inf_x]))
    finite = ~(nan_x | inf_x)
    return ok, x[finite], y[finite]


def tolerance_ok(rule, x, y, dtype):
    """True if finite y deviates from finite x by no more than the rule allows."""
    if x.size == 0:
        return True
    d = np.abs(x - y)
    kind, n = rule
    if kind == "exact":
        return not d.any()
    if kind == "dec":
        # rounding bound plus the storage-type spacing at the largest value
        return d.max() <= 0.5 * 10.0 ** -n + np.spacing(dtype.type(np.abs(x).max()))
    tiny = np.finfo(dtype).tiny
    big = np.abs(x) >= tiny
    rel_ok = not big.any() or (d[big] / np.abs(x[big])).max() <= 1.01 * 0.5 * 10.0 ** (1 - n)
    return rel_ok and (big.all() or d[~big].max() <= tiny)  # zeros/subnormals: absolute


def attrs_equal(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return False
    if a.dtype.kind == "f" or b.dtype.kind == "f":  # float64 -> float32 cast attributes
        return np.allclose(a.astype(float), b.astype(float), rtol=1e-6, equal_nan=True)
    return np.array_equal(a, b)


def check(src_path, dst_path, report=False):
    """Verify dst against src: structure, attributes, fill-value positions and
    every value within its stored rule. Returns (ok, report_lines)."""
    lines, ok_all = [], True
    with nc.Dataset(src_path) as a, nc.Dataset(dst_path) as b:
        ga = {k: a.getncattr(k) for k in a.ncattrs() if k != "history"}
        gb = {k: b.getncattr(k) for k in b.ncattrs() if k != "history"}
        if ga.keys() != gb.keys() or not all(attrs_equal(ga[k], gb[k]) for k in ga):
            return False, ["  global attributes differ"]
        if ({k: len(d) for k, d in a.dimensions.items()}
                != {k: len(d) for k, d in b.dimensions.items()}):
            return False, ["  dimensions differ"]
        if set(a.variables) != set(b.variables):
            return False, ["  variable lists differ"]
        if report:
            lines.append(f"  {'variable':22s} {'rule':>8s} {'type':>8s} "
                         f"{'max abs err':>12s} {'max rel err':>12s}  ok")
        for name, va in a.variables.items():
            vb = b[name]
            rule = stored_rule(vb)
            aa = {k: va.getncattr(k) for k in va.ncattrs()}
            ab = {k: vb.getncattr(k) for k in vb.ncattrs()
                  if k not in (RULE_ATTR, "least_significant_digit")}
            ok = (va.dimensions == vb.dimensions and va.shape == vb.shape
                  and aa.keys() == ab.keys() and all(attrs_equal(aa[k], ab[k]) for k in aa))
            va.set_auto_maskandscale(False)
            vb.set_auto_maskandscale(False)
            fa, fb = fill_of(va), fill_of(vb)
            amax = rmax = 0.0
            if ok and va.ndim == 0:
                ok = attrs_equal(va.getValue(), vb.getValue())
            elif ok and va.dtype.kind != "f":
                ok = all(np.array_equal(va[i], vb[i]) for i in slabs(va.shape))
            elif ok:
                for idx in slabs(va.shape):
                    x = np.asarray(va[idx], dtype=np.float64)
                    y = np.asarray(vb[idx], dtype=np.float64)
                    if fa is not None:  # fill values must stay fill values; not compared
                        fx = x == np.float64(fa)
                        if not np.array_equal(fx, y == np.float64(fb)):
                            ok = False
                            break
                        x, y = x[~fx], y[~fx]
                    special_ok, x, y = split_special(x, y)
                    ok = ok and special_ok and tolerance_ok(rule, x, y, vb.dtype)
                    if x.size == 0:
                        continue
                    d = np.abs(x - y)
                    amax = max(amax, float(d.max()))
                    nz = x != 0
                    if nz.any():
                        rmax = max(rmax, float((d[nz] / np.abs(x[nz])).max()))
            ok_all = ok_all and ok
            if report or not ok:
                lines.append(f"  {name:22s} {rule_str(rule):>8s} {str(vb.dtype):>8s} "
                             f"{amax:12.3g} {rmax:12.3g}  {'yes' if ok else 'NO'}")
    return ok_all, lines


# ---------------------------------------------------------------------------
# per-file driver
# ---------------------------------------------------------------------------
def already_processed(path):
    with nc.Dataset(path) as ds:
        history = str(getattr(ds, "history", ""))
        if any(tag in history for tag in HISTORY_TAGS):
            return True
        return any(stored_rule(v) != EXACT for v in ds.variables.values())


def process_file(path, settings, outdir=None, report=False):
    """Quantize one file. outdir=None replaces the file in place (only after a
    successful check); otherwise a checked copy is written to outdir.
    Never raises: returns a dict with ok/skipped/before/after/defaults/lines."""
    res = dict(path=path, ok=False, skipped=False, before=os.path.getsize(path),
               after=0, defaults=[], lines=[])
    try:
        if already_processed(path):
            res.update(ok=True, skipped=True)
            return res
        target = path if outdir is None else os.path.join(outdir, os.path.basename(path))
        tmp = target + TMP_SUFFIX
        try:
            res["defaults"] = copy_quantized(path, tmp, settings)
            ok, res["lines"] = check(path, tmp, report=report)
            if not ok:
                raise RuntimeError("check against the original failed")
            st = os.stat(path)
            os.chmod(tmp, st.st_mode & 0o7777)
            os.utime(tmp, (st.st_atime, st.st_mtime))  # keep the original time stamp
            os.replace(tmp, target)
            res.update(ok=True, after=os.path.getsize(target))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    except Exception as e:  # e.g. disk quota exceeded, unreadable file
        res["lines"].append(f"  ERROR: {e}")
    return res


def describe(path, settings):
    """Rows (name, out dtype, MB, rule, source) without writing anything."""
    rows = []
    with nc.Dataset(path) as ds:
        coords = coordinate_names(ds)
        for name, var in ds.variables.items():
            rule, source = rule_for(name, var, coords, settings)
            mb = var.dtype.itemsize * math.prod(var.shape) / 1e6
            rows.append((name, str(out_dtype(var, rule, settings)), mb, rule_str(rule), source))
    return rows
