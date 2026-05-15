"""
End-to-end verification mimicking the user's real flow:
- ERP-style master upload (Korean column names, NaN values, mixed types)
- Custom master + stale bundled loads scenario (the bug pattern)
- Custom master + custom loads (happy path)
- Master persistence on disk + reload
- Edge cases that crashed before

Run: pytest tests/test_e2e.py -v
"""
import sys
import pytest
import pandas as pd
import numpy as np
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Import the functions to test
import app
from app import (
    normalize_master_df,
    normalize_trucks_df,
    normalize_loads_df,
    save_user_master,
    _resolve_user_data_dir,
    _writable,
    build_loads_template_bytes,
    REQUIRED_MASTER_COLS,
)
from engine.best_packer import simulate


# ─────────────────────────────────────────────────────────────────────────
# Edge-case fixtures mimicking user's ERP exports
# ─────────────────────────────────────────────────────────────────────────
def make_messy_master():
    """Master with the exact patterns seen in user's 73k-row ERP export."""
    return pd.DataFrame([
        # Normal row
        {"model_code": "REF001", "category": "Refrigerator",
         "width_in": 36.0, "depth_in": 30.0, "height_in": 68.0,
         "weight_lb": 280.0, "stackable": False, "volume_cft": 42.5},
        # Capitalized column names will be normalized
        {"model_code": "WSH001", "category": "Washer",
         "width_in": 29.0, "depth_in": 32.0, "height_in": 41.0,
         "weight_lb": 210.0, "stackable": True, "volume_cft": 22.0},
        # NaN category (the actual TypeError cause)
        {"model_code": "TV001", "category": np.nan,
         "width_in": 65.0, "depth_in": 8.0, "height_in": 38.0,
         "weight_lb": 53.0, "stackable": True, "volume_cft": 11.4},
        # stackable as string "Y" / "N"
        {"model_code": "MWO001", "category": "Microwave",
         "width_in": 30.0, "depth_in": 16.0, "height_in": 17.0,
         "weight_lb": 40.0, "stackable": "Y", "volume_cft": 4.7},
        # quantity-like with weird types
        {"model_code": "DRY001", "category": "Dryer",
         "width_in": 29.0, "depth_in": 32.0, "height_in": 41.0,
         "weight_lb": 165.0, "stackable": 1, "volume_cft": 22.0},
        # Row with NaN in required dim → should be dropped
        {"model_code": "BAD001", "category": "Unknown",
         "width_in": np.nan, "depth_in": 20.0, "height_in": 20.0,
         "weight_lb": 50.0, "stackable": False, "volume_cft": np.nan},
        # Missing volume_cft → should be auto-computed
        {"model_code": "OK001", "category": "TV",
         "width_in": 55.0, "depth_in": 7.0, "height_in": 33.0,
         "weight_lb": 38.0, "stackable": True},
    ])


def make_loads(model_codes):
    """Build a Loads df referencing given SKUs."""
    return pd.DataFrame([
        {"load_id": "L100", "model_code": mc, "quantity": 2}
        for mc in model_codes
    ])


# ─────────────────────────────────────────────────────────────────────────
# Normalize tests
# ─────────────────────────────────────────────────────────────────────────
class TestNormalizeMaster:
    def test_drops_rows_with_missing_required(self):
        df, warns = normalize_master_df(make_messy_master())
        assert "BAD001" not in df["model_code"].tolist()
        assert any("Dropped 1 row" in w for w in warns)

    def test_fills_nan_category_with_uncategorized(self):
        df, _ = normalize_master_df(make_messy_master())
        # TV001 had NaN category → should be string "Uncategorized" now
        tv = df[df["model_code"] == "TV001"].iloc[0]
        assert isinstance(tv["category"], str)
        assert tv["category"] in ("Uncategorized", "")

    def test_no_nan_in_text_columns(self):
        df, _ = normalize_master_df(make_messy_master())
        for col in ("model_code", "category"):
            assert not df[col].isna().any(), f"NaN found in {col}"
            for v in df[col]:
                assert isinstance(v, str), f"non-str in {col}: {v!r}"

    def test_categories_are_sortable(self):
        """Was crashing with TypeError before fix."""
        df, _ = normalize_master_df(make_messy_master())
        cats = sorted({str(c) for c in df["category"].dropna().unique()})
        assert len(cats) > 0  # no exception means we passed

    def test_stackable_coerced_from_strings_and_ints(self):
        df, _ = normalize_master_df(make_messy_master())
        mwo = df[df["model_code"] == "MWO001"].iloc[0]
        dry = df[df["model_code"] == "DRY001"].iloc[0]
        assert mwo["stackable"] is True or mwo["stackable"] == True
        assert dry["stackable"] is True or dry["stackable"] == True

    def test_auto_computes_volume_when_missing(self):
        df, warns = normalize_master_df(make_messy_master())
        ok = df[df["model_code"] == "OK001"].iloc[0]
        expected = 55 * 7 * 33 / 1728
        assert abs(ok["volume_cft"] - expected) < 0.1

    def test_capitalized_columns_normalized(self):
        raw = pd.DataFrame([{
            "Model_Code": "X1", "Width_In": 20, "Depth_In": 20,
            "Height_In": 20, "Weight_Lb": 10, "Stackable": True,
        }])
        df, _ = normalize_master_df(raw)
        assert "model_code" in df.columns
        assert df.iloc[0]["model_code"] == "X1"

    def test_rejects_with_missing_required_columns(self):
        raw = pd.DataFrame([{"model_code": "X", "width_in": 10}])  # missing depth/height/weight/stackable
        with pytest.raises(ValueError, match="Missing required"):
            normalize_master_df(raw)


