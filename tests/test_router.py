"""Router smoke tests — verify engine auto-selection and result envelope."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.router import solve, MILP_MAX_ITEMS


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


def test_router_picks_milp_for_small_load(sample_data):
    """Loads with ≤ MILP_MAX_ITEMS items should use MILP (or fall back gracefully)."""
    master, trucks, _ = sample_data
    # 5 items — small enough for MILP
    order = [{"model_code": "LDFN4542S", "quantity": 3},
             {"model_code": "LWS3063ST", "quantity": 2}]
    r = solve(order, master, trucks["26ft"], time_budget_s=30)
    assert r["fits"] is True
    assert r["engine"] in {"MILP", "Heuristic+SA(skel)", "Heuristic"}
    assert "metrics" in r
    assert r["metrics"]["x_used_ft"] > 0


def test_router_picks_sa_for_medium_load(sample_data):
    """L001 (44 items) is too big for MILP — routes to Heuristic+SA refiner."""
    master, trucks, loads = sample_data
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = solve(order, master, trucks["26ft"], time_budget_s=15)
    assert r["fits"] is True
    assert r["fitted_count"] == r["requested_count"]
    assert r["engine"] == "Heuristic+SA"
    # SA must do at least as well as the heuristic baseline.
    assert r["metrics"]["x_used_ft"] <= 22.85  # heuristic seed on this load is ~22.83


def test_router_envelope_compatible(sample_data):
    """Result envelope must match simulate() so app.py and pdf_gen stay agnostic."""
    master, trucks, loads = sample_data
    order = loads[loads["load_id"] == "L004"][["model_code", "quantity"]].to_dict("records")
    r = solve(order, master, trucks["26ft"], time_budget_s=30)
    # Required keys present (mirrors simulate()'s contract)
    for key in ("fits", "fitted_count", "requested_count", "placements",
                "unfitted_detail", "metrics", "engine"):
        assert key in r
    for mk in ("x_used_ft", "compactness_pct", "volume_util_pct",
               "weight_total_lb", "weight_util_pct"):
        assert mk in r["metrics"]


def test_router_force_heuristic(sample_data):
    """force_engine='heuristic' should bypass MILP even on a small load."""
    master, trucks, _ = sample_data
    order = [{"model_code": "LDFN4542S", "quantity": 3}]
    r = solve(order, master, trucks["26ft"], time_budget_s=30, force_engine="heuristic")
    assert r["engine"] == "Heuristic"
    assert r["fits"] is True
