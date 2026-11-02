"""Command line interface: icon-quantize (or python -m icon_quantize)."""
import argparse
import glob
import os
import sys
from functools import partial
from multiprocessing import Pool

from . import __version__
from .core import TMP_SUFFIX, Settings, describe, process_file
from .rules import DEFAULT_PRESETS, DEFAULT_RULE, available_presets, build_table, parse_rule, rule_str

EPILOG = """\
rule order for each variable (first match wins):
  1. not float / scalar / coordinate (time, height, *_bnds, clon, clat, ...) -> exact
  2. --lossless                                                     -> exact
  3. --rules FILE(s), then --preset(s)                              -> that rule
  4. anything else                                                  -> --default (sig 6)

examples:
  icon-quantize --dry-run out/file.nc            show the rule per variable, write nothing
  icon-quantize --inplace -j 16 out/             replace files after a successful check
  icon-quantize --inplace --pattern '*_DOM04_*.nc' out/   only files whose name matches
  icon-quantize -o out_small/ out/*.nc          write checked copies, keep originals
  icon-quantize --inplace --lossless out/        compression only, bit-identical
  icon-quantize --inplace --rules my.rules out/  own precisions for a new setup
"""


def collect(inputs, pattern="*.nc"):
    """Files to process: each file given is taken as is; each directory
    contributes the files directly inside it that match `pattern` (not
    recursive). Leftover *.tmp_quant files are ignored and a file reached
    twice (e.g. via '.' and its name) is processed only once."""
    files, seen = [], set()
    for p in inputs:
        if os.path.isdir(p):
            found = sorted(f for f in glob.glob(os.path.join(p, pattern)) if os.path.isfile(f))
        elif os.path.isfile(p):
            found = [p]
        else:
            sys.exit(f"error: not found: {p}")
        for f in found:
            real = os.path.realpath(f)
            if not f.endswith(TMP_SUFFIX) and real not in seen:
                seen.add(real)
                files.append(os.path.abspath(f))
    return files


def _work(path, settings, outdir, report):
    return process_file(path, settings, outdir=outdir, report=report)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="icon-quantize",
        description="Shrink ICON netCDF output by rounding each variable to a chosen "
                    "precision and compressing it. Every file is verified before it "
                    "replaces (or is written next to) the original.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*",
                    help="netCDF files and/or directories (files inside matching --pattern)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--inplace", action="store_true", help="replace each file after a successful check")
    mode.add_argument("-o", "--outdir", help="write checked copies here, keep originals")
    mode.add_argument("--dry-run", action="store_true", help="only show the rule per variable")
    mode.add_argument("--list-presets", action="store_true", help="list built-in presets and exit")
    mode.add_argument("--show-rules", action="store_true", help="print the effective rule table and exit")
    ap.add_argument("--pattern", default="*.nc", metavar="GLOB",
                    help="which files to take from a directory input, e.g. '*_DOM04_*.nc' "
                         "(default: '*.nc'; quote it; not recursive)")
    ap.add_argument("--preset", action="append", metavar="NAME",
                    help=f"built-in rule preset(s), later ones override (default: {', '.join(DEFAULT_PRESETS)}; "
                         "'none' for no preset)")
    ap.add_argument("--rules", action="append", default=[], metavar="FILE",
                    help="rules file(s) that extend/override the presets")
    ap.add_argument("--default", nargs="+", metavar="RULE",
                    help=f"rule for variables in no table, e.g. --default sig 5 (default: {rule_str(DEFAULT_RULE)})")
    ap.add_argument("--lossless", action="store_true", help="no rounding at all, compression only")
    ap.add_argument("--float32", action="store_true",
                    help="store rounded float64 variables as float32 (usually gains little)")
    ap.add_argument("--complevel", type=int, default=1, choices=range(1, 10), metavar="1-9",
                    help="deflate level (default 1; higher is slower and barely smaller)")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="files processed in parallel")
    ap.add_argument("--report", action="store_true", help="print the max error of every variable")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    opts = ap.parse_args(argv)
    if not (opts.list_presets or opts.show_rules):
        if not (opts.inplace or opts.outdir or opts.dry_run):
            ap.error("choose one of --inplace, -o/--outdir, --dry-run")
        if not opts.inputs:
            ap.error("no input files or directories given")
    return opts


def make_settings(opts):
    presets = [p for p in (opts.preset or DEFAULT_PRESETS) if p.lower() != "none"]
    try:
        table = build_table(presets, opts.rules)
        default = parse_rule(opts.default, "for --default ") if opts.default else DEFAULT_RULE
    except (OSError, ValueError) as e:
        sys.exit(f"error: {e}")
    return Settings(table=table, default=default, lossless=opts.lossless,
                    float32=opts.float32, complevel=opts.complevel)


def say(*args, **kw):
    """print() that survives a closed pipe (e.g. `icon-quantize ... | head`):
    output is dropped from then on, but processing continues normally."""
    try:
        print(*args, **kw, flush=True)
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())


def main(argv=None):
    opts = parse_args(argv)
    if opts.list_presets:
        say("\n".join(available_presets()))
        return 0
    settings = make_settings(opts)
    if opts.show_rules:
        for name, rule in sorted(settings.table.items()):
            say(f"{name:24s} {rule_str(rule)}")
        say(f"{'(default)':24s} {rule_str(settings.default)}")
        return 0

    files = collect(opts.inputs, opts.pattern)
    if not files:
        sys.exit("error: no .nc files found")
    if opts.dry_run:
        for f in files:
            say(f"== {f}")
            say(f"  {'variable':22s} {'type':>8s} {'MB':>9s}  {'rule':8s} source")
            for name, dtype, mb, rule, source in describe(f, settings):
                say(f"  {name:22s} {dtype:>8s} {mb:9.1f}  {rule:8s} {source}")
        return 0
    if opts.outdir:
        os.makedirs(opts.outdir, exist_ok=True)
        if any(os.path.samefile(os.path.dirname(f), opts.outdir) for f in files):
            sys.exit("error: --outdir is the input directory; use --inplace instead")

    say(f"icon-quantize {__version__}: {len(files)} file(s), {opts.jobs} job(s), "
          f"{'lossless' if opts.lossless else 'quantized'}, "
          f"{'in place' if opts.inplace else '-> ' + opts.outdir}")
    work = partial(_work, settings=settings, outdir=opts.outdir, report=opts.report)
    tot_before = tot_after = n_fail = n_skip = 0
    seen_defaults = set()
    with Pool(max(1, opts.jobs)) as pool:
        for r in pool.imap_unordered(work, files):
            if r["skipped"]:
                n_skip += 1
                say(f"skipped (already processed): {r['path']}")
            elif r["ok"]:
                tot_before += r["before"]
                tot_after += r["after"]
                new = sorted(set(r["defaults"]) - seen_defaults)
                if new:
                    say(f"  note: default ({rule_str(settings.default)}) used for: {', '.join(new)}")
                    seen_defaults.update(new)
                say(f"{r['before']/1e9:8.2f} GB -> {r['after']/1e9:8.2f} GB  {r['path']}")
            else:
                n_fail += 1
                say(f"FAILED, original kept: {r['path']}")
            for line in r["lines"]:
                say(line)

    saved = 100 * (1 - tot_after / tot_before) if tot_before else 0
    say(f"done: {len(files) - n_fail - n_skip} ok, {n_skip} skipped, {n_fail} failed; "
          f"{tot_before/1e9:.2f} GB -> {tot_after/1e9:.2f} GB ({saved:.0f} % saved)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
