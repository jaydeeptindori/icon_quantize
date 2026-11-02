---
name: icon-quantize-extend
description: Extend the icon-quantize package to a new model setup, new output variables, or new functionality. Use when asked to add support for an ICON configuration (new physics, ICON-ART tracers, two-moment microphysics, new diagnostics, another model), to choose or change precision rules for variables, to add a preset, or to add or modify features of icon-quantize, including a release.
---

# Extending icon-quantize

icon-quantize rounds each netCDF variable to a per-variable precision rule
(`dec N` absolute, `sig N` relative, `exact`) and verifies every file before it
replaces the original. Read `docs/ADDING_A_SETUP.md` first; it is the reference for
everything below.

## What to ask the user for (only what you can't find out yourself)

- Paths to one or two **sample output files** of the new setup. Never work on the
  only copy of their data.
- What the data will be used for: thresholds (e.g. cloud masks), budgets,
  perturbation or difference analyses, comparisons with observations. This decides
  how tight the rules must be.
- Whether the setup's rules belong in the shared `icon` preset, a new preset, or a
  private rules file. Propose an answer using the table in ADDING_A_SETUP.md §3.

## Workflow for a new setup or new variables

1. **Inventory.** `icon-quantize --dry-run <sample>` and list the variables whose
   source is `DEFAULT`. For each one, get units, long_name, min/max, the smallest
   non-zero |value| and the fraction of zeros (snippet in ADDING_A_SETUP.md §1).
2. **Classify each variable** with the rule table in ADDING_A_SETUP.md §2. Reason
   physically:
   - categorical, flag, index or mask → `exact`;
   - spans orders of magnitude → `sig 4`/`sig 5`;
   - used with thresholds → `sig 5` or tighter;
   - natural absolute resolution → `dec N`, with the error at least 10× below the
     smallest signal analysed.

   When unsure, keep `sig 6` or use `exact`, and say so. Present the proposed rules
   to the user as a table (variable, units, range, rule, max error in units, why)
   before writing them.
3. **Write the rules** into the agreed place: `presets/icon.rules`, a new
   `presets/<setup>.rules`, or a user rules file. Use the existing file style: one
   variable per line, grouped with section comments, a units comment on the first
   line of each group, and a header naming the setup and the date it was validated.
4. **Validate on copies:** `icon-quantize -o <tmpdir> --report [--preset ...] <sample>`.
   Every variable must show `ok yes`. Report the max errors and the size reduction.
   Spot-check the user's key analysis quantity, such as a threshold count or a mean
   profile, against the original.
5. **Test and release:** `python -m unittest discover -s tests -v` must pass (the
   suite checks every preset file automatically). Bump `__version__` (patch for rule
   changes, minor for new presets or options), and add a `CHANGELOG.md` entry naming
   the setup and the files used for validation.

## Workflow for new functionality (code changes)

- Know the architecture first: `rules.py` (rules, presets, `rule_for`), `core.py`
  (rounding, `copy_quantized`, `check`, `process_file`) and `cli.py`.
- **Never break these invariants**; each has a test:
  1. The original is not modified or removed before `check()` passes.
  2. Coordinates, non-float, `exact`, fill/missing values, NaN and ±Inf are never
     changed.
  3. `check()` verifies against the stored rule and is independent of the rounding
     implementation.
  4. The rule is recorded in the `icon_quantize_rule` attribute, and the run in
     `history`.
  5. Output is plain netCDF4.
- Don't rely on libnetcdf's quantize filters: libnetcdf 4.9.2's `GranularBitRound`
  turns NaN and Inf into 0. Rounding is done in numpy (`round_decimals`,
  `round_significant`).
- A new rule kind needs four parts: `parse_rule`, a rounding function, a
  `tolerance_ok` branch, and tests with all magnitudes, zeros, subnormals, NaN, Inf,
  fill values, and float32 and float64.
- Add a test for every new behaviour in `tests/test_icon_quantize.py`, using the
  synthetic `make_icon_like()` file (extend it if needed). Use no network and no real
  data in tests.
- Keep the CLI backwards compatible. A change to default behaviour is a major
  version bump, noted prominently in `CHANGELOG.md`.

## Done means

- The tests pass, and the output of the run is shown to the user.
- The real-file validation has every variable `ok yes`, with sizes reported.
- README/docs are updated if behaviour or options changed.
- The version is bumped and the changelog entry written.
- Nothing has been run in place on the user's real data unless they asked for it.
