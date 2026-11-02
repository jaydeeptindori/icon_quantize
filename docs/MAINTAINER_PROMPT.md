# Prompt for extending icon-quantize with an AI assistant

Copy the block below into any AI coding assistant that can read and run code in this
repository, and fill in the bracketed parts. With Claude Code you can instead run it
in this folder and say "add support for <setup>": it uses the skill in
`.claude/skills/icon-quantize-extend/`, which contains the same instructions.

---

```text
You are maintaining the Python package icon-quantize (this repository). It shrinks
ICON netCDF output by rounding each variable to a per-variable precision rule
("dec N" = absolute error <= 0.5*10^-N, "sig N" = N significant digits at any
magnitude, "exact" = never rounded) and verifies each file against the original
before replacing it. Read README.md and docs/ADDING_A_SETUP.md first and follow them.

Task: [add support for <model / ICON configuration>]
      [and/or: add the feature <description>]

Sample output files (work on copies only, never modify these):
  [path/to/sample_1.nc]
  [path/to/sample_2.nc]

How the data will be used (decides how tight the rules must be):
  [e.g. cloud masks with clct > 0.05 %, boundary-layer budgets, run-to-run
   differences of T of ~0.1 K, comparison with radiosondes]

Steps:
1. Run `icon-quantize --dry-run` on the samples and list every variable whose
   source is DEFAULT, with units, long_name, value range, the smallest non-zero |x|
   and the fraction of zeros.
2. Propose a rule for each (table: variable, units, range, rule, max error in
   units, reasoning) using the guidance in docs/ADDING_A_SETUP.md §2. Use exact for
   categorical/flag/index fields, sig for fields spanning orders of magnitude or
   used with thresholds, and dec where an absolute resolution is natural. Wait for
   my confirmation.
3. Add the confirmed rules to [presets/icon.rules | a new presets/<setup>.rules |
   my rules file], following the existing file style.
4. Validate: `icon-quantize -o <tmpdir> --report ...` on the samples. Every
   variable must pass. Report the max errors and the size reduction.
5. For code changes: keep the invariants listed in docs/ADDING_A_SETUP.md §5, add
   tests for every new behaviour (synthetic data only), and do not use libnetcdf's
   quantize filters (they turn NaN/Inf into 0).
6. Run `python -m unittest discover -s tests -v`, bump the version, and add a
   CHANGELOG.md entry.
Report what you changed, the test output and the validation table.
```
