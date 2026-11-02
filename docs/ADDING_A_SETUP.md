# Adding a new model setup or new variables

icon-quantize is meant to grow with new ICON configurations, such as other physics
packages, ICON-ART tracers, two-moment microphysics or new diagnostics, and even with
non-ICON models. Usually **no code change is needed**: you only add rules.

## 1. Find what is not covered yet

```bash
icon-quantize --dry-run sample_file.nc | grep DEFAULT
```

Variables marked `DEFAULT` get `sig 6`. That is safe but saves less space than a rule
chosen for the variable.

Look at what they are:

```bash
ncdump -h sample_file.nc | grep -A4 'float <name>'
python - <<'EOF'
import netCDF4 as nc, numpy as np
d = nc.Dataset("sample_file.nc")
for n in ["<name1>", "<name2>"]:
    v = d[n][...].compressed() if np.ma.isMaskedArray(d[n][...]) else np.ravel(d[n][...])
    nz = np.abs(v[v != 0])
    print(n, d[n].units, "min", v.min(), "max", v.max(),
          "smallest |x|>0", nz.min() if nz.size else None, "frac zero", np.mean(v == 0))
EOF
```

## 2. Choose a rule for each variable

| the variable is… | rule | reasoning |
|---|---|---|
| categorical, an index, a flag or a mask (soil type, land-cover class, lsm) | `exact` | any change alters its meaning |
| spanning orders of magnitude (mixing ratios, number concentrations, aerosol and tracer masses, effective radii) | `sig 4` (`sig 5` for tight budgets) | relative precision keeps small values meaningful |
| a fraction or cover used with thresholds (clc, clct, snow cover) | `sig 5` | small values near thresholds survive |
| with a natural absolute resolution (winds, temperature, pressure, fluxes, heights) | `dec N` | choose N from the smallest change that matters: well below observation error, model noise and your analysis needs |
| accumulated (precipitation sums) | `dec 4` in kg m⁻² | accumulations grow; the absolute error stays fixed |
| something you are not sure about | leave it at `sig 6`, or `exact` | the default is safe; `exact` is safest |

Rules of thumb for `dec N`: N decimals in the variable's units, so `dec 3` for a
temperature in K means 1 mK. Keep the error at least 10× below the smallest signal
you'll analyse (perturbations, trends, differences between runs).

## 3. Put the rule in the right place

| scope | where |
|---|---|
| a name that means the same thing in all ICON output | `src/icon_quantize/presets/icon.rules` |
| specific to one configuration, or a name whose meaning differs there | a new preset `src/icon_quantize/presets/<setup>.rules`, used with `--preset icon --preset <setup>` (later presets override earlier ones) |
| only for your own analysis | a rules file outside the package, used with `--rules FILE` |

Presets are discovered automatically: any `presets/*.rules` file is listed by
`icon-quantize --list-presets` and checked by the test suite.

## 4. Validate on real files (never on the only copy)

```bash
icon-quantize -o q_test/ --report [--preset icon --preset <setup>] sample_file.nc
```

Look at the `max abs err`/`max rel err` columns and the size reduction. Open a few
fields side by side, and make sure the analysis you care about (thresholds,
differences, budgets) gives the same answer.

## 5. If you change code

Architecture:

| file | role |
|---|---|
| `rules.py` | rule parsing, presets, `rule_for()` (which rule a variable gets), coordinate detection |
| `core.py` | `round_decimals()`, `round_significant()`, `copy_quantized()`, `check()`, `process_file()` |
| `cli.py` | command line, parallel driver |
| `tests/test_icon_quantize.py` | synthetic ICON-like files; must pass before any release |

**These must always hold**, and the tests check them:
1. The original is never modified or removed before `check()` has passed.
2. Coordinates, non-float, categorical, fill/missing values, NaN and ±Inf are never
   changed.
3. `check()` verifies against the stored rule and stays independent of how the
   rounding was done, so a rounding bug is caught, not hidden.
4. The applied rule is recorded in the `icon_quantize_rule` variable attribute, and
   the run in global `history`.
5. Output stays plain netCDF4, readable without this package.

A new rounding mode (e.g. `bits N`, or a per-level precision) needs four things:
parsing in `parse_rule()`, an implementation in `core.py`, a tolerance branch in
`tolerance_ok()`, and tests with values at all magnitudes, zeros, NaN, Inf and fill
values.

## 6. Release

1. Run `python -m unittest discover -s tests -v`.
2. Bump `__version__` in `src/icon_quantize/__init__.py`: patch for rule tweaks,
   minor for new presets/options, major for a changed default behaviour.
3. Add an entry to `CHANGELOG.md`, including which output each new rule was
   validated on.
4. Build: `python -m build` (or `pip wheel --no-deps .`), then upload with `twine`,
   or add a conda-forge recipe.
