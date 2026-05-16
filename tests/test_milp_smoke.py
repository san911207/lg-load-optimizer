"""Smoke tests for the MILP solver (Day 1).

Verifies the formulation runs end-to-end on small loads (3 to 10 items)
and produces non-overlapping, in-bounds placements. Larger-N performance
is tracked separately — these tests exist to prove the pipeline works.
"""
from __future__ import annotations

import pytest

from engine.milp_solver import milp_solve


# Small truck for fast tests
SMALL_TRUCK = {
    "length_in": 120.0,   # 10 ft
    "width_in": 80.0,
    "height_in": 80.0,
    "max_payload_lb": 5000,
    "cargo_volume_cft": 444,
}

TINY_MASTER = {
    "BOX_A": {"width_in": 30, "depth_in": 30, "height_in": 30, "weight_lb": 50,
              "stackable": True, "category": "test"},
    "BOX_B": {"width_in": 40, "depth_in": 40, "height_in": 40, "weight_lb": 80,
              "stackable": True, "category": "test"},
    "BOX_C": {"width_in": 20, "depth_in": 20, "height_in": 20, "weight_lb": 20,
              "stackable": True, "category": "test"},
}


def _no_overlap_and_in_bounds(result, truck):
    """Pairwise non-overlap + truck-bound checks."""
    L, W, H = truck["length_in"], truck["width_in"], truck["height_in"]
    EPS = 0.05
    for p in result.placements:
        assert p["x_in"] + p["dim_x_in"] <= L + EPS, f"x bound: {p}"
        assert p["y_in"] + p["dim_y_in"] <= W + EPS, f"y bound: {p}"
        assert p["z_in"] + p["dim_z_in"] <= H + EPS, f"z bound: {p}"
    for i, p1 in enumerate(result.placements):
        for p2 in result.placements[i + 1:]:
            overlap = (
                p1["x_in"] + EPS < p2["x_in"] + p2["dim_x_in"] and
                p1["x_in"] + p1["dim_x_in"] > p2["x_in"] + EPS and
                p1["y_in"] + EPS < p2["y_in"] + p2["dim_y_in"] and
                p1["y_in"] + p1["dim_y_in"] > p2["y_in"] + EPS and
                p1["z_in"] + EPS < p2["z_in"] + p2["dim_z_in"] and
                p1["z_in"] + p1["dim_z_in"] > p2["z_in"] + EPS
            )
            assert not overlap, f"overlap: {p1} ↔ {p2}"


def test_milp_solves_three_box_load():
    """3 BOX_A items in a small truck. Should fit linearly along x."""
    order = [{"model_code": "BOX_A", "quantity": 3}]
    r = milp_solve(order, TINY_MASTER, SMALL_TRUCK, time_limit_s=30)
    assert r.fits is True
    assert r.fitted_count == 3
    _no_overlap_and_in_bounds(r, SMALL_TRUCK)


def test_milp_minimizes_trailer_length():
    """3 stacked BOX_A items should use 30 in (1 row), not 90 (3 rows)."""
    order = [{"model_code": "BOX_A", "quantity": 3}]
    r = milp_solve(order, TINY_MASTER, SMALL_TRUCK, time_limit_s=30)
    assert r.x_used_in <= 35.0, (
        f"Expected solver to stack/pair into <=35 in, got {r.x_used_in}"
    )


def test_milp_mixed_sizes():
    """Mixed BOX_A + BOX_B + BOX_C — should still solve and produce valid placements."""
    order = [
        {"model_code": "BOX_A", "quantity": 2},
        {"model_code": "BOX_B", "quantity": 1},
        {"model_code": "BOX_C", "quantity": 2},
    ]
    r = milp_solve(order, TINY_MASTER, SMALL_TRUCK, time_limit_s=60)
    assert r.fits is True
    assert r.fitted_count == 5
    _no_overlap_and_in_bounds(r, SMALL_TRUCK)


def test_milp_returns_status_label():
    order = [{"model_code": "BOX_A", "quantity": 2}]
    r = milp_solve(order, TINY_MASTER, SMALL_TRUCK, time_limit_s=10)
    assert r.status in {"Optimal", "Time-limit"}
    assert r.solve_time_s >= 0
    assert r.objective_value > 0


def test_milp_infeasible_load():
    """One BOX_B (40 in deep) in a truck only 30 in long — must be infeasible."""
    micro_truck = {**SMALL_TRUCK, "length_in": 30.0}
    order = [{"model_code": "BOX_B", "quantity": 1}]
    r = milp_solve(order, TINY_MASTER, micro_truck, time_limit_s=10)
    # CBC may either prove infeasible or return Optimal at boundary;
    # but if it claims it fits, the placement must respect bounds.
    if r.fits:
        _no_overlap_and_in_bounds(r, micro_truck)
    else:
        assert r.status in {"Infeasible", "Not Solved", "Undefined"}
