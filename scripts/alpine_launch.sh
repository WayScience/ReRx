#!/usr/bin/env bash
# ReRx Alpine pilot launcher.
#
# Run ON Persistence1, inside tmux (skill pattern):
#     module load nextflow/25.10.2
#     bash scripts/alpine_launch.sh
#
# Layout:
#   repo:     /scratch/alpine/dabu57888@xsede.org/rerx/ReRx
#   venv:     /scratch/alpine/dabu57888@xsede.org/rerx/ReRx/.venv (uv sync)
#   sif:      /scratch/alpine/dabu57888@xsede.org/rerx/cellprofiler.sif
#   durable:  /pl/active/koala/ReRx
#
# Pass RERX_RESUME=1 to add `-resume` (only reruns tasks whose inputs
# changed since the last run in the same -work-dir; safe default is off
# so a fresh RERX_RUN_ID always starts clean).
set -euo pipefail

RERX_ROOT="/scratch/alpine/dabu57888@xsede.org/rerx"
REPO="${RERX_ROOT}/ReRx"
VENV="${REPO}/.venv"
SIF="${RERX_ROOT}/cellprofiler.sif"
KOALA_RUN="/pl/active/koala/ReRx/runs/${RERX_RUN_ID:-pilot-dev}"
LAUNCH_DIR="${RERX_ROOT}/launch/${RERX_RUN_ID:-pilot-dev}"

# 1. Python venv pinned exactly to the repo's uv.lock (reproducible: the
# same lockfile that passes local tests/CI runs on Alpine, no drift from
# a hand-maintained package list).
if [ ! -x "${VENV}/bin/python" ]; then
    cd "${REPO}"
    uv sync --frozen
    cd -
fi

# 2. CellProfiler sif (built once from the pinned def).
if [ ! -f "${SIF}" ]; then
    cd "${RERX_ROOT}"
    apptainer build cellprofiler.sif "${REPO}/containers/cellprofiler.def"
    cd -
fi

# 3. Durable run directory.
mkdir -p "${KOALA_RUN}" "${LAUNCH_DIR}"

# 4. Launch Nextflow from the launch dir (keeps work/ per-run).
cd "${LAUNCH_DIR}"
export NXF_HOME="${RERX_ROOT}/nextflow_home"
mkdir -p "${NXF_HOME}"

RESUME_FLAG=()
if [ "${RERX_RESUME:-0}" = "1" ]; then
    RESUME_FLAG=(-resume)
fi

nextflow -q run "${REPO}/workflows/main.nf" \
    -c "${REPO}/nextflow.config" \
    -work-dir "${RERX_ROOT}/nextflow_work/${RERX_RUN_ID:-pilot-dev}" \
    "${RESUME_FLAG[@]}" \
    --run_id "${RERX_RUN_ID:-pilot-dev}" \
    --pilot_scale "${RERX_PILOT_SCALE:-1}" \
    --shard_size "${RERX_SHARD_SIZE:-24}"
