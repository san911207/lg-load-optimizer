"""Unit tests for the natural-language explainer."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.explain import Reason, explain, explain_html
from engine.router import solve


MASTER_FAKE = {
    "FRIDGE":  {"category": "Refrigerator", "width_in": 36, "depth_in": 30, "height_in": 68, "weight_lb": 280, "fragile": True, "stackable": False},
    "WASHER":  {"category": "Washer",       "width_in": 29, "depth_in": 30, "height_in": 45, "weight_lb": 135, "fragile": False, "stackable": True},
    "DRYER":   {"category": "Dryer",        "width_in": 29, "depth_in": 30, "height_in": 41, "weight_lb": 115, "fragile": False, "stackable": True},
    "MICRO":   {"category": "Microwave",    "width_in": 30, "depth_in": 18, "height_in": 18, "weight_lb": 45,  "fragile": False, "stackable": True},
    "TOWER":   {"category": "SKS_Column",   "width_in": 32, "depth_in": 26, "height_in": 88, "weight_lb": 420, "fragile": False, "stackable": False},
}
TRUCK = {"length_in": 311.0, "width_in": 97.0, "height_in": 97.0, "max_payload_lb": 10000, "cargo_volume_cft": 1700}


def test_explain_heavy_bottom_bullet():
    """Heavy items on z=0 → success bullet."""
    placements = [
        {"seq": 1, "model_code": "FRIDGE", "x_in": 0, "y_in": 0, "z_in": 0,
         "dim_x_in": 30, "dim_y_in": 36, "dim_z_in": 68, "weight_lb": 280},
        {"seq": 2, "model_code": "TOWER", "x_in": 30, "y_in": 0, "z_in": 0,
         "dim_x_in": 26, "dim_y_in": 32, "dim_z_in": 88, "weight_lb": 420},
    ]
    result = {
        "placements": placements,
        "metrics": {"x_used_in": 56, "x_used_ft": 4.7},
        "engine": "Heuristic+SA",
        "pair_count": 0, "audit_block_count": 0, "audit_warn_count": 0,
    }
    reasons = explain(result, MASTER_FAKE, TRUCK)
    labels = [r.label for r in reasons]
    assert any("Heavy items on bottom" in l for l in labels)


def test_explain_tall_to_front():
    """Tall items in front zone → success bullet."""
    placements = [
        {"seq": 1, "model_code": "TOWER", "x_in": 0, "y_in": 0, "z_in": 0,
         "dim_x_in": 26, "dim_y_in": 32, "dim_z_in": 88, "weight_lb": 420},
    ]
    result = {
        "placements": placements,
        "metrics": {"x_used_in": 26, "x_used_ft": 2.2},
        "engine": "Heuristic+SA",
        "pair_count": 0, "audit_block_count": 0, "audit_warn_count": 0,
    }
    reasons = explain(result, MASTER_FAKE, TRUCK)
    assert any("front" in r.label.lower() for r in reasons)


def test_explain_optimal_milp_bullet():
    """Provably-optimal result mentions MILP proof."""
    result = {
        "placements": [
            {"seq": 1, "model_code": "MICRO", "x_in": 0, "y_in": 0, "z_in": 0,
             "dim_x_in": 18, "dim_y_in": 30, "dim_z_in": 18, "weight_lb": 45}
        ],
        "metrics": {"x_used_in": 18, "x_used_ft": 1.5},
        "engine": "MILP",
        "is_provable_optimal": True,
        "pair_count": 0, "audit_block_count": 0, "audit_warn_count": 0,
    }
    reasons = explain(result, MASTER_FAKE, TRUCK)
    # Phase B D4 — jargon-free copy: "Mathematically optimal" → "Proven shortest"
    assert any(
        ("optimal" in r.label.lower()) or ("shortest" in r.label.lower())
        for r in reasons
    )


def test_explain_pair_bullet():
    result = {
        "placements": [
            {"seq": 1, "model_code": "WASHER", "x_in": 0, "y_in": 0, "z_in": 0,
             "dim_x_in": 30, "dim_y_in": 29, "dim_z_in": 45, "weight_lb": 135},
            {"seq": 2, "model_code": "DRYER", "x_in": 0, "y_in": 0, "z_in": 45,
             "dim_x_in": 30, "dim_y_in": 29, "dim_z_in": 41, "weight_lb": 115},
        ],
        "metrics": {"x_used_in": 30, "x_used_ft": 2.5},
        "engine": "Heuristic+SA",
        "pair_count": 1,
        "audit_block_count": 0, "audit_warn_count": 0,
    }
    reasons = explain(result, MASTER_FAKE, TRUCK)
    assert any("Pair" in r.label or "pair" in r.label.lower() for r in reasons)


def test_explain_audit_block_warn():
    result = {
        "placements": [],
        "metrics": {"x_used_in": 0, "x_used_ft": 0},
        "engine": "Heuristic",
        "audit_block_count": 2,
        "audit_warn_count": 0,
        "pair_count": 0,
    }
    reasons = explain(result, MASTER_FAKE, TRUCK)
    # Phase B D4 — jargon-free: "Audit BLOCKs detected" → "Loading rule violation(s)"
    assert any(
        ("BLOCK" in r.label) or ("violation" in r.label.lower())
        for r in reasons
    )


def test_explain_html_renders_reasons():
    reasons = [
        Reason("Test label", "Test detail line", "success"),
        Reason("Warn label", "Warn line", "warn"),
    ]
    html = explain_html(reasons)
    assert "Test label" in html
    assert "Warn label" in html
    assert "ECFDF5" in html  # success bg colour present


def test_explain_returns_at_least_one_bullet():
    """Even a trivial load → fallback explanation bullet."""
    result = {
        "placements": [
            {"seq": 1, "model_code": "MICRO", "x_in": 0, "y_in": 0, "z_in": 0,
             "dim_x_in": 18, "dim_y_in": 30, "dim_z_in": 18, "weight_lb": 45}
        ],
        "metrics": {"x_used_in": 18, "x_used_ft": 1.5},
        "engine": "Heuristic",
        "pair_count": 0, "audit_block_count": 0, "audit_warn_count": 0,
    }
    reasons = explain(result, MASTER_FAKE, TRUCK)
    assert len(reasons) >= 1
