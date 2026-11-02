"""Tests on small synthetic ICON-like files. Run with:  python -m unittest -v
(no pytest needed)."""
import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
import netCDF4 as nc

from icon_quantize import Settings, build_table, check, parse_rule, process_file
from icon_quantize.cli import main
from icon_quantize.rules import available_presets, load_preset

FILL = np.float32(-9.99e-08)  # ICON boundary-zone fill value (lmask_boundary)


def make_icon_like(path, dtype="f4"):
    """Unstructured-grid file with coordinates, fill values, NaN, an unknown
    variable, a categorical field, an integer and a scalar variable."""
    rng = np.random.default_rng(1)
    with nc.Dataset(path, "w") as d:
        d.title = "synthetic ICON-like test file"
        d.createDimension("time", None)
        d.createDimension("height", 4)
        d.createDimension("ncells", 500)
        d.createDimension("vertices", 3)
        clon = d.createVariable("clon", "f8", ("ncells",))
        clon.bounds = "clon_bnds"
        clon[:] = rng.uniform(-0.2, 0.3, 500)
        d.createVariable("clon_bnds", "f8", ("ncells", "vertices"))[:] = rng.uniform(-0.2, 0.3, (500, 3))
        d.createVariable("clat", "f8", ("ncells",))[:] = rng.uniform(0.8, 1.0, 500)
        d.createVariable("height", "f8", ("height",))[:] = np.arange(1, 5)
        d.createVariable("time", "f8", ("time",))[:] = [0.0, 1800.0]

        def var(name, values, fill=True, kind=dtype):
            v = d.createVariable(name, kind, ("time", "height", "ncells"),
                                 fill_value=FILL if fill else None)
            if fill:
                v.missing_value = FILL
            v.coordinates = "clon clat"
            v[:] = values
            return v

        shape = (2, 4, 500)
        temp = rng.uniform(220, 310, shape)
        temp[:, :, :20] = FILL                       # masked boundary cells
        temp[1, 3, 400] = np.nan
        var("temp", temp).units = "K"
        qc = 10.0 ** rng.uniform(-30, -2, shape)    # many orders of magnitude
        qc[:, :, 20:60] = 0.0
        qc[:, :, :20] = FILL
        qc[1, 2, 200] = np.nan
        qc[1, 2, 201] = np.inf
        var("qc", qc).units = "kg kg-1"
        unknown = rng.normal(0, 1, shape)
        unknown[0, 0, 100] = np.nan
        unknown[0, 0, 101] = -np.inf
        var("my_new_var", unknown, fill=False)
        var("soiltyp", rng.integers(1, 10, shape).astype(float), fill=False)
        d.createVariable("level_index", "i4", ("height",))[:] = np.arange(4)
        d.createVariable("scalar_const", "f8", ())[...] = 3.14159265358979


def file_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read(path, name):
    with nc.Dataset(path) as d:
        v = d[name]
        v.set_auto_maskandscale(False)
        return np.asarray(v[...]), {a: v.getncattr(a) for a in v.ncattrs()}, v.dtype


class IconQuantizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = os.path.join(self.tmp, "orig.nc")
        make_icon_like(self.orig)
        self.work = os.path.join(self.tmp, "work.nc")
        shutil.copy2(self.orig, self.work)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_inplace_quantization_is_within_rules(self):
        r = process_file(self.work, Settings())
        self.assertTrue(r["ok"], r["lines"])
        self.assertLess(r["after"], r["before"])
        self.assertEqual(r["defaults"], ["my_new_var"])
        ok, _ = check(self.orig, self.work)
        self.assertTrue(ok)
        x, _, _ = read(self.orig, "temp")
        y, attrs, _ = read(self.work, "temp")
        self.assertEqual(attrs["icon_quantize_rule"], "dec 3")
        valid = (x != FILL) & np.isfinite(x)
        self.assertLessEqual(np.abs(x[valid] - y[valid]).max(), 0.5e-3 + 1e-4)

    def test_fill_values_are_never_rounded(self):
        process_file(self.work, Settings())
        for name in ("temp", "qc"):
            x, _, _ = read(self.orig, name)
            y, _, _ = read(self.work, name)
            np.testing.assert_array_equal(x == FILL, y == FILL)

    def test_small_values_keep_relative_precision(self):
        process_file(self.work, Settings())
        x, _, _ = read(self.orig, "qc")
        y, _, _ = read(self.work, "qc")
        sel = (x != FILL) & (x != 0) & np.isfinite(x)
        rel = np.abs(y[sel].astype(float) - x[sel]) / np.abs(x[sel])
        self.assertLessEqual(rel.max(), 0.5e-3 * 1.01)  # sig 4
        np.testing.assert_array_equal(x == 0, y == 0)

    def test_coordinates_categorical_int_scalar_stay_exact(self):
        process_file(self.work, Settings())
        for name in ("clon", "clon_bnds", "clat", "height", "time",
                     "soiltyp", "level_index", "scalar_const"):
            x, _, dx = read(self.orig, name)
            y, _, dy = read(self.work, name)
            self.assertEqual(dx, dy, name)
            self.assertEqual(x.tobytes(), y.tobytes(), name)

    def test_nan_inf_preserved_and_history_added(self):
        r = process_file(self.work, Settings())
        self.assertTrue(r["ok"], r["lines"])
        y, _, _ = read(self.work, "my_new_var")      # sig 6 (default)
        self.assertTrue(np.isnan(y[0, 0, 100]))
        self.assertEqual(y[0, 0, 101], -np.inf)
        y, _, _ = read(self.work, "qc")              # sig 4
        self.assertTrue(np.isnan(y[1, 2, 200]))
        self.assertEqual(y[1, 2, 201], np.inf)
        y, _, _ = read(self.work, "temp")            # dec 3
        self.assertTrue(np.isnan(y[1, 3, 400]))
        with nc.Dataset(self.work) as d:
            self.assertIn("icon-quantize", d.history)
            self.assertEqual(d.title, "synthetic ICON-like test file")

    def test_rerun_is_skipped(self):
        process_file(self.work, Settings())
        r = process_file(self.work, Settings())
        self.assertTrue(r["skipped"])

    def test_lossless_is_bit_identical(self):
        process_file(self.work, Settings(lossless=True))
        with nc.Dataset(self.orig) as a, nc.Dataset(self.work) as b:
            for name in a.variables:
                va, vb = a[name], b[name]
                va.set_auto_maskandscale(False)
                vb.set_auto_maskandscale(False)
                self.assertEqual(np.asarray(va[...]).tobytes(), np.asarray(vb[...]).tobytes(), name)

    def test_check_detects_corruption(self):
        process_file(self.work, Settings())
        with nc.Dataset(self.work, "a") as d:
            v = d["temp"]
            v.set_auto_maskandscale(False)
            v[0, 0, 300] = v[0, 0, 300] + 0.01   # 10x beyond dec 3
        ok, lines = check(self.orig, self.work)
        self.assertFalse(ok)
        self.assertTrue(any("temp" in line for line in lines))

    def test_check_detects_lost_fill_value(self):
        process_file(self.work, Settings())
        with nc.Dataset(self.work, "a") as d:
            v = d["temp"]
            v.set_auto_maskandscale(False)
            v[0, 0, 0] = 0.0                      # fill turned into data
        self.assertFalse(check(self.orig, self.work)[0])

    def test_outdir_keeps_original(self):
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        before = file_bytes(self.work)
        r = process_file(self.work, Settings(), outdir=out)
        self.assertTrue(r["ok"])
        self.assertEqual(file_bytes(self.work), before)
        self.assertTrue(os.path.exists(os.path.join(out, "work.nc")))

    def test_failure_keeps_original_and_leaves_no_tmp(self):
        out = os.path.join(self.tmp, "readonly")
        os.makedirs(out)
        os.chmod(out, 0o500)
        try:
            r = process_file(self.work, Settings(), outdir=out)
        finally:
            os.chmod(out, 0o700)
        self.assertFalse(r["ok"])
        self.assertEqual(os.listdir(out), [])

    def test_float32_option_on_float64_file(self):
        f64 = os.path.join(self.tmp, "f64.nc")
        make_icon_like(f64, dtype="f8")
        ref = os.path.join(self.tmp, "f64_ref.nc")
        shutil.copy2(f64, ref)
        r = process_file(f64, Settings(float32=True))
        self.assertTrue(r["ok"], r["lines"])
        self.assertEqual(read(f64, "temp")[2], np.float32)
        self.assertEqual(read(f64, "clon")[2], np.float64)     # coordinate keeps type
        self.assertEqual(read(f64, "soiltyp")[2], np.float64)  # exact keeps type

    def test_round_significant_all_magnitudes(self):
        from icon_quantize.core import round_significant
        rng = np.random.default_rng(0)
        for dtype, lo in ((np.float32, -37), (np.float64, -300)):
            x = (10.0 ** rng.uniform(lo, 30, 200000)).astype(dtype) * rng.choice([-1, 1], 200000)
            for n in (3, 4, 5, 6):
                y = round_significant(x, n, [])
                rel = np.abs(y.astype(float) - x) / np.abs(x)
                self.assertLessEqual(rel.max(), 0.5 * 10.0 ** (1 - n), (dtype, n))

    def test_rules_override_and_parsing(self):
        table = build_table(extra={"TEMP": ("dec", 1), "my_new_var": ("exact", None)})
        r = process_file(self.work, Settings(table=table))
        self.assertTrue(r["ok"], r["lines"])
        self.assertEqual(r["defaults"], [])
        self.assertEqual(read(self.work, "temp")[1]["icon_quantize_rule"], "dec 1")
        self.assertEqual(parse_rule(["SIG", "4"]), ("sig", 4))
        for bad in (["sig"], ["sig", "0"], ["dec", "x"], ["round", "2"]):
            with self.assertRaises(ValueError):
                parse_rule(bad)

    def test_presets_are_valid(self):
        self.assertIn("icon", available_presets())
        for name in available_presets():
            table = load_preset(name)
            self.assertTrue(table)
            self.assertTrue(all(k == k.lower() for k in table))

    def test_file_selection(self):
        from icon_quantize.cli import collect
        d = os.path.join(self.tmp, "sel")
        os.makedirs(os.path.join(d, "sub"))
        for name in ("a_DOM01.nc", "a_DOM04.nc", "b_DOM04.nc", "notes.txt",
                     "c_DOM04.nc.tmp_quant", "sub/d_DOM04.nc"):
            open(os.path.join(d, name), "w").close()
        names = lambda fs: [os.path.relpath(f, d) for f in fs]
        self.assertEqual(names(collect([d])), ["a_DOM01.nc", "a_DOM04.nc", "b_DOM04.nc"])
        self.assertEqual(names(collect([d], "*_DOM04*")), ["a_DOM04.nc", "b_DOM04.nc"])
        # same file twice (directory + explicit name) is taken once
        self.assertEqual(names(collect([d, os.path.join(d, "a_DOM01.nc")])),
                         ["a_DOM01.nc", "a_DOM04.nc", "b_DOM04.nc"])
        # an explicitly named file is taken even if it does not match the pattern
        self.assertEqual(names(collect([os.path.join(d, "sub", "d_DOM04.nc")], "*DOM01*")),
                         ["sub/d_DOM04.nc"])

    def test_cli_dry_run_and_inplace(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--dry-run", self.work]), 0)
        self.assertIn("my_new_var", buf.getvalue())
        self.assertIn("DEFAULT", buf.getvalue())
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        for i in range(3):
            shutil.copy2(self.orig, os.path.join(run_dir, f"f{i}.nc"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--inplace", "-j", "2", run_dir]), 0)
        for i in range(3):
            self.assertTrue(check(self.orig, os.path.join(run_dir, f"f{i}.nc"))[0])


if __name__ == "__main__":
    unittest.main()
