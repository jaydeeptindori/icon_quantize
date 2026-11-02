# icon-quantize

Shrink ICON model netCDF output by **4–8×** by keeping only the precision each variable
actually needs, and compressing it. **Every file is checked against the original
before it is accepted.**

ICON writes every value with ~7 significant digits (float32) or ~16 (float64), and
uncompressed. Most of those digits are noise far below the model's own accuracy.
icon-quantize rounds each variable to a precision you choose: for example 1 mm/s for
winds, 1 mK for temperature, or 4 significant digits for mixing ratios at any
magnitude. It then stores the result with lossless netCDF4 compression. The files stay
ordinary netCDF: xarray, CDO, NCO, ncview and Python read them directly, with no
unpacking step.

| ICON output file (tested) | original | icon-quantize | lossless only |
|---|---|---|---|
| ICON-LES 3D, 624 m nest (float32) | 21.4 GB | ~5.6 GB | 10.2 GB |
| ICON-LES 3D, parent domain (float32) | 2.55 GB | 0.53 GB | – |
| ICON-D2 2 km, all fields (float64) | 2.49 GB | 0.32 GB | – |
| ICON-LES 2D surface fields | 0.44 GB | 0.14 GB | 0.25 GB |

**Disclaimer.** `--inplace` permanently replaces the original files, and rounded digits
cannot be recovered. Keep a copy of data you can't regenerate, and test on a sample
first. This software is provided "as is", without warranty of any kind, and the authors
accept no liability for data loss or inaccurate data, to the extent permitted by law.
See sections 15 and 16 of the [LICENSE](LICENSE).

## Install

```bash
pip install icon-quantize        # once published
# or from a checkout of this folder:
pip install .                    # add -e for a development install
```

Requires Python ≥ 3.9, numpy and netCDF4 ≥ 1.6. All rounding is done in the package
itself, so no particular libnetcdf version is needed.

## Quick start

```bash
# 1. see which precision every variable would get (writes nothing)
icon-quantize --dry-run out/my_file.nc

# 2. process a whole output directory in place, 16 files in parallel
icon-quantize --inplace -j 16 out/

# 3. or write checked copies somewhere else and keep the originals
icon-quantize -o out_small/ out/*.nc

# 4. compression only, data bit-identical
icon-quantize --inplace --lossless out/
```

### Which files are taken

- **A file name** is processed as given.
- **A directory** contributes the files directly inside it that match `--pattern`
  (default `*.nc`). It is not recursive, so subdirectories are not searched.
- Leftover `*.tmp_quant` files are ignored, and a file reached twice (e.g. `.` plus
  its own name) is processed once.

```bash
icon-quantize --inplace .                                  # all *.nc in the current directory
icon-quantize --inplace --pattern '*_DOM04_*.nc' out/      # only names that match, inside out/
icon-quantize --inplace out/*_20220801T1*.nc               # same idea, expanded by the shell
icon-quantize --dry-run --pattern '*3dvar*' out/ | grep '^=='   # list what would be taken
```

Quote the `--pattern` value, so that icon-quantize expands it and not the shell. This
also avoids "argument list too long" errors with many thousands of files.
`*`, `?` and `[...]` work as in the shell.

`python -m icon_quantize ...` works as well. `icon-quantize --help` lists all options.

## Command reference

All of these work from any directory. `file.nc`, `out/` and `my.rules` stand for your
own file, output directory and rules file.

### Install and check

```bash
pip install -e .                                       # from a checkout of this folder, into the active environment
icon-quantize --version                                # print the installed version
icon-quantize --help                                   # all options
python -m icon_quantize ...                            # same as icon-quantize
```

### Inspect without writing anything

```bash
icon-quantize --dry-run file.nc                        # rule each variable would get
icon-quantize --dry-run .                              # all *.nc in the current directory
icon-quantize --dry-run . | grep -c '^=='              # count the selected files
icon-quantize --dry-run --pattern '*_DOM04*.nc' . | grep '^=='     # list the selected files
icon-quantize --dry-run --rules my.rules --pattern '*3dvar*' . | grep -E '^==|ninact'
icon-quantize --dry-run file.nc | grep DEFAULT         # variables not in the preset
icon-quantize --list-presets                           # built-in presets
icon-quantize --show-rules                             # effective rule table
icon-quantize --show-rules --rules my.rules            # table merged with your rules
```

### Single file

```bash
icon-quantize --inplace file.nc                        # replace with a checked, smaller file
icon-quantize --inplace --report file.nc               # also print the max error per variable
icon-quantize --inplace --rules my.rules --report file.nc
icon-quantize -o iq_test/ --report file.nc             # keep the original, write a copy elsewhere
```

### Whole directory or selected files

