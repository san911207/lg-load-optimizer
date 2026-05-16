"""
Engine router — picks the right packer for each load.
======================================================

The v2 engine stack has three layers (L1 MILP, L2 SA, L3 Heuristic) with
different speed/quality trade-offs.  This module exposes a single
``solve()`` entry point that auto-selects the best engine based on item
count and time budget.

Routing decision table:

    items <= 15            → L1 MILP (proves optimal in <30 s)
    16 <= items <= 300     → L3 Heuristic + L2 SA refiner
    items > 300            → L3 Heuristic only (no refinement, instant)

Result envelope keeps a stable shape (``simulate``-compatible) so the
Streamlit app and PDF generator don't care which engine produced it.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from engine.best_packer import simulate as heuristic_simulate
from engine.milp_solver import milp_solve, MILP_MAX_ITEMS
from engine.sa_refiner import refine as sa_refine


SA_MAX_ITEMS = 300
SA_DEFAULT_BUDGET_S = 12.0


def solve(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
    time_budget_s: float = 30.0,
    force_engine: str | None = None,
) -> Dict[str, Any]:
    """
    Pack ``order_lines`` into ``truck_spec`` using the best available engine.

    Parameters
    ----------
    order_lines :
        ``[{"model_code": str, "quantity": int}, ...]``
    master :
        SKU dim/weight lookup keyed by ``model_code``.
    truck_spec :
        Truck dimensions (``length_in``, ``width_in``, ``height_in``,
        ``max_payload_lb``, ``cargo_volume_cft``).
    time_budget_s :
        Wallclock seconds the auto-router is allowed to spend. The actual
        budget per engine layer is derived from this.
    force_engine :
        Override the auto-selection with one of ``"milp"``, ``"sa"``,
        ``"heuristic"``. Used by tests and the engine-selector UI.

    Returns
    -------
    A dict with the standard ``simulate()`` shape plus an ``engine`` key
    indicating which layer produced the result.
    """
    start = time.monotonic()
    n_items = sum(int(ol["quantity"]) for ol in order_lines)

    engine_choice = force_engine or _auto_pick(n_items)

    if engine_choice == "milp":
        m = milp_solve(order_lines, master, truck_spec, time_limit_s=time_budget_s)
        if m.fits and m.fitted_count == n_items:
            return _wrap_milp(m, order_lines, master, truck_spec, "MILP")
        # MILP couldn't solve in budget — fall back to SA/heuristic.
        engine_choice = "sa" if n_items <= SA_MAX_ITEMS else "heuristic"

    if engine_choice == "sa":
        # Run heuristic for the envelope, then let SA refine the *order*.
        heur = heuristic_simulate(order_lines, master, truck_spec)
        sa_budget = max(2.0, time_budget_s - (time.monotonic() - start))
        sa = sa_refine(order_lines, master, truck_spec,
                       time_budget_s=min(sa_budget, SA_DEFAULT_BUDGET_S))
        # If SA found a strict improvement, swap placements + metrics in.
        return _wrap_sa(heur, sa, truck_spec, elapsed=time.monotonic() - start)

    # heuristic
    heur = heuristic_simulate(order_lines, master, truck_spec)
    return _wrap_heuristic(heur, "Heuristic", elapsed=time.monotonic() - start)


def _auto_pick(n_items: int) -> str:
    if n_items <= MILP_MAX_ITEMS:
        return "milp"
    if n_items <= SA_MAX_ITEMS:
        return "sa"
    return "heuristic"


def _wrap_milp(
    m,
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
    engine_label: str,
) -> Dict[str, Any]:
    """Convert MilpResult to the same dict shape simulate() returns."""
    # We need the volume/weight aggregates that simulate() produces, but
    # MilpResult already has placements with dims. Compute them inline.
    L = truck_spec["length_in"]
    used_vol_cuin = sum(p["dim_x_in"] * p["dim_y_in"] * p["dim_z_in"] for p in m.placements)
    used_vol_cft = used_vol_cuin / 1728.0
    cargo_cft = truck_spec.get("cargo_volume_cft", L * truck_spec["width_in"] * truck_spec["height_in"] / 1728.0)
    weight_total = sum(p["weight_lb"] for p in m.placements)
    return {
        "fits": m.fits,
        "fitted_count": m.fitted_count,
        "requested_count": m.requested_count,
        "placements": m.placements,
        "unfitted_detail": m.unfitted_detail,
        "engine": engine_label,
        "status": m.status,
        "is_provable_optimal": m.is_provable_optimal,
        "solve_time_s": m.solve_time_s,
        "metrics": {
            "x_used_in": m.x_used_in,
            "x_used_ft": round(m.x_used_in / 12.0, 2),
            "compactness_pct": round(m.x_used_in / L * 100, 2),
            "volume_loaded_cft": round(used_vol_cft, 2),
            "volume_util_pct": round(used_vol_cft / cargo_cft * 100, 2),
            "weight_total_lb": round(weight_total, 1),
            "weight_util_pct": round(weight_total / truck_spec["max_payload_lb"] * 100, 2),
            "remaining_length_in": round(L - m.x_used_in, 2),
            "remaining_length_ft": round((L - m.x_used_in) / 12.0, 2),
        },
    }


def _wrap_heuristic(heur, engine_label: str, elapsed: float) -> Dict[str, Any]:
    out = dict(heur)
    out["engine"] = engine_label
    out["status"] = "Heuristic"
    out["is_provable_optimal"] = False
    out["solve_time_s"] = elapsed
    return out


def _wrap_sa(heur: Dict[str, Any], sa, truck_spec: Dict[str, Any], elapsed: float) -> Dict[str, Any]:
    """
    Merge heuristic envelope with SA's refined placements. SA returns ``Placement``
    objects (from ``engine.best_packer``); the rest of the app expects the dict
    form ``simulate()`` emits, so we convert.
    """
    L = truck_spec["length_in"]
    placements_dict = [
        {
            "seq": idx + 1,
            "model_code": p.model_code,
            "category": "",  # filled in from master upstream if needed
            "x_in": round(p.x, 3),
            "y_in": round(p.y, 3),
            "z_in": round(p.z, 3),
            "dim_x_in": p.dim_x,
            "dim_y_in": p.dim_y,
            "dim_z_in": p.dim_z,
            "weight_lb": p.weight_lb,
        }
        for idx, p in enumerate(sa.placements)
    ]

    used_vol_cuin = sum(p.dim_x * p.dim_y * p.dim_z for p in sa.placements)
    used_vol_cft = used_vol_cuin / 1728.0
    cargo_cft = truck_spec.get(
        "cargo_volume_cft",
        L * truck_spec["width_in"] * truck_spec["height_in"] / 1728.0,
    )
    weight_total = sum(p.weight_lb for p in sa.placements)

    out = dict(heur)
    out["placements"] = placements_dict
    out["fitted_count"] = len(placements_dict)
    out["fits"] = sa.x_used_in < L
    out["engine"] = "Heuristic+SA"
    out["status"] = "Refined"
    out["is_provable_optimal"] = False
    out["solve_time_s"] = elapsed
    out["sa_iterations"] = sa.iterations
    out["sa_improved"] = sa.improved
    out["sa_initial_x_used_in"] = round(sa.initial_x_used_in, 3)
    out["metrics"] = {
        "x_used_in": round(sa.x_used_in, 2),
        "x_used_ft": round(sa.x_used_in / 12.0, 2),
        "compactness_pct": round(sa.x_used_in / L * 100, 2),
        "volume_loaded_cft": round(used_vol_cft, 2),
        "volume_util_pct": round(used_vol_cft / cargo_cft * 100, 2),
        "weight_total_lb": round(weight_total, 1),
        "weight_util_pct": round(weight_total / truck_spec["max_payload_lb"] * 100, 2),
        "remaining_length_in": round(L - sa.x_used_in, 2),
        "remaining_length_ft": round((L - sa.x_used_in) / 12.0, 2),
    }
    return out
