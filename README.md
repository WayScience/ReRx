# ReRx

Single-cell phenotypic reversal in RxRx19a

## What this pipeline does

ReRx turns raw microscopy images from the RxRx19a dataset into single-cell
morphology profiles, then scores each treatment for how much it reverses
a diseased cell back toward a healthy state (BUSCAR scoring).

The pipeline has six stages, in order:

1. **Metadata and selection** — download the RxRx19a metadata, then pick
   the wells for a run (a small pilot subset, or the full dataset).
1. **CellProfiler** — segment cells and measure per-cell features from
   the five-channel images. Runs in a container (Docker locally,
   Apptainer on Alpine).
1. **CytoTable** — join CellProfiler's per-compartment SQLite tables
   (Nuclei, Cells, Cytoplasm) into one row per cell, and add a stable
   `Metadata_cell_id`.
1. **Crops** — cut a JPEG crop of each cell from each of the five
   channels, for visual QC and future embedding models.
1. **Finalize (per plate)** — annotate, normalize, and select features
   with Pycytominer, then run a biological QC gate and BUSCAR reversal
   scoring. See "Why the pipeline batches by plate" below.
1. **Catalog** — build a read-only DuckLake catalog over the finished
   Parquet files, so anyone can query the run with plain SQL.

Two runners drive this pipeline:

- Local, small runs: `tests/test_pilot_e2e.py` (an opt-in end-to-end
  test) shows the full call sequence against a real CellProfiler
  container.
- Alpine HPC, full-scale runs: `workflows/main.nf` (Nextflow, Slurm
  executor) calls `scripts/rerx_tasks.py`, a thin command-line driver
  that wraps the same library functions under `src/rerx/`.

## Why the pipeline batches by plate

Each plate is its own biological batch: it has its own mock (healthy) and
disease (active, untreated) control wells, and normalization must
compare cells against controls from the *same* plate, not a different
one.

This has three effects on the code:

- `rerx.cytotable.plate_partitions` groups cell profiles by
  `(experiment, plate)`. Every downstream step consumes one plate's
  group at a time.
- `rerx.finalize.finalize_plate` runs annotate, normalize,
  feature-select, the control-separation QC gate, and BUSCAR for one
  plate. It never holds more than one plate's cells in memory.
- A full multi-plate run (thousands of plates) processes plate by
  plate. Memory use stays flat as the run scales, instead of growing
  with the whole dataset.

If a future project has a different batching unit (for example, per
96-well plate barcode, or per acquisition day), replace
`plate_partitions`'s group key. The rest of `finalize_plate` does not
change.

## The biological QC gate

A technically clean run (every SQLite integrity check passes, every
crop decodes) can still carry bad biology — a failed stain, or a dead
plate where nothing grew. `rerx.validate.check_control_separation`
catches this case.

The check compares the mock and disease control populations on every
morphology feature, using Cohen's d effect size. If the median absolute
effect size falls below a threshold (0.5 by default), the check fails
for that plate.

This check runs before BUSCAR, not after, for a concrete reason: BUSCAR
itself raises an error when there is no separating feature between mock
and disease (division by zero inside its Earth Mover's Distance
calculation). `finalize_plate` checks first, then skips BUSCAR with a
clear reason logged, instead of letting the whole run crash on one bad
plate.

## BUSCAR reversal scoring

BUSCAR (`rerx.buscar`) answers one question per treatment: how far does
this treatment move a diseased cell back toward the healthy state?

The scoring needs three pieces of metadata, all added automatically
during the finalize stage:

- `Metadata_rxrx_control_type` — RxRx19a's own control label (`mock`,
  `uv`, `active_untreated`, `treated`).
- `Metadata_perturbation` — a stable identifier for BUSCAR to group
  replicate wells by. Mock, UV, and active-untreated controls each get
  their own value (so their differences stay visible). A dosed
  treatment becomes `<treatment>__<concentration>`, for example
  `Remdesivir (GS-5734)__1.0`.
- `Metadata_buscar_state` — `healthy` for mock, `disease` for every
  challenged well (UV, active-untreated, treated).

BUSCAR writes two files per plate:

- `signatures.parquet` — which morphology features move between the
  healthy and disease controls (the "on" signature), and which do not
  (the "off" signature, used to catch off-target effects).
- `scores.parquet` — one row per perturbation, with an `on_buscar_scores`
  column (0 means fully reversed to healthy, 1 means no reversal) and an
  `off_buscar_scores` column (off-target effect size).

## The result tree

A finished run directory (see `rerx.runs.make_run_dir`) looks like this:

