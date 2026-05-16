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
from engine.domain_rules import (
    Severity,
    detect_pairs,
    expand_with_pair_hint,
    verify,
)
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

    # ── Pre-pack: pair-pack hint so washer+dryer stay adjacent ──────────
    pair_info = detect_pairs(order_lines, master)
    pack_lines = expand_with_pair_hint(order_lines, master) if pair_info else list(order_lines)

    engine_choice = force_engine or _auto_pick(n_items)

    if engine_choice == "milp":
        m = milp_solve(pack_lines, master, truck_spec, time_limit_s=time_budget_s)
        # Use MILP only if it found a complete, valid arrangement. Time-limit
        # results with partial fit (or solver-unavailable / infeasible /
        # not-solved) fall through to the SA path so the dispatcher always
        # gets a usable envelope (QA Lead audit finding #4).
        milp_complete = (
            m.fits
            and m.fitted_count == n_items
            and m.status in {"Optimal", "Time-limit"}
        )
        if milp_complete:
            return _attach_audit(
                _wrap_milp(m, pack_lines, master, truck_spec, "MILP"),
                pair_info, master,
            )
        # MILP couldn't solve in budget — fall back to SA/heuristic.
        engine_choice = "sa" if n_items <= SA_MAX_ITEMS else "heuristic"

    if engine_choice == "sa":
        # Run heuristic for the envelope, then let SA refine the *order*.
        heur = heuristic_simulate(pack_lines, master, truck_spec)
        sa_budget = max(2.0, time_budget_s - (time.monotonic() - start))
        sa = sa_refine(pack_lines, master, truck_spec,
                       time_budget_s=min(sa_budget, SA_DEFAULT_BUDGET_S))
        return _attach_audit(
            _wrap_sa(heur, sa, truck_spec, elapsed=time.monotonic() - start, master=master),
            pair_info, master,
        )

    # heuristic
    heur = heuristic_simulate(pack_lines, master, truck_spec)
    return _attach_audit(
        _wrap_heuristic(heur, "Heuristic", elapsed=time.monotonic() - start),
        pair_info, master,
    )


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
        "unfitted_count": m.requested_count - m.fitted_count,
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


def _attach_audit(
    result: Dict[str, Any],
    pair_info: List,
    master: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Run post-pack verification and attach the findings + pair count."""
    findings = verify(result.get("placements", []), master)
    result["pair_count"] = sum(p[2] for p in pair_info)
    result["audit_findings"] = [
        {
            "rule": f.rule,
            "severity": f.severity.value,
            "message": f.message,
            "seq_above": f.seq_above,
            "seq_below": f.seq_below,
        }
        for f in findings
    ]
    result["audit_block_count"] = sum(1 for f in findings if f.severity == Severity.BLOCK)
    result["audit_warn_count"] = sum(1 for f in findings if f.severity == Severity.WARN)
    return result


def _wrap_heuristic(heur, engine_label: str, elapsed: float) -> Dict[str, Any]:
    out = dict(heur)
    out["engine"] = engine_label
    out["status"] = "Heuristic"
    out["is_provable_optimal"] = False
    out["solve_time_s"] = elapsed
    return out


def _wrap_sa(
    heur: Dict[str, Any], sa, truck_spec: Dict[str, Any],
    elapsed: float,
    master: Dict[str, Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Merge heuristic envelope with SA's refined placements. SA returns ``Placement``
    objects (from ``engine.best_packer``); the rest of the app expects the dict
    form ``simulate()`` emits, so we convert.
    """
    L = truck_spec["length_in"]
    # IMPORTANT — category MUST come from master so domain_rules.verify
    # category-blacklist check fires on SA-routed loads (the 16-300 item
    # range, which is exactly where mixed Microwave/Fridge stacking
    # violations occur). Eng Lead audit finding #2.
    def _cat_for(mc: str) -> str:
        if master is None:
            return ""
        return master.get(mc, {}).get("category", "")

    placements_dict = [
        {
            "seq": idx + 1,
            "model_code": p.model_code,
            "category": _cat_for(p.model_code),
            "x_in": round(p.x, 3),
            "y_in": round(p.y, 3),
            "z_in": round(p.z, 3),
            "dim_x_in": p.dim_x,
            "dim_y_in": p.dim_y,
            "dim_z_in": p.dim_z,
            "weight_lb": p.weight_lb,
            "lane": p.lane,
            "layer": p.layer,
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
    # Recompute unfitted_count from the heuristic seed's requested count.
    requested = out.get("requested_count", len(placements_dict))
    out["unfitted_count"] = max(0, requested - len(placements_dict))
    out["fits"] = (out["unfitted_count"] == 0) and (sa.x_used_in <= L + 0.01)
    out["engine"] = "Heuristic+SA"
    out["status"] = "Refined"
    out["is_provable_optimal"] = False
    out["solve_time_s"] = elapsed
    out["sa_iterations"] = sa.iterations
    out["sa_improved"] = sa.improved
    out["sa_initial_x_used_in"] = round(sa.initial_x_used_in, 3)
    out["sa_cluster_breaks"] = sa.cluster_breaks
    out["sa_initial_cluster_breaks"] = sa.initial_cluster_breaks
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
