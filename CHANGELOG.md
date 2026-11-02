# Changelog

All notable changes. Versions follow semantic versioning: patch for rule tweaks,
minor for new presets/options, major for changed default behaviour.

## 1.0.1 – 2026-11-02

- README: disclaimer added (no warranty, no liability for data loss or inaccurate
  data). No change to the code or to the results.

## 1.0.0 – 2026-10-19

First packaged release, grown out of the `nc_quantize.py` script.

- Rules `dec N` (absolute), `sig N` (relative, any magnitude) and `exact`.
  Variable names are matched case-insensitively.
- Built-in preset `icon` covering ICON NWP and LES output. Validated on:
  - ICON-LES (624 m nest, float32): 3D 2.55 GB → 0.53 GB; 2D 0.44 GB → 0.14 GB;
  - ICON NWP at 2 km, TKE and 2TE turbulence schemes (float64): 2.49 GB → 0.32 GB and
    4.41 GB → 0.74 GB.
- Coordinates (`clon`, `clat`, `*_bnds`, dimension variables), non-float and
  categorical fields, fill/missing values, NaN and ±Inf are never changed.
- Rounding is done in numpy rather than with libnetcdf's quantize filters:
  - netCDF4-python's `least_significant_digit` also rounds fill values (ICON's
    `-9.99e-08` boundary fill would become 0);
  - libnetcdf 4.9.2's `GranularBitRound` turns NaN and Inf into 0.
- Every file is verified against the original before it replaces it (`--inplace`) or
  is written (`-o`). Failures keep the original untouched.
- Rules files (`--rules`), presets (`--preset`, `--list-presets`, `--show-rules`),
  `--dry-run`, `--lossless`, `--float32`, `--report`, parallel `-j`.
- File selection: file names as given; directories contribute the files matching
  `--pattern` (default `*.nc`, not recursive). Duplicates are removed.
- netCDF3 input (e.g. ICON `filetype = 4`) is accepted and written as compressed
  netCDF4.
- The applied rule is stored in the `icon_quantize_rule` attribute, and the run in
  `history`. Files that were already processed are skipped.
