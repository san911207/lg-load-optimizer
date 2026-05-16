"""Smoke + behaviour tests for the SA refiner."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.sa_refiner import refine


@pytest.fixture(scope="module")
def sample_data():
    base = "/Users/sangkyu/projects/load_optimizer/data/sample_input.xlsx"
    master_df = pd.read_excel(base, "Model_Master")
    truck_df = pd.read_excel(base, "Truck_Master")
    loads = pd.read_excel(base, "Loads")
    master = master_df.set_index("model_code").to_dict("index")
    master["LDFN4542S"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    master["LWS3063ST"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    trucks = truck_df.set_index("truck_type").to_dict("index")
    return master, trucks, loads


def test_sa_runs_and_returns_result(sample_data):
    """SA must run end-to-end and produce non-trivial output."""
    master, trucks, _ = sample_data
    order = [
        {"model_code": "LDFN4542S", "quantity": 4},
        {"model_code": "LWS3063ST", "quantity": 3},
    ]
    r = refine(order, master, trucks["26ft"], time_budget_s=3.0, seed=7)
    assert r.iterations > 0
    assert len(r.placements) > 0
    assert r.initial_x_used_in >= r.x_used_in  # SA never worsens the best


def test_sa_never_worsens_heuristic(sample_data):
    """SA must never return a worse (fitted, x_used) than the heuristic seed.

    SA's objective penalises unfit items, so a refined solution may have a
    slightly longer x_used IF it manages to fit more items — that is an
    improvement, not a regression. The lexicographic check below captures
    that: SA must fit ≥ as many items, and at equal fit count must not
    increase trailer length.
    """
    master, trucks, loads = sample_data
    for lid in ("L001", "L002", "L003", "L004"):
        order = loads[loads["load_id"] == lid][["model_code", "quantity"]].to_dict("records")
        r = refine(order, master, trucks["26ft"], time_budget_s=4.0, seed=1)
        # fitted must be >= initial
        assert r.fitted_count >= r.initial_fitted_count, (
            f"SA fit fewer items on {lid}: init={r.initial_fitted_count} best={r.fitted_count}"
        )
        # if fit count is the same, x_used must not regress
        if r.fitted_count == r.initial_fitted_count:
            assert r.x_used_in <= r.initial_x_used_in + 0.01, (
                f"SA regressed on {lid} same-fit: init={r.initial_x_used_in} best={r.x_used_in}"
            )


def test_sa_improves_l001(sample_data):
    """L001 is known to benefit from SA — set a 5s budget and expect ≥1% gain."""
    master, trucks, loads = sample_data
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = refine(order, master, trucks["26ft"], time_budget_s=5.0, seed=42)
    gain_pct = (r.initial_x_used_in - r.x_used_in) / max(r.initial_x_used_in, 1e-6) * 100
    assert gain_pct >= 1.0, f"Expected SA to improve L001 by ≥1%, got {gain_pct:.2f}%"


def test_sa_deterministic_with_seed(sample_data):
    """Same seed + same budget should produce reproducible best-found."""
    master, trucks, _ = sample_data
    order = [
        {"model_code": "LDFN4542S", "quantity": 3},
        {"model_code": "LWS3063ST", "quantity": 3},
    ]
    r1 = refine(order, master, trucks["26ft"], time_budget_s=2.0, seed=11)
    r2 = refine(order, master, trucks["26ft"], time_budget_s=2.0, seed=11)
    assert abs(r1.x_used_in - r2.x_used_in) < 0.5, (
        f"SA non-deterministic with same seed: {r1.x_used_in} vs {r2.x_used_in}"
    )
