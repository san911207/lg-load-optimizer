"""
Tests for the pair-packing engine.
Run: pytest tests/
"""
import pytest
from pathlib import Path
import pandas as pd
import sys

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.best_packer import simulate, pair_pack, find_best, DOOR_TRACK_LOSS_MM


@pytest.fixture
def master():
    xl = Path(__file__).resolve().parent.parent / "data" / "sample_input.xlsx"
    df = pd.read_excel(xl, sheet_name="Model_Master")
    m = df.set_index("model_code").to_dict("index")
    # Apply known calibrations
    m["LDFN4542S"]["stackable"] = True
    m["LDFN4542S"]["load_bear_kg"] = 60
    m["LDFN4542S"]["fragile"] = False
    m["LWS3063ST"]["stackable"] = True
    m["LWS3063ST"]["load_bear_kg"] = 90
    m["LWS3063ST"]["fragile"] = False
    return m


@pytest.fixture
def truck_26ft():
    return {
        "length_mm": 7925,
        "width_mm": 2438,
        "height_mm": 2590,
        "max_payload_kg": 4500,
    }


@pytest.fixture
def truck_53ft():
    return {
        "length_mm": 16154,
        "width_mm": 2591,
        "height_mm": 2700,
        "max_payload_kg": 20000,
    }


@pytest.fixture
def sample_order():
    return [
        {"model_code": "LF29H8330S", "quantity": 6},
        {"model_code": "WM4000HWA",  "quantity": 8},
        {"model_code": "DLEX4000W",  "quantity": 8},
        {"model_code": "LDFN4542S",  "quantity": 10},
        {"model_code": "LWS3063ST",  "quantity": 4},
    ]


class TestPairPackingBasics:
    """Basic invariants of the algorithm."""

    def test_all_fit_in_26ft(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        assert result["fits"] is True
        assert result["fitted_count"] == 36
        assert result["unfitted_count"] == 0

    def test_all_fit_in_53ft(self, sample_order, master, truck_53ft):
        result = simulate(sample_order, master, truck_53ft)
        assert result["fits"] is True
        assert result["fitted_count"] == 36

    def test_compactness_under_95pct_in_26ft(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        # Pair packing should give us ≥5% buffer
        assert result["metrics"]["compactness_pct"] < 95.0
        assert result["metrics"]["remaining_length_mm"] > 300

    def test_no_overlapping_boxes(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        placements = result["placements"]
        for i, p1 in enumerate(placements):
            for p2 in placements[i+1:]:
                # 3D AABB overlap check
                if (p1["x_mm"] < p2["x_mm"] + p2["dim_x_mm"] and
                    p1["x_mm"] + p1["dim_x_mm"] > p2["x_mm"] and
                    p1["y_mm"] < p2["y_mm"] + p2["dim_y_mm"] and
                    p1["y_mm"] + p1["dim_y_mm"] > p2["y_mm"] and
                    p1["z_mm"] < p2["z_mm"] + p2["dim_z_mm"] and
                    p1["z_mm"] + p1["dim_z_mm"] > p2["z_mm"]):
                    pytest.fail(f"Overlap: seq {p1['seq']} ({p1['model_code']}) and seq {p2['seq']} ({p2['model_code']})")

    def test_all_boxes_within_truck(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        for p in result["placements"]:
            assert p["x_mm"] + p["dim_x_mm"] <= truck_26ft["length_mm"], f"Box {p['seq']} overflows length"
            assert p["y_mm"] + p["dim_y_mm"] <= truck_26ft["width_mm"], f"Box {p['seq']} overflows width"
            # Height must clear door track
            eff_height = truck_26ft["height_mm"] - DOOR_TRACK_LOSS_MM
            assert p["z_mm"] + p["dim_z_mm"] <= eff_height, f"Box {p['seq']} hits door track"


class TestLaneUtilization:
    """Verify max lane count for each model."""

    def test_max_lanes_for_washer(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        washer_placements = [p for p in result["placements"] if p["model_code"] == "WM4000HWA"]
        unique_lanes = set(p["lane"] for p in washer_placements)
        assert len(unique_lanes) == 3, f"Washer should use 3 lanes, got {len(unique_lanes)}"

    def test_max_lanes_for_dishwasher(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        dish_placements = [p for p in result["placements"] if p["model_code"] == "LDFN4542S"]
        unique_lanes = set(p["lane"] for p in dish_placements)
        assert len(unique_lanes) == 3, f"Dishwasher should use 3 lanes, got {len(unique_lanes)}"

    def test_refrigerator_uses_2_lanes(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        fridge_placements = [p for p in result["placements"] if p["model_code"] == "LF29H8330S"]
        unique_lanes = set(p["lane"] for p in fridge_placements)
        assert len(unique_lanes) == 2, f"Refrigerator should use 2 lanes, got {len(unique_lanes)}"


class TestStackingRules:
    """Verify stacking logic."""

    def test_refrigerator_not_stacked(self, sample_order, master, truck_26ft):
        # Fridge is too tall to stack (1850 * 2 = 3700 > 2340 eff height)
        result = simulate(sample_order, master, truck_26ft)
        fridge_placements = [p for p in result["placements"] if p["model_code"] == "LF29H8330S"]
        layers = set(p["layer"] for p in fridge_placements)
        assert layers == {0}, "Refrigerator must not stack"

    def test_washer_dryer_stacked(self, sample_order, master, truck_26ft):
        # Same dim, stackable, fit under ceiling → should be 2-tier
        result = simulate(sample_order, master, truck_26ft)
        wash_dry = [p for p in result["placements"] if p["model_code"] in ("WM4000HWA", "DLEX4000W")]
        layers = set(p["layer"] for p in wash_dry)
        assert layers == {0, 1}, "Washer/dryer should be 2-tier"


class TestEdgeCases:
    """Edge cases."""

    def test_single_item(self, master, truck_26ft):
        order = [{"model_code": "LF29H8330S", "quantity": 1}]
        result = simulate(order, master, truck_26ft)
        assert result["fits"]
        assert result["fitted_count"] == 1

    def test_empty_order(self, master, truck_26ft):
        result = simulate([], master, truck_26ft)
        assert result["fits"]  # vacuously true
        assert result["fitted_count"] == 0
        assert result["metrics"]["x_used_mm"] == 0

    def test_oversized_order_unfitted(self, master, truck_26ft):
        # Request way too much
        order = [{"model_code": "LF29H8330S", "quantity": 100}]
        result = simulate(order, master, truck_26ft)
        assert result["fits"] is False
        assert result["unfitted_count"] > 0
