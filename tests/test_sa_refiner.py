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
    """SA must never return a worse OBJECTIVE than the heuristic seed.

    Phase C added a category-clustering term to the objective. As a
    result, SA may legitimately accept a longer x_used IF the improvement
    in cluster breaks (×8 in) outweighs the length cost. The contract
    is therefore on the *composite objective*, not raw x_used.
    """
    master, trucks, loads = sample_data
    for lid in ("L001", "L002", "L003", "L004"):
        order = loads[loads["load_id"] == lid][["model_code", "quantity"]].to_dict("records")
        r = refine(order, master, trucks["26ft"], time_budget_s=4.0, seed=1)
        # fitted must be >= initial (penalty term enforces this)
        assert r.fitted_count >= r.initial_fitted_count, (
            f"SA fit fewer items on {lid}: init={r.initial_fitted_count} best={r.fitted_count}"
        )
        # objective_value must not regress (length + 8·breaks combined)
        assert r.objective_value <= r.initial_objective_value + 0.5, (
            f"SA objective regressed on {lid}: "
            f"init={r.initial_objective_value:.2f} best={r.objective_value:.2f}"
        )


def test_sa_improves_l001(sample_data):
    """L001 is known to benefit from SA — set a 5s budget and expect ≥1% gain."""
    master, trucks, loads = sample_data
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = refine(order, master, trucks["26ft"], time_budget_s=5.0, seed=42)
    gain_pct = (r.initial_x_used_in - r.x_used_in) / max(r.initial_x_used_in, 1e-6) * 100
    assert gain_pct >= 1.0, f"Expected SA to improve L001 by ≥1%, got {gain_pct:.2f}%"


def test_sa_cluster_breaks_tracked(sample_data):
    """Phase C — SA must populate cluster_breaks / initial_cluster_breaks
    in SaResult so the UI can show the worker-efficiency improvement.
    """
    master, trucks, loads = sample_data
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = refine(order, master, trucks["26ft"], time_budget_s=4.0, seed=11)
    # Fields populated (not the default 0/0 from skipping the budget loop)
    assert r.initial_cluster_breaks > 0
    # Cluster breaks should not increase under the new objective
    assert r.cluster_breaks <= r.initial_cluster_breaks, (
        f"SA increased cluster breaks: init={r.initial_cluster_breaks} "
        f"best={r.cluster_breaks}"
    )


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