```text
runs/<run_id>/
├── metadata/
│   └── rxrx19a.parquet              # full parsed RxRx19a metadata
├── selection.json                    # wells picked for this run
├── shards.json                       # CellProfiler shard plan
├── profiles/cellprofiler/
│   ├── raw/
│   │   └── experiment=<e>/plate=<p>/profiles.parquet   # CytoTable output
│   ├── normalized/
│   │   └── experiment=<e>/plate=<p>/profiles.parquet   # Pycytominer normalize
│   └── feature_selected/
│       └── experiment=<e>/plate=<p>/profiles.parquet   # Pycytominer select_features
├── crops/cells/
│   └── <shard_id>.parquet            # one row per cell, 5 JPEG columns
├── buscar/cellprofiler/
│   └── experiment=<e>/plate=<p>/
│       ├── signatures.parquet
│       ├── scores.parquet
│       └── cellprofiler_summary.json
├── catalog/
│   └── run.ducklake                  # DuckLake catalog over every file above
├── validation_report.txt             # technical + biological QC summary
└── _SUCCESS                          # written only when the run passes
```

Every Parquet file under `profiles/`, `crops/`, and `buscar/` stays the
canonical data. The DuckLake catalog only points at these files; delete
and rebuild it any time with `rerx.catalog.build_run_catalog`.

Query the finished dataset with plain SQL, without DuckLake, using
DuckDB's `read_parquet`:

```sql
SELECT *
FROM read_parquet('profiles/cellprofiler/feature_selected/**/*.parquet');
```

## Adapting this pipeline to a different dataset

Four things are specific to RxRx19a today, and each has a clear home if
you port this pipeline to a different imaging dataset:

- **Metadata schema and channel layout** — `src/rerx/metadata.py` (URL
  format, column names, five-channel naming).
- **Control taxonomy** — `src/rerx/pycytominer.py`'s
  `RXRX_CONTROL_*` constants and `rxrx_control_type` (map your dataset's
  own control labels to the same three-column pattern:
  `Metadata_rxrx_control_type`, `Metadata_pycytominer_control_type`,
  `Metadata_buscar_state`).
- **CellProfiler pipeline** — `pipelines/rxrx19a.cppipe` is tuned for
  this dataset's cell type and stains. A new dataset needs its own
  `.cppipe`, built and validated the same way.
- **Batching unit** — `rerx.cytotable.plate_partitions`, as described
  above.

Everything else — sharding, container invocation, CytoTable conversion,
crops, the QC gate, BUSCAR, the catalog, and the Nextflow/Slurm
orchestration — works unchanged.

## High-level overview

This template gives you a ready-to-run Python research software project with:

- `uv`-managed environments and dependencies
- Testing and coverage defaults via `pytest` + `coverage.py`
- Pre-commit automation for formatting, linting, and type checks
- GitHub Actions workflows for linting, tests, docs, and release-related automation
- Starter package + CLI scaffold under `src/`
- Documentation scaffold under `docs/`
- Poe task entrypoints for common local workflows (including a full local pipeline task)

## Included agent skills

This project includes agent guidance in `.agents/skills/` with common skills for:

- Test-driven development
- Incremental implementation
- Code review and quality checks
- CI/CD and automation workflow alignment
- Debugging and error recovery
- Optional learning exercises (`learning-opportunities`)
- Simplified Technical English (ASD-STE100) writing checks (`simple-english`)

If you do not want to use local agent guidance in your project, remove `AGENTS.md` and the `.agents/` directory.

## Post template copy instructions

While we provide some customizations to the files in this template based on your specification there's likely a chance some things aren't perfect.
We recommend taking a look at each file used within this template to ensure it meets your expectations for the project you're working on.
In addition, consider the following steps to help ensure the project is in good shape before proceeding too far.

- [ ] Remove files you plan on not using (e.g. `src/notebooks`, `.github/workflows/publish-pypi.yml`, etc.).
- [ ] Update the `LICENSE` file based on the project.
- [ ] Update the `CITATION.cff` file based on the project.
- [ ] Update `.github/CODEOWNERS` with the right GitHub handle(s) for review ownership, and expand it to additional owners/teams and path-specific ownership rules as the project grows.
- [ ] Update the project dependencies using `uv remove` or `uv add`.
- [ ] Update the `pyproject.toml` file based on the project.
- [ ] Enable `pre-commit-lite` to help automate corrections to code during pull request updates. Otherwise, consider removing the step: labeled with: `pre-commit-ci/lite-action` within `.github/workflows/run-tests.yml`.
- [ ] Enable private security vulnerability issue reporting within the repo settings (e.g. https://github.com/repo_org/repo_name/settings/security_analysis)
- [ ] Enable [branch protection rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule) to require one pull request review approval per pull request to help with maintainer expectations.
- [ ] Add collaborators to access the repository.
- [ ] Create a `pages` branch and enable GitHub Pages on the repository (for documentation).
- [ ] Update the GitHub repository description.
- [ ] Remove these instructions!
