# ReRx pilot reports

Two self-contained HTML reports built from the pilot run's data (HRCE-1
Plate 25). Each report embeds its data directly, so it opens and works
from a plain file:// URL with no server, no network, and no build step.

- `phenotypic_overview.html` — compares CellProfiler features against
  Recursion's published deep-learning site embeddings and MorphEm
  per-cell embeddings (PCA plots, Spearman correlations of
  site-distance matrices, k-nearest-neighbor overlap). MorphEm ran on
  all 33,330 pilot cells on Alpine CPU nodes (1,920 raw features,
  1,552 after selection; 0.75 Spearman / 45% kNN overlap vs
  CellProfiler).
- `buscar_reversal.html` — BUSCAR reversal scores for every pilot
  treatment (efficacy/specificity scatter, Remdesivir/Oseltamivir dose
  response, full score table). Explains why the report scores wells
  instead of single cells (see `rerx.validate.check_control_separation`).

## Regenerating the data

Four scripts build the report payloads from local copies of the pilot
run's Parquet files (synced from Alpine into `data/`, which is
gitignored — regenerate it, don't expect it to be there after a fresh
clone):

```bash
uv run python reports/scripts/prepare_data.py        # core payloads for both reports
uv run python reports/scripts/morphem_report_data.py  # MorphEm QC + PCA comparison payload
uv run --frozen --with umap-learn python reports/scripts/umap_report_data.py  # UMAP coordinates
uv run python reports/scripts/embed_data.py          # embed the JSON payloads into the HTML
```

To rebuild the UMAP report data specifically, `data/` needs the
full CellProfiler feature-selected table
(`data/feature_selected/experiment=HRCE-1/plate=25/profiles.parquet`),
the Recursion site embeddings
(`data/recursion_site_embeddings.parquet`), and the MorphEm
feature-selected sample
(`data/morphem_feature_selected_sample.parquet` — a 3,333-cell sample
of the MorphEm finalized table, all 168 pilot sites); run
`umap_report_data.py` with `umap-learn` available as shown above.

## What data these reports need, and where it comes from

| File                                           | Source                                                                                |
| ---------------------------------------------- | ------------------------------------------------------------------------------------- |
| `data/feature_selected/**/*.parquet`           | `runs/<id>/profiles/cellprofiler/feature_selected/` on the run's durable storage      |
| `data/normalized/**/*.parquet`                 | `runs/<id>/profiles/cellprofiler/normalized/`                                         |
| `data/recursion_site_embeddings.parquet`       | RxRx19a's published `RxRx19a-DL-embeddings.zip`, filtered to this pilot's 168 sites   |
| `data/morphem_feature_selected_sample.parquet` | Sample of `runs/<id>/profiles/morphem/feature_selected/` (3,333 cells, all 168 sites) |
| `data/selection.json`                          | `runs/<id>/selection.json`                                                            |

This is pilot-scale data (one plate, 33,330 cells), not the full RxRx19a
dataset. Numbers and plots describe this pilot only.
