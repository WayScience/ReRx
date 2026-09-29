#!/usr/bin/env python
"""Alpine pilot task driver.

One CLI entry point per pipeline stage, wrapping the tested rerx library
functions (the proven sequence lives in tests/test_pilot_e2e.py). The
Nextflow workflow and the launcher call these subcommands so the
orchestration stays thin and the logic stays in the library.

Environment (set by scripts/alpine_launch.sh / nextflow.config):
    RERX_REPO       repo checkout (src/ + pipelines/ inside)
    RERX_RUN_DIR    durable run directory (/pl/active/koala/ReRx/runs/<id>)
    RERX_SCRATCH    scratch root (/scratch/alpine/<user>/rerx)
    RERX_SOURCE     staged source images root
    RERX_SIF        apptainer image for CellProfiler
    RERX_RUN_ID     run id (rxrx19a-pilot-...-g<sha>)

Subcommands:
    prepare             download metadata, select pilot wells, plan shards
    download            fetch the pilot's PNGs into RERX_SOURCE
    cellprofiler <sid>  stage + run CellProfiler for one shard
    cytotable <sid>     SQLite -> joined single-cell Parquet for one shard
    crops <sid>         per-cell JPEG crops for one shard
    morphem <sid>       MorphEm-embed one crop shard (inside morphem.sif)
    finalize            merge, annotate/normalize/select, validate, catalog
    recursion-buscar    buscar-score Recursion's published site embeddings
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RUN_DIR = Path(os.environ["RERX_RUN_DIR"])
SCRATCH = Path(os.environ["RERX_SCRATCH"])
SOURCE = Path(os.environ["RERX_SOURCE"])
SIF = Path(os.environ["RERX_SIF"])
MORPHEM_SIF = Path(os.environ.get("RERX_MORPHEM_SIF", str(SIF.parent / "morphem.sif")))
RUN_ID = os.environ["RERX_RUN_ID"]
REPO = Path(os.environ["RERX_REPO"])
SHARD_SIZE = int(os.environ.get("RERX_SHARD_SIZE", "24"))

sys.path.insert(0, str(REPO / "src"))


def _log(msg: str) -> None:
    print(f"[rerx {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _load_selection() -> list[dict]:
    with (RUN_DIR / "selection.json").open() as fh:
        return json.load(fh)


def _shards_path() -> Path:
    return RUN_DIR / "shards.json"


def _load_shards() -> list[dict]:
    with _shards_path().open() as fh:
        return json.load(fh)


def _task_dir(shard_id: str) -> Path:
    return SCRATCH / RUN_ID / shard_id


def _pipeline_path() -> Path:
    return REPO / "pipelines" / "rxrx19a.cppipe"


def cmd_prepare() -> None:
    """Download metadata, select pilot wells, write selection + shard plan."""
    from rerx.cellprofiler import shard_image_sets
    from rerx.metadata import (
        ImageSetID,
        download_metadata,
        parse_metadata,
        select_pilot_wells,
    )

    inputs = RUN_DIR / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    _log("downloading RxRx19a metadata")
    csv_path = download_metadata(inputs)

    _log("parsing metadata")
    metadata = parse_metadata(csv_path)
    (RUN_DIR / "metadata").mkdir(parents=True, exist_ok=True)
    metadata.to_parquet(RUN_DIR / "metadata" / "rxrx19a.parquet", index=False)

    # RERX_PILOT_SCALE multiplies every pilot arm size (1 = base pilot).
    arm_scale = int(os.environ.get("RERX_PILOT_SCALE", "1"))
    pilot = select_pilot_wells(
        metadata, cell_type="HRCE", max_wells=42 * arm_scale, arm_scale=arm_scale
    )
    _log(f"pilot selection: {len(pilot)} site rows (arm_scale={arm_scale})")
    pilot.to_json(RUN_DIR / "selection.json", orient="records")

    image_sets = [
        ImageSetID(
            experiment=str(r["experiment"]),
            plate=str(r["plate"]),
            well=str(r["well"]),
            site=int(r["site"]),
        )
        for r in pilot.to_dict("records")
    ]
    shards = shard_image_sets(image_sets, shard_size=SHARD_SIZE)
    plan = [
        {"shard_id": s.shard_id, "site_ids": [ids.site_id for ids in s.image_sets]}
        for s in shards
    ]
    _shards_path().write_text(json.dumps(plan, indent=2))
    _log(f"wrote {len(plan)} shards to {_shards_path()}")


def cmd_download() -> None:
    """Fetch every pilot site's five channel PNGs from GCS into RERX_SOURCE."""
    from rerx.metadata import ImageSetID

    rows = _load_selection()
    jobs: list[tuple[str, Path]] = []
    for r in rows:
        ids = ImageSetID(
            experiment=str(r["experiment"]),
            plate=str(r["plate"]),
            well=str(r["well"]),
            site=int(r["site"]),
        )
        for channel in (1, 2, 3, 4, 5):
            url = ids.image_url(channel)
            dest = SOURCE / ids.image_path(channel)
            jobs.append((url, dest))

    def fetch(job: tuple[str, Path]) -> None:
        url, dest = job
        if dest.is_file() and dest.stat().st_size > 0:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        rc = subprocess.run(
            ["curl", "-fsSL", "--retry", "3", "-o", str(tmp), url],
            timeout=300,
            check=False,
        ).returncode
        if rc != 0:
            raise SystemExit(f"download failed rc={rc}: {url}")
        tmp.rename(dest)

    _log(f"downloading ~{len(jobs)} PNGs")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in pool.map(fetch, jobs):
            pass
    missing = [d for _, d in jobs if not d.is_file()]
    if missing:
        raise SystemError(f"{len(missing)} downloads missing, e.g. {missing[0]}")
    _log(f"all PNGs staged under {SOURCE}")


