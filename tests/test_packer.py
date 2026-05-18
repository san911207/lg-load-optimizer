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

from engine.best_packer import simulate, pair_pack, find_best, DOOR_TRACK_LOSS_IN


@pytest.fixture
def master():
    xl = Path(__file__).resolve().parent.parent / "data" / "sample_input.xlsx"
    df = pd.read_excel(xl, sheet_name="Model_Master")
    m = df.set_index("model_code").to_dict("index")
    # Apply known calibrations (US units)
    m["DISH-001"]["stackable"] = True
    m["DISH-001"]["load_bear_lb"] = 132.3
    m["DISH-001"]["fragile"] = False
    m["WOVEN-001"]["stackable"] = True
    m["WOVEN-001"]["load_bear_lb"] = 198.4
    m["WOVEN-001"]["fragile"] = False
    return m


@pytest.fixture
def truck_26ft():
    return {
        "length_in": 312.01,
        "width_in": 95.98,
        "height_in": 101.97,
        "max_payload_lb": 9921,
        "cargo_volume_cft": 1767.1,
    }


@pytest.fixture
def truck_53ft():
    return {
        "length_in": 635.98,
        "width_in": 102.01,
        "height_in": 106.30,
        "max_payload_lb": 44092,
        "cargo_volume_cft": 3990.9,
    }


@pytest.fixture
def sample_order():
    return [
        {"model_code": "FRIDGE-FD-001", "quantity": 6},
        {"model_code": "WASHER-FL-001",  "quantity": 8},
        {"model_code": "DRYER-EL-001",  "quantity": 8},
        {"model_code": "DISH-001",  "quantity": 10},
        {"model_code": "WOVEN-001",  "quantity": 4},
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
        assert result["metrics"]["remaining_length_in"] > 12   # >1 ft buffer

    def test_no_overlapping_boxes(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        placements = result["placements"]
        # Tolerance for float boundary cases (boxes touching face-to-face are not
        # overlapping; rounding of 2-decimal inches can put neighbors 0.005 in apart).
        EPS = 0.05
        for i, p1 in enumerate(placements):
            for p2 in placements[i+1:]:
                if (p1["x_in"] + EPS < p2["x_in"] + p2["dim_x_in"] and
                    p1["x_in"] + p1["dim_x_in"] > p2["x_in"] + EPS and
                    p1["y_in"] + EPS < p2["y_in"] + p2["dim_y_in"] and
                    p1["y_in"] + p1["dim_y_in"] > p2["y_in"] + EPS and
                    p1["z_in"] + EPS < p2["z_in"] + p2["dim_z_in"] and
                    p1["z_in"] + p1["dim_z_in"] > p2["z_in"] + EPS):
                    pytest.fail(
                        f"Overlap: seq {p1['seq']} ({p1['model_code']}) "
                        f"and seq {p2['seq']} ({p2['model_code']})"
                    )

    def test_all_boxes_within_truck(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        for p in result["placements"]:
            assert p["x_in"] + p["dim_x_in"] <= truck_26ft["length_in"] + 0.01, \
                f"Box {p['seq']} overflows length"
            assert p["y_in"] + p["dim_y_in"] <= truck_26ft["width_in"] + 0.01, \
                f"Box {p['seq']} overflows width"
            # Height must clear door track
            eff_height = truck_26ft["height_in"] - DOOR_TRACK_LOSS_IN
            assert p["z_in"] + p["dim_z_in"] <= eff_height + 0.01, \
                f"Box {p['seq']} hits door track"


class TestLaneUtilization:
    """Verify max lane count for each model."""

    def test_max_lanes_for_washer(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        washer_placements = [p for p in result["placements"] if p["model_code"] == "WASHER-FL-001"]
        unique_lanes = set(p["lane"] for p in washer_placements)
        assert len(unique_lanes) == 3, f"Washer should use 3 lanes, got {len(unique_lanes)}"

    def test_max_lanes_for_dishwasher(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        dish_placements = [p for p in result["placements"] if p["model_code"] == "DISH-001"]
        unique_lanes = set(p["lane"] for p in dish_placements)
        assert len(unique_lanes) == 3, f"Dishwasher should use 3 lanes, got {len(unique_lanes)}"

    def test_refrigerator_uses_2_lanes(self, sample_order, master, truck_26ft):
        result = simulate(sample_order, master, truck_26ft)
        fridge_placements = [p for p in result["placements"] if p["model_code"] == "FRIDGE-FD-001"]
        unique_lanes = set(p["lane"] for p in fridge_placements)
        assert len(unique_lanes) == 2, f"Refrigerator should use 2 lanes, got {len(unique_lanes)}"


class TestStackingRules:
    """Verify stacking logic."""

    def test_refrigerator_not_stacked(self, sample_order, master, truck_26ft):
        # Fridge is too tall to stack (1850mm = 72.8in × 2 = 145.6 > eff 91.97)
        result = simulate(sample_order, master, truck_26ft)
        fridge_placements = [p for p in result["placements"] if p["model_code"] == "FRIDGE-FD-001"]
        layers = set(p["layer"] for p in fridge_placements)
        assert layers == {0}, "Refrigerator must not stack"

    def test_washer_dryer_stacked(self, sample_order, master, truck_26ft):
        # Same dim, stackable, fit under ceiling → ≥2-tier
        result = simulate(sample_order, master, truck_26ft)
        wash_dry = [p for p in result["placements"] if p["model_code"] in ("WASHER-FL-001", "DRYER-EL-001")]
        layers = set(p["layer"] for p in wash_dry)
        assert 0 in layers and 1 in layers, "Washer/dryer should be at least 2-tier"


class TestEdgeCases:
    """Edge cases."""

    def test_single_item(self, master, truck_26ft):
        order = [{"model_code": "FRIDGE-FD-001", "quantity": 1}]
        result = simulate(order, master, truck_26ft)
        assert result["fits"]
        assert result["fitted_count"] == 1

    def test_empty_order(self, master, truck_26ft):
        result = simulate([], master, truck_26ft)
        assert result["fits"]  # vacuously true
        assert result["fitted_count"] == 0
        assert result["metrics"]["x_used_in"] == 0

    def test_oversized_order_unfitted(self, master, truck_26ft):
        # Request way too much
        order = [{"model_code": "FRIDGE-FD-001", "quantity": 100}]
        result = simulate(order, master, truck_26ft)
        assert result["fits"] is False
        assert result["unfitted_count"] > 0
