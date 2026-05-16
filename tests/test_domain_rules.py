"""Tests for engine.domain_rules — pair detection + post-pack verification."""
from __future__ import annotations

from engine.domain_rules import (
    Severity,
    detect_pairs,
    expand_with_pair_hint,
    verify,
)


MASTER = {
    "WT8405CW": {"category": "Washer", "width_in": 29, "depth_in": 30, "height_in": 45, "weight_lb": 135, "fragile": False, "stackable": True},
    "WM4000HBA": {"category": "Washer", "width_in": 29, "depth_in": 33, "height_in": 41, "weight_lb": 170, "fragile": False, "stackable": True},
    "DLG8401BE": {"category": "Dryer",  "width_in": 29, "depth_in": 30, "height_in": 41, "weight_lb": 115, "fragile": False, "stackable": True},
    "DLEX4000B": {"category": "Dryer",  "width_in": 29, "depth_in": 33, "height_in": 41, "weight_lb": 120, "fragile": False, "stackable": True},
    "LRFXS3106": {"category": "Refrigerator", "width_in": 38, "depth_in": 38, "height_in": 70, "weight_lb": 380, "fragile": False, "stackable": False},
    "MV1825":    {"category": "Microwave", "width_in": 31, "depth_in": 18, "height_in": 18, "weight_lb": 45, "fragile": False, "stackable": True},
    "TV85":      {"category": "TV", "width_in": 85, "depth_in": 12, "height_in": 50, "weight_lb": 95, "fragile": True, "stackable": False},
    "PK305":     {"category": "Panel", "width_in": 31, "depth_in": 4, "height_in": 32, "weight_lb": 8, "fragile": False, "stackable": False},
}


def test_detect_pairs_basic_washer_dryer():
    order = [
        {"model_code": "WT8405CW", "quantity": 2},
        {"model_code": "DLG8401BE", "quantity": 2},
    ]
    pairs = detect_pairs(order, MASTER)
    assert ("WT8405CW", "DLG8401BE", 2) in pairs


def test_detect_pairs_respects_footprint():
    """WM4000HBA (29×33) shouldn't pair with DLG8401BE (29×30) — depths differ by 3 in."""
    order = [
        {"model_code": "WM4000HBA", "quantity": 1},
        {"model_code": "DLG8401BE", "quantity": 1},
    ]
    pairs = detect_pairs(order, MASTER)
    assert pairs == []  # depth diff 3 > tolerance 1.5


def test_detect_pairs_min_quantity():
    """Pair count is min(washer_qty, dryer_qty)."""
    order = [
        {"model_code": "WT8405CW", "quantity": 5},
        {"model_code": "DLG8401BE", "quantity": 3},
    ]
    pairs = detect_pairs(order, MASTER)
    assert pairs[0] == ("WT8405CW", "DLG8401BE", 3)


def test_expand_with_pair_hint_keeps_pairs_adjacent():
    """Reordered output has washer+dryer next to each other for adjacency in pack."""
    order = [
        {"model_code": "LRFXS3106", "quantity": 2},
        {"model_code": "WT8405CW", "quantity": 2},
        {"model_code": "MV1825", "quantity": 3},
        {"model_code": "DLG8401BE", "quantity": 2},
    ]
    new_order = expand_with_pair_hint(order, MASTER)
    codes = [o["model_code"] for o in new_order]
    # Washer + Dryer should be neighbours
    w_idx = codes.index("WT8405CW")
    d_idx = codes.index("DLG8401BE")
    assert abs(w_idx - d_idx) == 1


def test_expand_with_pair_hint_no_pairs_passthrough():
    """When no pair-eligible SKUs exist, order returns unchanged."""
    order = [
        {"model_code": "LRFXS3106", "quantity": 1},
        {"model_code": "MV1825", "quantity": 2},
    ]
    assert [o["model_code"] for o in expand_with_pair_hint(order, MASTER)] == ["LRFXS3106", "MV1825"]


def test_verify_fragile_blocks_overhead_stack():
    """A microwave stacked on a fragile TV → BLOCK finding."""
    placements = [
        {"seq": 1, "model_code": "TV85", "x_in": 0, "y_in": 0, "z_in": 0,
         "dim_x_in": 85, "dim_y_in": 12, "dim_z_in": 50, "weight_lb": 95},
        {"seq": 2, "model_code": "MV1825", "x_in": 0, "y_in": 0, "z_in": 50,
         "dim_x_in": 31, "dim_y_in": 18, "dim_z_in": 18, "weight_lb": 45},
    ]
    findings = verify(placements, MASTER)
    block = [f for f in findings if f.severity == Severity.BLOCK]
    assert len(block) == 1
    assert block[0].rule == "fragile_no_overhead"


def test_verify_category_blacklist_warns():
    """Microwave directly on top of refrigerator → WARN finding."""
    placements = [
        {"seq": 1, "model_code": "LRFXS3106", "x_in": 0, "y_in": 0, "z_in": 0,
         "dim_x_in": 38, "dim_y_in": 38, "dim_z_in": 70, "weight_lb": 380},
        {"seq": 2, "model_code": "MV1825", "x_in": 0, "y_in": 0, "z_in": 70,
         "dim_x_in": 31, "dim_y_in": 18, "dim_z_in": 18, "weight_lb": 45},
    ]
    findings = verify(placements, MASTER)
    warn = [f for f in findings if f.severity == Severity.WARN]
    assert len(warn) == 1
    assert warn[0].rule == "category_blacklist"


def test_verify_clean_load_no_findings():
    """A simple unstacked load triggers no rule violations."""
    placements = [
        {"seq": 1, "model_code": "WT8405CW", "x_in": 0, "y_in": 0, "z_in": 0,
         "dim_x_in": 30, "dim_y_in": 29, "dim_z_in": 45, "weight_lb": 135},
        {"seq": 2, "model_code": "LRFXS3106", "x_in": 30, "y_in": 0, "z_in": 0,
         "dim_x_in": 38, "dim_y_in": 38, "dim_z_in": 70, "weight_lb": 380},
    ]
    assert verify(placements, MASTER) == []