def cmd_cellprofiler(shard_id: str) -> None:
    """Stage and run CellProfiler for one shard inside the sif."""
    from rerx.cellprofiler import ImageSetShard, run_cellprofiler_shard
    from rerx.metadata import ImageSetID

    shard_plan = {s["shard_id"]: s for s in _load_shards()}[shard_id]
    by_site = {r["site_id"]: r for r in _load_selection()}
    image_sets = [
        ImageSetID(
            experiment=str(by_site[sid]["experiment"]),
            plate=str(by_site[sid]["plate"]),
            well=str(by_site[sid]["well"]),
            site=int(by_site[sid]["site"]),
        )
        for sid in shard_plan["site_ids"]
    ]
    shard = ImageSetShard(shard_id=shard_id, image_sets=image_sets)
    result = run_cellprofiler_shard(
        shard=shard,
        source_root=SOURCE,
        scratch_root=SCRATCH,
        run_id=RUN_ID,
        pipeline_path=_pipeline_path(),
        container_image=str(SIF),
        runtime="apptainer",
        timeout=7200,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"cellprofiler {shard_id} failed rc={result.returncode}; "
            f"log: {result.log_path}"
        )
    if result.sqlite_path is None:
        raise SystemExit(f"cellprofiler {shard_id} produced no SQLite db")
    _log(f"{shard_id}: sqlite={result.sqlite_path} masks={len(result.mask_paths)}")


def _shard_sqlite(shard_id: str) -> Path:
    dbs = sorted(_task_dir(shard_id).glob("output/*.sqlite"))
    if not dbs:
        raise SystemExit(
            f"no SQLite db for shard {shard_id} under {_task_dir(shard_id)}"
        )
    return dbs[0]


def _shard_profiles_path(shard_id: str) -> Path:
    return SCRATCH / RUN_ID / "cytotable" / f"{shard_id}.parquet"


