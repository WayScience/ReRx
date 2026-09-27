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

`scripts/prepare_data.py` reads local copies of the pilot run's Parquet
files (synced from Alpine into `data/`, which is gitignored — regenerate
it, don't expect it to be there after a fresh clone) and writes two
compact JSON files. `scripts/embed_data.py` then embeds them into the
two HTML reports:

```bash
uv run python reports/scripts/prepare_data.py
uv run python reports/scripts/embed_data.py
```

## What data these reports need, and where it comes from

| File | Source |
| --- | --- |
| `data/feature_selected/**/*.parquet` | `runs/<id>/profiles/cellprofiler/feature_selected/` on the run's durable storage |
| `data/normalized/**/*.parquet` | `runs/<id>/profiles/cellprofiler/normalized/` |
| `data/recursion_site_embeddings.parquet` | RxRx19a's published `RxRx19a-DL-embeddings.zip`, filtered to this pilot's 168 sites |
| `data/selection.json` | `runs/<id>/selection.json` |

This is pilot-scale data (one plate, 33,330 cells), not the full RxRx19a
dataset. Numbers and plots describe this pilot only.
