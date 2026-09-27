"""
Tests for the fuse module (CP + MorphEm feature fusion).

The join seam is ``Metadata_cell_id``: both finalized spaces carry it, and
real pilot data verifies 100% overlap (all 3,333 sampled MorphEm cells
match CP). The CP space holds ~572 columns; MorphEm adds up to 1,552
``Morphem_``-prefixed features on the same cells.
"""

import numpy as np
import pandas as pd

from rerx.fuse import fuse_features


def test_fusion_metadata_labels_kind() -> None:
    """The sidecar must answer "what kind of fused" up front."""
    from rerx.fuse import fusion_metadata

    cp = _cp_frame(["w_c0", "w_c1"])
    me = _morphem_frame(["w_c0", "w_c1"])
    fused = fuse_features(cp, me)
    meta = fusion_metadata(fused)
    assert meta["kind"] == "early-fusion:feature-concatenation"
    assert meta["join_key"] == "Metadata_cell_id"
    assert meta["rows"] == 2
    assert meta["cellprofiler_feature_cols"] == 2
    assert meta["morphem_feature_cols"] == 2
    assert meta["sources"]["cellprofiler"].endswith("feature_selected")
    assert meta["sources"]["morphem"].endswith("feature_selected")


def test_write_fused_profiles_partitions_and_labels(tmp_path) -> None:
    """Fused table lands under profiles/fused with the label sidecar."""
    import json

    from rerx.fuse import write_fused_profiles

    cp = _cp_frame(["w_c0", "w_c1"])
    me = _morphem_frame(["w_c0", "w_c1"])
    fused = fuse_features(cp, me)
    written = write_fused_profiles(fused, tmp_path)
    assert len(written) == 1
    assert written[0] == (
        tmp_path
        / "profiles"
        / "fused"
        / "feature_selected"
        / "experiment=HRCE-1"
        / "plate=25"
        / "profiles.parquet"
    )
    sidecar = tmp_path / "profiles" / "fused" / "fusion.json"
    meta = json.loads(sidecar.read_text())
    assert meta["kind"] == "early-fusion:feature-concatenation"
    assert meta["rows"] == 2


def test_write_fused_profiles_requires_plate_metadata(tmp_path) -> None:
    from rerx.fuse import write_fused_profiles

    cp = _cp_frame(["w_c0"])
    me = _morphem_frame(["w_c0"])
    fused = fuse_features(cp, me)
    fused = fused.drop(columns=["Metadata_Plate"])
    try:
        write_fused_profiles(fused, tmp_path)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Metadata_Plate" in str(exc)


def _cp_frame(cells: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "Metadata_cell_id": cells,
            "Metadata_Experiment": ["HRCE-1"] * len(cells),
            "Metadata_Plate": ["25"] * len(cells),
            "Metadata_Well": [c.split("_")[0] for c in cells],
            "Cells_AreaShape_Area": rng.normal(size=len(cells)),
            "Cells_Intensity_MeanIntensity_DNA": rng.normal(size=len(cells)),
        }
    )


def _morphem_frame(cells: list[str]) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        {
            "Metadata_cell_id": cells,
            "Metadata_site_id": [f"site_{c}" for c in cells],
            "Metadata_object_number": np.arange(len(cells)),
            "Morphem_0": rng.normal(size=len(cells)),
            "Morphem_1": rng.normal(size=len(cells)),
        }
    )


def test_fuse_inner_join_on_cell_id() -> None:
    cp = _cp_frame(["w_c0", "w_c1", "w_c2"])
    me = _morphem_frame(["w_c0", "w_c1"])
    fused = fuse_features(cp, me)
    assert len(fused) == 2
    # Feature block = CP features + Morphem features, metadata kept from CP
    morphem_cols = [c for c in fused.columns if c.startswith("Morphem_")]
    assert morphem_cols == ["Morphem_0", "Morphem_1"]
    cp_feature_cols = [
        c for c in fused.columns if c.startswith(("Cells_", "Cytoplasm_", "Nuclei_"))
    ]
    assert cp_feature_cols == [
        "Cells_AreaShape_Area",
        "Cells_Intensity_MeanIntensity_DNA",
    ]


def test_fuse_errors_on_empty_join() -> None:
    cp = _cp_frame(["w_c0"])
    me = _morphem_frame(["other_c9"])
    try:
        fuse_features(cp, me)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "no shared cells" in str(exc)


def test_fuse_column_collision_error() -> None:
    cp = _cp_frame(["w_c0"])
    me = _morphem_frame(["w_c0"])
    me["Cells_AreaShape_Area"] = 0.0  # collides with CP feature name
    try:
        fuse_features(cp, me)
        raise AssertionError("expected ValueError")
    except ValueError as ValueError_exc:
        assert "collide" in str(ValueError_exc)


def test_fuse_warns_on_partial_overlap() -> None:
    cp = _cp_frame(["w_c0", "w_c1", "w_c2", "w_c3"])
    me = _morphem_frame(["w_c0", "w_c1"])  # 50% overlap
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fuse_features(cp, me)
    assert any("cells dropped" in str(w.message) for w in caught)