def cmd_cytotable(shard_id: str) -> None:
    """Convert one shard's SQLite into a joined single-cell Parquet."""
    import pandas as pd

    from rerx.cytotable import (
        add_cell_ids,
        convert_sqlite_to_parquet,
        validate_unique_cell_ids,
    )
    from rerx.validate import check_sqlite_integrity

    sqlite_path = _shard_sqlite(shard_id)
    integrity = check_sqlite_integrity(sqlite_path)
    if not integrity.passed:
        raise SystemExit(f"sqlite integrity failed for {shard_id}: {integrity}")

    dest = _shard_profiles_path(shard_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    parquet_path = convert_sqlite_to_parquet(sqlite_path, dest)
    profiles = pd.read_parquet(parquet_path)
    profiles = add_cell_ids(profiles)
    validate_unique_cell_ids(profiles)
    profiles.to_parquet(parquet_path, index=False)
    _log(f"{shard_id}: {len(profiles)} single cells -> {parquet_path}")


def cmd_crops(shard_id: str) -> None:
    """Per-cell JPEG crops for one shard (durable output on the run dir)."""
    import pandas as pd

    from rerx.crops import (
        crop_site_cells,
        load_site_crop_inputs,
        validate_crops_join_one_to_one,
        write_crops_parquet,
    )
    from rerx.metadata import ImageSetID

    task_dir = _task_dir(shard_id)
    images_dir = task_dir / "images"
    profiles = pd.read_parquet(_shard_profiles_path(shard_id))

    mask_by_site: dict[tuple[str, int], Path] = {}
    for p in sorted(task_dir.glob("output/*_MaskCells*.tiff")):
        stem = p.stem  # e.g. A01_s1_w1_MaskCells
        well = stem.split("_")[0]
        site = int(stem.split("_")[1][1:])
        mask_by_site[(well, site)] = p

    shard_plan = {s["shard_id"]: s for s in _load_shards()}[shard_id]
    by_site = {r["site_id"]: r for r in _load_selection()}
    frames = []
    for sid in shard_plan["site_ids"]:
        r = by_site[sid]
        ids = ImageSetID(
            experiment=str(r["experiment"]),
            plate=str(r["plate"]),
            well=str(r["well"]),
            site=int(r["site"]),
        )
        mask_path = mask_by_site[(ids.well, ids.site)]
        inputs = load_site_crop_inputs(ids, images_dir, mask_path)
        cell_rows = profiles.loc[
            (profiles["Image_Metadata_Well"] == ids.well)
            & (profiles["Image_Metadata_Site"].astype(str) == str(ids.site))
        ]
        frames.append(crop_site_cells(inputs, cell_rows))
    crops = pd.concat(frames, ignore_index=True)
    validate_crops_join_one_to_one(crops, profiles)
    dest = RUN_DIR / "crops" / "cells" / f"{shard_id}.parquet"
    write_crops_parquet(crops, dest)
    _log(f"{shard_id}: {len(crops)} crops -> {dest}")


def cmd_morphem(shard_id: str) -> None:
    """Embed one crop shard with MorphEm (runs INSIDE morphem.sif).

    The heavy lifting (torch/transformers) lives in the morphem
    container; this wrapper only points it at the run tree. The
    Nextflow MORPHEM process bind-mounts the same paths.
    """
    cmd = [
        "apptainer",
        "exec",
        "--bind",
        f"{REPO}:/rerx:ro",
        "--bind",
        f"{RUN_DIR}:/rerx_run",
        str(MORPHEM_SIF),
        "python",
        "/rerx/scripts/morphem_embed.py",
        shard_id,
        "--crops-dir",
        "/rerx_run/crops/cells",
        "--dest-root",
        "/rerx_run",
    ]
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise SystemExit(f"morphem {shard_id} failed rc={rc}")


def _finalize_profiles(
    profiles: "object",
    profiler: str,
    buscar: bool,
) -> None:
    """Annotate/normalize/select (and buscar) per plate for one profiler."""
    import pandas as pd

    from rerx.cytotable import plate_partitions
    from rerx.finalize import finalize_plate

    pilot = pd.DataFrame(_load_selection())
    normalized_frames = []
    feature_selected_frames = []
    for (experiment, plate), plate_profiles in plate_partitions(profiles):
        plate_metadata = pilot[
            (pilot["experiment"].astype(str) == experiment)
            & (pilot["plate"].astype(str) == plate)
        ]
        result = finalize_plate(
            raw_profiles=plate_profiles,
            site_metadata=plate_metadata,
            run_dir=RUN_DIR,
            experiment=experiment,
            plate=plate,
            profiler=profiler,
            run_buscar=buscar,
        )
        normalized_frames.append(result.normalized)
        feature_selected_frames.append(result.feature_selected)
        if result.buscar_skipped_reason:
            _log(
                f"{profiler} {experiment}/{plate}: buscar skipped -- "
                f"{result.buscar_skipped_reason}"
            )
        else:
            _log(f"{profiler} {experiment}/{plate}: buscar scored")
    normalized = pd.concat(normalized_frames, ignore_index=True)
    selected = pd.concat(feature_selected_frames, ignore_index=True)
    _log(
        f"{profiler}: annotated/normalized {len(normalized)} cells "
        f"across {len(normalized_frames)} plate(s); feature_selected -> "
        f"{selected.shape[1]} cols"
    )


def _fuse_finalized_profiles() -> None:
    """Fuse finalized CP and MorphEm profiles on Metadata_cell_id.

    Pairs each plate's CP table with its own MorphEm table by
    experiment/plate partition (not sorted-file order), and fuses every
    shared pair; unmatched partitions are skipped (inner-join behavior,
    same as fuse_features itself).
    """
    import pandas as pd

    from rerx.fuse import (
        fuse_features,
        pair_fused_partitions,
        write_fused_profiles,
    )

    cp_selected = sorted(
        (RUN_DIR / "profiles" / "cellprofiler" / "feature_selected").glob(
            "*/*/*.parquet"
        )
    )
    morphem_selected = sorted(
        (RUN_DIR / "profiles" / "morphem" / "feature_selected").glob("*/*/*.parquet")
    )
    if not (cp_selected and morphem_selected):
        _log("fused output skipped: missing finalized CP or MorphEm")
        return
    pairs = pair_fused_partitions(cp_selected, morphem_selected)
    if not pairs:
        _log(
            "fused output skipped: no shared experiment/plate "
            "partitions between CP and MorphEm profiles"
        )
        return
    fused = pd.concat(
        [
            fuse_features(pd.read_parquet(cp_path), pd.read_parquet(me_path))
            for cp_path, me_path in pairs
        ],
        ignore_index=True,
    )
    fused_paths = write_fused_profiles(fused, RUN_DIR)
    _log(
        f"fused: {len(fused)} cells x {fused.shape[1]} cols from "
        f"{len(pairs)} plate(s) -> {len(fused_paths)} partition(s)"
    )


def cmd_recursion_buscar() -> None:
    """buscar-score Recursion's published site embeddings per plate."""
    import pandas as pd

    from rerx.buscar import run_buscar_for_plate
    from rerx.cytotable import plate_partitions
    from rerx.embeddings import build_recursion_buscar_profiles

    emb_path = (
        RUN_DIR
        / "baseline"
        / "recursion_site_embeddings"
        / ("recursion_site_embeddings.parquet")
    )
    if not emb_path.is_file():
        _log("recursion buscar skipped: published embeddings not present")
        return
    embeddings = pd.read_parquet(emb_path)
    selection = pd.DataFrame(_load_selection())
    profiles = build_recursion_buscar_profiles(embeddings, selection)
    _log(f"recursion: annotated {len(profiles)} site embeddings")

    # Site embeddings are one row per (well, site); average to one row
    # per well (the replicate unit buscar scores) so the KS test has
    # the same per-treatment sample sizes as the other profilers.
    feature_cols = [c for c in profiles.columns if c.startswith("Recursion_")]
    group_cols = [
        "experiment",
        "plate",
        "well",
        "Metadata_rxrx_control_type",
        "Metadata_perturbation",
        "Metadata_buscar_state",
    ]
    well_profiles = (
        profiles.groupby(group_cols, as_index=False)[feature_cols]
        .median()
        .reset_index(drop=True)
    )
    # plate_partitions groups on (Image_Metadata_Experiment,
    # Image_Metadata_Plate); supply those names for the embeddings.
    well_profiles = well_profiles.rename(  # type: ignore[dict-item]
        columns={
            "experiment": "Image_Metadata_Experiment",
            "plate": "Image_Metadata_Plate",
            "well": "Image_Metadata_Well",
        }
    )
    for (experiment, plate), plate_profiles in plate_partitions(well_profiles):
        run_buscar_for_plate(
            plate_profiles=plate_profiles,
            dest_dir=RUN_DIR / "buscar" / "recursion",
            experiment=experiment,
            plate=plate,
            profiler="recursion",
        )
        _log(f"recursion {experiment}/{plate}: buscar scored")


def cmd_finalize() -> None:
    """Merge shards, then finalize (annotate/normalize/select/buscar) per plate."""
    import pandas as pd

    from rerx.catalog import build_run_catalog
    from rerx.cytotable import write_partitioned_profiles
    from rerx.finalize import finalize_plate  # noqa: F401  (re-exported use)
    from rerx.validate import validate_pilot_run

    shard_parquets = sorted((SCRATCH / RUN_ID / "cytotable").glob("*.parquet"))
    if not shard_parquets:
        raise SystemExit(f"no shard parquets under {SCRATCH / RUN_ID / 'cytotable'}")
    profiles = pd.concat(
        [pd.read_parquet(p) for p in shard_parquets], ignore_index=True
    )
    _log(f"merged {len(shard_parquets)} shards -> {len(profiles)} cells")

    raw_dir = RUN_DIR / "profiles" / "cellprofiler" / "raw"
    partition_paths = write_partitioned_profiles(profiles, raw_dir)
    _log(f"wrote {len(partition_paths)} partitioned profile files")

    # Per-plate finalize batch (annotate/normalize/select_features/buscar):
    # each plate is its own biological batch with its own control
    # population, so this scales to a full multi-plate run without ever
    # holding more than one plate's profiles in memory at once (unlike a
    # single dataset-wide normalize/select_features/buscar call).
    _finalize_profiles(profiles, profiler="cellprofiler", buscar=True)

    # MorphEm pass: same per-plate finalize over the morphem raw shards,
    # only when every morphem shard is present (a partial set would
    # silently produce incomplete buscar scores).
    morphem_parquets = sorted(
        (RUN_DIR / "profiles" / "morphem" / "raw").glob("*.parquet")
    )
    morphem_shards = {p.stem for p in morphem_parquets}
    expected = {s["shard_id"] for s in _load_shards()}
    if morphem_shards == expected:
        morphem_profiles = pd.concat(
            [pd.read_parquet(p) for p in morphem_parquets], ignore_index=True
        )
        # Crop-carried metadata uses plain lowercase names
        # (Metadata_experiment, Metadata_plate, ...); the shared
        # finalize layer expects the cytotable-style
        # Image_Metadata_* names (annotate_profiles then maps them
        # to Metadata_Experiment/Plate/Well/Site for the site join),
        # so rename before the per-plate batch.
        morphem_profiles = morphem_profiles.rename(
            columns={
                "Metadata_experiment": "Image_Metadata_Experiment",
                "Metadata_plate": "Image_Metadata_Plate",
                "Metadata_well": "Image_Metadata_Well",
                "Metadata_site": "Image_Metadata_Site",
            }
        )
        _log(
            f"morphem: merged {len(morphem_parquets)} shards "
            f"-> {len(morphem_profiles)} cells"
        )
        _finalize_profiles(morphem_profiles, profiler="morphem", buscar=True)

        # Fused output: early feature-concatenation fusion of the two
        # finalized spaces on Metadata_cell_id (see rerx.fuse). Labeled
        # in profiles/fused/fusion.json ("what kind of fused").
        _fuse_finalized_profiles()
    elif morphem_shards:
        missing = sorted(expected - morphem_shards)
        _log(
            f"morphem finalize skipped: {len(missing)} shard(s) missing "
            f"(e.g. {missing[0]}); rerun after MORPHEM completes"
        )
    else:
        _log("morphem finalize skipped: no morphem shards present")

    crop_paths = sorted((RUN_DIR / "crops" / "cells").glob("*.parquet"))
    crops = pd.concat([pd.read_parquet(p) for p in crop_paths], ignore_index=True)
    sqlite_paths = sorted(SCRATCH.glob(f"{RUN_ID}/*/output/*.sqlite"))
    report = validate_pilot_run(
        sqlite_paths=sqlite_paths,
        profile_parquet_paths=partition_paths,
        profiles=profiles,
        crops=crops,
    )
    summary = report.summary()
    (RUN_DIR / "validation_report.txt").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )
    if not report.passed:
        raise SystemExit(f"pilot validation FAILED:\n{summary}")
    _log("pilot validation: PASS")

    build_run_catalog(
        run_root=RUN_DIR,
        catalog_path=RUN_DIR / "catalog" / "run.ducklake",
        data_path=RUN_DIR / "catalog" / "ducklake_data",
    )
    (RUN_DIR / "_SUCCESS").write_text(RUN_ID + "\n")
    _log(f"run complete: {RUN_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rerx-tasks")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    sub.add_parser("download")
    for name in ("cellprofiler", "cytotable", "crops", "morphem"):
        p = sub.add_parser(name)
        p.add_argument("shard_id")
    sub.add_parser("finalize")
    sub.add_parser("recursion-buscar")
    args = parser.parse_args()

    if args.command == "prepare":
        cmd_prepare()
    elif args.command == "download":
        cmd_download()
    elif args.command == "cellprofiler":
        cmd_cellprofiler(args.shard_id)
    elif args.command == "cytotable":
        cmd_cytotable(args.shard_id)
    elif args.command == "crops":
        cmd_crops(args.shard_id)
    elif args.command == "morphem":
        cmd_morphem(args.shard_id)
    elif args.command == "finalize":
        cmd_finalize()
    elif args.command == "recursion-buscar":
        cmd_recursion_buscar()
    else:  # pragma: no cover - argparse enforces choices
        raise SystemExit(f"unknown command {args.command!r}")


if __name__ == "__main__":
    main()
