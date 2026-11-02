#!/bin/bash
# Process one ICON output directory in place on a single compute node.
#   sbatch slurm_job.sh <output_directory> [extra icon-quantize options]
# Set account, partition and time limit for your HPC system.
#SBATCH --job-name=icon-quantize
#SBATCH --account=YOUR_ACCOUNT
#SBATCH --partition=YOUR_PARTITION
#SBATCH --nodes=1
#SBATCH --time=08:00:00
#SBATCH --output=icon-quantize.%j.log
set -euo pipefail

OUTDIR=${1:?usage: sbatch slurm_job.sh <output_directory> [options]}
shift

# activate the Python environment where icon-quantize is installed here

# the work is I/O bound: 16-32 parallel files is usually a good start
icon-quantize --inplace -j 16 "$@" "$OUTDIR"