```bash
icon-quantize --inplace .                              # all *.nc in the current directory
icon-quantize --inplace -j 16 out/                     # 16 files in parallel
icon-quantize --inplace --pattern '*_DOM04_*.nc' out/  # only names matching the pattern
icon-quantize --inplace --pattern '*_20220801T*' .     # one day
icon-quantize --inplace --pattern '*T120000Z.nc' .     # only 12 UTC
icon-quantize --inplace out/*_DOM04_*.nc               # same idea, expanded by the shell
icon-quantize -o out_small/ out/*.nc                   # checked copies, originals kept
```

### Options for precision

```bash
icon-quantize --inplace --rules my.rules out/          # your own rules (e.g. "ninact sig 4")
icon-quantize --inplace --preset none --rules all.rules out/   # ignore the built-in preset
icon-quantize --inplace out/ --default sig 5           # rule for unknown variables
icon-quantize --inplace --lossless out/                # compression only, bit-identical
icon-quantize --inplace --float32 out/                 # store rounded float64 as float32
icon-quantize --inplace --complevel 4 out/             # deflate level (default 1)
```

Give the input before `--default`, as shown: `--default` takes all the words after it.

For compute-node commands, see [Use on an HPC system](#use-on-an-hpc-system-slurm).

## How precision is chosen

Each variable gets one **rule**:

| rule | meaning | typical use |
|---|---|---|
| `dec N` | absolute: error ≤ 0.5·10⁻ᴺ in the variable's units | u, v, w, T, p, fluxes, precipitation |
| `sig N` | relative: N significant digits, error ≤ 0.5·10¹⁻ᴺ of the value, at any magnitude (10¹ … 10⁻³⁸) | mixing ratios, number concentrations, cloud cover |
| `exact` | never rounded, lossless compression only | categorical fields, anything you need bit-exact |

The first matching step decides:

1. not floating point, scalar, or a **coordinate** (dimension variables, `*_bnds`, and
   anything named in a `coordinates` or `bounds` attribute, such as `clon`, `clat`,
   `clon_bnds`) → `exact`
2. `--lossless` → `exact`
3. your `--rules FILE`(s), then the built-in **preset** (`icon`, see
   `src/icon_quantize/presets/icon.rules`) → that rule
4. anything else → `--default` (`sig 6`, i.e. ≤ 5·10⁻⁶ relative). The run prints a
   note listing these variables, so nothing is rounded without you seeing it.

Names are matched case-insensitively, so `QV` (ICON NWP) and `qv` (ICON-LES) share
one rule.

### Your own precisions

Write a rules file (one variable per line, `#` starts a comment):

```text
# my_setup.rules
w          dec 4      # 0.1 mm/s for LES vertical velocity
qv         sig 5
tracer_01  sig 3
lsm        exact
```

```bash
icon-quantize --inplace --rules my_setup.rules out/
icon-quantize --show-rules --rules my_setup.rules    # print the merged table
icon-quantize --inplace --preset none --rules all_my_rules.rules out/   # ignore the built-in preset
```

## What is guaranteed

- **Originals are safe.** Each file is written to `FILE.nc.tmp_quant` next to its
  target. It is then re-read and compared variable by variable with the original:
  structure, dimensions, attributes, fill-value positions, NaN/Inf, and every value
  within its rule. Only then does it replace the original (`--inplace`) or appear in
  `--outdir`. On any failure, such as a failed check, disk quota or a read error, the
  original is left untouched and the temporary file is removed.
- **Never rounded:** coordinates, integer and categorical fields, fill and missing
  values (e.g. ICON's `-9.99e-08` boundary-zone fill from `lmask_boundary`), NaN and
  ±Inf.
- **Traceable:** each rounded variable carries an `icon_quantize_rule` attribute
  (e.g. `"sig 4"`), and the file's `history` attribute records the run.
- **Rerun-safe:** files that were already processed are skipped (see below).
- File timestamps and permissions are kept.

## How a file is processed

The original is never copied. It is read once, and a new, smaller file is written
next to it:

```text
for each selected file (N at a time with -j N):

  1. read  file.nc  --round + compress-->  write file.nc.tmp_quant  (same directory)
       the original is only read, never modified

  2. check: re-read both files and compare every variable
       (rule tolerance, fill values, NaN/Inf, attributes, dimensions)

  3a. check passed -> rename file.nc.tmp_quant to file.nc
                      (one atomic step: the old file is replaced and its space freed)
  3b. check failed -> delete file.nc.tmp_quant; file.nc stays exactly as it was
```

- **Extra disk space** is only the *compressed* size of the files in progress: at
  most N temporary files with `-j N`. Each file's space is freed as soon as it is
  done, so a directory shrinks step by step.
- **The temporary file sits in the same directory** because only a rename within
  one file system is atomic. The original is therefore either complete or already
  replaced by a checked file, never half-written.
- **With `-o OUTDIR`** the originals are never touched. Checked files are created in
  `OUTDIR`, which needs room for all of them.

## Reruns and interrupted jobs

Before working on a file, icon-quantize reads only its header (a fraction of a
second) and looks for its own marker:

```text
for each selected file:
    "icon-quantize" in the global history attribute,
    or any variable with an icon_quantize_rule attribute?
        yes -> "skipped (already processed)"     nothing is read or written
        no  -> round + compress -> check -> replace
```

- **The marker only exists in a finished, checked file.** It is written into the
  new file, and that file replaces the original only after the check has passed.
- **An interrupted file is simply redone.** If a job stops mid-file (time limit,
  crash, cancellation), the original is still the untouched old file without the
  marker, so the next run processes it from the start.
- **Leftover `*.tmp_quant` files** are never taken as input. They are overwritten
  and removed when their file is redone, or you can delete them by hand.
- **To continue, run the same command again.** Nothing needs to be listed or
  tracked. The log shows finished files as `skipped (already processed)` and ends
  with a summary such as `done: 120 ok, 220 skipped, 0 failed`.

Two things to know:

1. **Only icon-quantize's own marker counts.** A file compressed some other way (e.g.
   with `nccopy -d`) has no marker. It is processed: rounded and compressed further.
2. **A finished file is never redone,** even with different rules. To apply new
   rules, start again from the original, uncompressed file.

## Good to know

- **Thresholds.** Rounding can move a value across an analysis threshold only if it
  lies within the rule's error of that threshold. Cloud cover therefore uses `sig 5`
  in the `icon` preset. Check fields you threshold on and tighten them with a rules
  file if needed.
- **float64 output** (`lnetcdf_flt64_output = .true.`). Rounded float64 compresses
  almost as well as float32, so `--float32` usually saves only ~2 %. It's still
  available.
- **Disk space.** Processing needs room for one compressed copy per running job. On a
  file system that is over quota, every file fails safely and nothing gets smaller.
  Free some space first.
- **Speed.** About 0.5–1 min per GB of input per job, including the check (measured
  on a parallel file system). Use `-j` on a compute node. Data is processed in slabs
  of at most 50 million values, so memory stays at a few GB per job, even for huge
  3D fields.
- **Derived fields.** Recomputing a quantity (e.g. density from p and T) from rounded
  fields carries the rounding errors forward.

## Use on an HPC system (SLURM)

Run these from inside the output directory. Replace `YOUR_ACCOUNT` and
`YOUR_PARTITION` with the values for your system, and submit from a shell where the
environment with icon-quantize is active.

```bash
sbatch -A YOUR_ACCOUNT -p YOUR_PARTITION -N 1 -t 08:00:00 -o iq_%j.log \
  --wrap "icon-quantize --inplace -j 16 ."

sbatch -A YOUR_ACCOUNT -p YOUR_PARTITION -N 1 -t 08:00:00 -o iq_%j.log \
  --wrap "icon-quantize --inplace -j 16 --rules my.rules ."

sbatch -A YOUR_ACCOUNT -p YOUR_PARTITION -N 1 -t 08:00:00 -o iq_%j.log \
  --wrap "icon-quantize --inplace -j 16 --pattern '*_DOM04*.nc' ."

# one big file
sbatch -A YOUR_ACCOUNT -p YOUR_PARTITION -N 1 -t 02:00:00 -o iq_%j.log \
  --wrap "icon-quantize --inplace --rules my.rules --report file.nc"

tail -f iq_*.log                                       # follow progress
```

Or edit the account and partition in the package's example script, then run it from
this folder:

```bash
sbatch examples/slurm_job.sh <output_directory>
```

## Python API

```python
from icon_quantize import Settings, build_table, process_file, check

settings = Settings(table=build_table(presets=["icon"], rules_files=["my_setup.rules"]))
result = process_file("out/file.nc", settings)                # in place, checked
result = process_file("out/file.nc", settings, outdir="small/")
ok, lines = check("orig.nc", "small/orig.nc", report=True)    # verify any pair
```

## Extending to a new setup

See [`docs/ADDING_A_SETUP.md`](docs/ADDING_A_SETUP.md). An AI-assistant prompt for
this task is in [`docs/MAINTAINER_PROMPT.md`](docs/MAINTAINER_PROMPT.md). A ready-made
Claude Code skill is in `.claude/skills/icon-quantize-extend/`: run Claude Code in
this folder and ask it to "add support for <setup>".

## Development

```bash
pip install -e .
python -m unittest discover -s tests -v      # no pytest needed
```

## Author

Dr. Jaydeep Singh (GUF), <tindori.jaydeep@gmail.com>

## License

Copyright (C) 2026 Dr. Jaydeep Singh.
GNU General Public License v3.0 or later. See [`LICENSE`](LICENSE).

Provided "as is", without warranty of any kind and without liability for data loss or
inaccurate data, to the extent permitted by law (sections 15 and 16 of the license).
