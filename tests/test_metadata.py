"""Unit tests for pilot well selection (src/rerx/metadata.py)."""

from __future__ import annotations

import pandas as pd
import pytest

from rerx.metadata import (
    DISEASE_CONDITION_ACTIVE,
    DISEASE_CONDITION_MOCK,
    DISEASE_CONDITION_UV,
    PILOT_NEGATIVE_CONTROL,
    PILOT_POSITIVE_CONTROL,
    select_pilot_wells,
)

METADATA_COLUMNS = [
    "site_id",
    "well_id",
    "cell_type",
    "experiment",
    "plate",
    "well",
    "site",
    "disease_condition",
    "treatment",
    "treatment_conc",
    "SMILES",
]


def _row(
    well: str,
    site: int,
    disease: str,
    treatment: str = "",
    conc: str = "",
) -> dict[str, object]:
    return {
        "site_id": f"HRCE-1_Plate25_{well}_s{site}",
        "well_id": f"HRCE-1_Plate25_{well}",
        "cell_type": "HRCE",
        "experiment": "HRCE-1",
        "plate": "25",
        "well": well,
        "site": site,
        "disease_condition": disease,
        "treatment": treatment,
        "treatment_conc": conc,
        "SMILES": "",
    }


def _synthetic_plate(
    n_mock: int = 10, n_uv: int = 10, n_active: int = 10
) -> pd.DataFrame:
    """One HRCE-1/Plate25-style plate with all five pilot arms."""
    rows: list[dict[str, object]] = []
    for i in range(n_mock):
        rows.append(_row(f"M{i:02d}", 1, DISEASE_CONDITION_MOCK))
    for i in range(n_uv):
        rows.append(_row(f"U{i:02d}", 1, DISEASE_CONDITION_UV))
    for i in range(n_active):
        rows.append(_row(f"A{i:02d}", 1, DISEASE_CONDITION_ACTIVE))
    for i in range(8):
        rows.append(
            _row(
                f"P{i:02d}",
                1,
                DISEASE_CONDITION_ACTIVE,
                PILOT_POSITIVE_CONTROL,
                str((i + 1) * 0.5),
            )
        )
    for i in range(8):
        rows.append(
            _row(
                f"N{i:02d}",
                1,
                DISEASE_CONDITION_ACTIVE,
                PILOT_NEGATIVE_CONTROL,
                str((i + 1) * 0.25),
            )
        )
    return pd.DataFrame(rows, columns=pd.Index(METADATA_COLUMNS))


def _parse(df: pd.DataFrame) -> pd.DataFrame:
    """Run parse_metadata-equivalent typing over an in-memory frame."""
    out = df.copy()
    out["treatment_conc_num"] = pd.to_numeric(out["treatment_conc"], errors="coerce")
    out["is_mock"] = out["disease_condition"] == DISEASE_CONDITION_MOCK
    out["is_active"] = out["disease_condition"] == DISEASE_CONDITION_ACTIVE
    out["is_uv"] = out["disease_condition"] == DISEASE_CONDITION_UV
    return out


@pytest.fixture
def metadata() -> pd.DataFrame:
    return _parse(_synthetic_plate())


class TestSelectPilotWells:
    def test_base_selection_covers_all_arms(self, metadata: pd.DataFrame) -> None:
        wells = select_pilot_wells(metadata, cell_type="HRCE", max_wells=48)
        conditions = wells.groupby("disease_condition")["well_id"].nunique()
        assert DISEASE_CONDITION_MOCK in conditions.index
        assert DISEASE_CONDITION_UV in conditions.index
        assert DISEASE_CONDITION_ACTIVE in conditions.index
        assert len(wells) > 0

    def test_scaled_selection_proportionally_larger(
        self, metadata: pd.DataFrame
    ) -> None:
        base = select_pilot_wells(metadata, cell_type="HRCE", max_wells=48)
        scaled = select_pilot_wells(
            metadata, cell_type="HRCE", max_wells=192, arm_scale=4
        )
        assert scaled["well_id"].nunique() > base["well_id"].nunique()
        # Each control arm grows proportionally.
        base_arms = base.groupby("disease_condition")["well_id"].nunique()
        scaled_arms = scaled.groupby("disease_condition")["well_id"].nunique()
        for arm in base_arms.index:
            if arm == DISEASE_CONDITION_ACTIVE:
                continue  # active includes treated wells at higher scales
            assert scaled_arms.get(arm, 0) >= base_arms[arm]

    def test_scaled_selection_respects_arm_minimums(
        self, metadata: pd.DataFrame
    ) -> None:
        scaled = select_pilot_wells(
            metadata, cell_type="HRCE", max_wells=192, arm_scale=4
        )
        # Mock, UV, and active-untreated arms are all present after scaling.
        arms = scaled.groupby("disease_condition")["well_id"].nunique()
        assert arms[DISEASE_CONDITION_MOCK] >= 6
        assert arms[DISEASE_CONDITION_UV] >= 6
        assert arms[DISEASE_CONDITION_ACTIVE] >= 6

    def test_scale_one_matches_base_selection(
        self, metadata: pd.DataFrame
    ) -> None:
        base = select_pilot_wells(metadata, cell_type="HRCE", max_wells=48)
        same = select_pilot_wells(
            metadata, cell_type="HRCE", max_wells=48, arm_scale=1
        )
        pd.testing.assert_frame_equal(base, same)