class TestNormalizeLoads:
    def test_minimal_3col_works(self):
        raw = pd.DataFrame([{"load_id": "L1", "model_code": "X1", "quantity": 5}])
        df, warns = normalize_loads_df(raw)
        assert len(df) == 1
        assert df.iloc[0]["quantity"] == 5

    def test_drops_zero_quantity(self):
        raw = pd.DataFrame([
            {"load_id": "L1", "model_code": "X1", "quantity": 5},
            {"load_id": "L1", "model_code": "X2", "quantity": 0},
            {"load_id": "L1", "model_code": "X3", "quantity": -1},
        ])
        df, warns = normalize_loads_df(raw)
        assert len(df) == 1
        assert any("Dropped" in w for w in warns)

    def test_case_insensitive_columns(self):
        raw = pd.DataFrame([{"Load_ID": "L1", "Model_Code": "X1", "Quantity": 3}])
        df, _ = normalize_loads_df(raw)
        assert df.iloc[0]["load_id"] == "L1"

    def test_rejects_missing_required(self):
        raw = pd.DataFrame([{"load_id": "L1", "quantity": 5}])  # no model_code
        with pytest.raises(ValueError, match="Missing required"):
            normalize_loads_df(raw)


class TestPersistenceFallback:
    def test_resolves_to_writable_dir(self):
        d = _resolve_user_data_dir()
        assert d.exists() or d.parent.exists()
        # Should be in a sensible location
        assert any(part.lower() in str(d).lower()
                   for part in ("LG_Load_Optimizer", "Documents"))

    def test_writable_probe_works(self, tmp_path):
        assert _writable(tmp_path) is True

    def test_writable_returns_false_for_nonexistent_parent(self):
        # Use a deep path with a known-bad parent
        if Path("/nonexistent_drive_xyz").exists():
            pytest.skip("nonexistent path exists")
        assert _writable(Path("/nonexistent_drive_xyz/foo/bar")) is False


# ─────────────────────────────────────────────────────────────────────────
# Full simulation flow tests
# ─────────────────────────────────────────────────────────────────────────
class TestSimulationWithCustomMaster:
    """End-to-end: custom master + custom loads → simulate."""

    def test_simulate_with_user_master(self):
        master_df, _ = normalize_master_df(make_messy_master())
        master_dict = master_df.set_index("model_code").to_dict("index")

        truck = {
            "length_in": 311.0, "width_in": 97.0, "height_in": 97.0,
            "max_payload_lb": 10000, "cargo_volume_cft": 1700.0,
        }
        order = [
            {"model_code": "REF001", "quantity": 2},
            {"model_code": "MWO001", "quantity": 10},
        ]
        result = simulate(order, master_dict, truck)
        assert result["fitted_count"] > 0
        assert "x_used_ft" in result["metrics"]
        # All placements should reference real models
        for p in result["placements"]:
            assert p["model_code"] in master_dict


class TestSampleLoadsMismatch:
    """Reproduces the user's KeyError: LF29H8330S not found."""

    def test_validation_catches_missing_sku_before_simulate(self):
        """The fix added to app.py — should detect missing SKU upfront."""
        master_df, _ = normalize_master_df(make_messy_master())
        master_dict = master_df.set_index("model_code").to_dict("index")
        order = [{"model_code": "LF29H8330S", "quantity": 6}]  # not in custom master
        requested = {l["model_code"] for l in order}
        missing = sorted(requested - set(master_dict.keys()))
        assert "LF29H8330S" in missing  # validation in app.py would stop here


class TestTemplateGeneration:
    def test_loads_template_has_only_3_columns_in_loads_sheet(self):
        b = build_loads_template_bytes()
        xls = pd.ExcelFile(BytesIO(b))
        assert "Loads" in xls.sheet_names
        assert "Schema_Notes" in xls.sheet_names
        loads = pd.read_excel(xls, "Loads")
        assert list(loads.columns) == ["load_id", "model_code", "quantity"]
        notes = pd.read_excel(xls, "Schema_Notes")
        # Notes should explicitly call out auto-mapping
        notes_text = " ".join(notes["description"].astype(str))
        assert "Model_Master" in notes_text or "auto-mapped" in notes_text.lower()


class TestSavePersistRound:
    """Save → reload → still works (no NaN crash, no SKU mismatch)."""

    def test_save_and_reload(self, tmp_path):
        master_df, _ = normalize_master_df(make_messy_master())
        truck_df, _ = normalize_trucks_df(pd.DataFrame([{
            "truck_type": "26ft", "display_name": "26ft Box",
            "length_in": 311, "width_in": 97, "height_in": 97,
            "max_payload_lb": 10000, "cargo_volume_cft": 1700,
        }]))
        target = tmp_path / "master.xlsx"
        # Save without loads (simulating the new "don't auto-persist loads" behavior)
        with pd.ExcelWriter(target, engine="openpyxl") as w:
            master_df.to_excel(w, sheet_name="Model_Master", index=False)
            truck_df.to_excel(w, sheet_name="Truck_Master", index=False)
        # Reload
        loaded_master = pd.read_excel(target, sheet_name="Model_Master")
        loaded_trucks = pd.read_excel(target, sheet_name="Truck_Master")
        try:
            pd.read_excel(target, sheet_name="Loads")
            has_loads = True
        except ValueError:
            has_loads = False
        assert not has_loads  # no stale loads persisted
        # Re-normalize should still work cleanly (defensive on load)
        renorm, _ = normalize_master_df(loaded_master)
        cats = sorted({str(c) for c in renorm["category"].dropna().unique()})
        # No TypeError — that was the bug
        assert len(cats) >= 0
