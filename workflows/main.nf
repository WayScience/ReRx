#!/usr/bin/env nextflow
/*
 * ReRx Alpine pilot (HRCE-1 Plate 25: 42 wells x 4 sites x 5 channels).
 *
 * Head node: Persistence1 (module load nextflow/25.10.2)
 * Compute:   Slurm acpu partition, account amc-general (nextflow.config)
 * Durable:   /pl/active/koala/ReRx (params.run_dir, params.source)
 * Scratch:   /scratch/alpine/<user>/rerx (params.scratch)
 *
 * All processes write directly to the shared filesystems via the task
 * driver (scripts/rerx_tasks.py); channels carry only shard ids, so the
 * DAG is a pure synchronization device. Each process exports the
 * RERX_* env vars the driver reads (os.environ) before invoking it.
 */

params.run_id     = 'pilot-dev'
params.repo       = '/scratch/alpine/dabu57888@xsede.org/rerx/ReRx'
params.python     = '/scratch/alpine/dabu57888@xsede.org/rerx/ReRx/.venv/bin/python'
params.run_dir    = params.run_dir ?: "/pl/active/koala/ReRx/runs/${params.run_id}"
params.scratch    = '/scratch/alpine/dabu57888@xsede.org/rerx'
params.source     = '/pl/active/koala/ReRx/source'
params.sif        = '/scratch/alpine/dabu57888@xsede.org/rerx/cellprofiler.sif'
params.morphem_sif = '/scratch/alpine/dabu57888@xsede.org/rerx/morphem.sif'
params.shard_size = '24'
params.pilot_scale = params.pilot_scale ?: '1'

def rerxEnv = """
export RERX_REPO='${params.repo}'
export RERX_RUN_DIR='${params.run_dir}'
export RERX_SCRATCH='${params.scratch}'
export RERX_SOURCE='${params.source}'
export RERX_SIF='${params.sif}'
export RERX_MORPHEM_SIF='${params.morphem_sif}'
export RERX_RUN_ID='${params.run_id}'
export RERX_SHARD_SIZE='${params.shard_size}'
export RERX_PILOT_SCALE='${params.pilot_scale}'
"""

process PREPARE {
    tag 'prepare'

    output:
    path 'shard_ids.txt', emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py prepare
    ${params.python} ${params.repo}/scripts/rerx_tasks.py download
    python3 -c "import json, os; plan = json.load(open(os.environ['RERX_RUN_DIR'] + '/shards.json')); print('\\n'.join(s['shard_id'] for s in plan))" > shard_ids.txt
    """
}

process CELLPROFILER {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py cellprofiler ${shard_id}
    """
}

process CYTOTABLE {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py cytotable ${shard_id}
    """
}

process CROPS {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py crops ${shard_id}
    """
}

process MORPHEM {
    tag "${shard_id}"

    input:
    val shard_id

    output:
    val shard_id, emit: ids

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py morphem ${shard_id}
    """
}

process FINALIZE {
    tag 'finalize'

    input:
    val shard_done

    output:
    path '_SUCCESS', emit: done

    script:
    """
    ${rerxEnv}
    ${params.python} ${params.repo}/scripts/rerx_tasks.py finalize
    cp ${params.run_dir}/_SUCCESS _SUCCESS
    """
}

workflow {
    PREPARE()
    CELLPROFILER(PREPARE.out.ids.splitText { it.trim() })
    CYTOTABLE(CELLPROFILER.out.ids)
    CROPS(CYTOTABLE.out.ids)
    MORPHEM(CROPS.out.ids)
    FINALIZE(MORPHEM.out.ids.collect())
}