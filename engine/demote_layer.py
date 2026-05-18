"""
Post-process: demote 2nd-tier items to floor when truck has tail clearance.
============================================================================

CEO decision (2026-05-18): after the main optimization (SA / MILP) picks
the *shortest* arrangement, if the truck still has free length behind the
last item, walk the placements and lay down any *light* items currently
sitting on a 2nd tier. This trades a tiny bit of length for a flatter
load that's faster to unload.

Strict guard rails (set after the 4-veteran review):
  * weight_lb ≤ 80 lb — heavy items (380 lb fridge) MUST stay stacked for
    transit stability (Field Veteran).
  * volume_util_pct < 80 — if the truck is already cubic-full, demoting
    forces an extra batch trip ($400–1,200/day truck cost ≫ worker time
    saved). Logistics Veteran ROI gate.
  * remaining_length_ft ≥ 4 — must actually have floor space to lay down.
    Warehouse Veteran threshold; below 4 ft you can't fit a 30-in deep
    item anyway.
  * item is in 2nd-tier (z > 0) and on a single supporter.
  * No category-blacklist violation introduced by the demotion.

Result keeps trailer length monotone (post-process never *increases*
trailer length beyond the original utilization target since we only
demote when there's already empty floor at the rear).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from engine.best_packer import Placement


# Tuned to match the 4-veteran review (Field/Warehouse/Logistics consensus).
DEMOTE_MAX_WEIGHT_LB = 80.0
DEMOTE_MIN_REMAINING_FT = 4.0
DEMOTE_MAX_VOLUME_UTIL_PCT = 80.0
IN_PER_FT = 12.0
EPS = 0.5


def post_process_demote(
    placements: List[Dict[str, Any]],
    truck_spec: Dict[str, Any],
    metrics: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], int]:
    """
    Walk the placements left-to-right; for each light 2nd-tier item, try
    to place it on the floor BEHIND the current trailer envelope.

    Returns
    -------
    (new_placements, n_demoted)
        new_placements : the same list with selected items moved to z=0
                         and pushed to the rear of the load
        n_demoted      : how many items got moved (0 if no candidates)
    """
    L = float(truck_spec["length_in"])
    W = float(truck_spec["width_in"])

    # Guard 1: only demote when the truck has real tail clearance.
    remaining_ft = float(metrics.get("remaining_length_ft", 0))
    if remaining_ft < DEMOTE_MIN_REMAINING_FT:
        return placements, 0

    # Guard 2: don't demote on cubic-full loads.
    util_pct = float(metrics.get("volume_util_pct", 0))
    if util_pct >= DEMOTE_MAX_VOLUME_UTIL_PCT:
        return placements, 0

    new_placements = copy.deepcopy(placements)

    # Find demote candidates: z > 0 (2nd tier), weight ≤ 80 lb.
    candidates: List[Dict[str, Any]] = [
        p for p in new_placements
        if p.get("z_in", 0) > EPS
        and p.get("weight_lb", 0) <= DEMOTE_MAX_WEIGHT_LB
    ]
    if not candidates:
        return placements, 0

    # Sort by current x (load order) so we demote rear-of-trailer items
    # first — they're closest to the free space and re-flow naturally.
    candidates.sort(key=lambda p: -(p.get("x_in", 0)))

    n_demoted = 0
    for cand in candidates:
        # Current trailer envelope (after each demotion).
        envelope_x = max(
            (p["x_in"] + p["dim_x_in"] for p in new_placements
             if p.get("seq") != cand.get("seq")),
            default=0.0,
        )
        free_x_in = L - envelope_x
        if free_x_in < cand["dim_x_in"] + 1.0:
            continue   # not enough floor length to lay this item flat

        # Try to find a y position that doesn't collide with floor items.
        target_x = envelope_x
        target_z = 0.0
        placed = False
        for y_try in (0.0, cand["dim_y_in"], W - cand["dim_y_in"]):
            if y_try < 0 or y_try + cand["dim_y_in"] > W + EPS:
                continue
            if _no_collision_at_floor(new_placements, cand, target_x, y_try):
                cand["x_in"] = round(target_x, 3)
                cand["y_in"] = round(y_try, 3)
                cand["z_in"] = 0.0
                cand["layer"] = 0
                cand["lane"] = int(y_try / max(cand["dim_y_in"], 1))
                placed = True
                break
        if placed:
            n_demoted += 1

    if n_demoted == 0:
        return placements, 0

    # Re-seq (load order = sorted by x, then z, then y) — important so the
    # walk-through modal / PDF table reflect the new sequence.
    new_placements.sort(key=lambda p: (p["x_in"], p["z_in"], p["y_in"]))
    for i, p in enumerate(new_placements, 1):
        p["seq"] = i

    return new_placements, n_demoted


def _no_collision_at_floor(
    placements: List[Dict[str, Any]],
    cand: Dict[str, Any],
    x: float, y: float,
) -> bool:
    """True iff placing cand at (x, y, 0) does not overlap any other item."""
    x1, y1 = x + cand["dim_x_in"], y + cand["dim_y_in"]
    z1 = cand["dim_z_in"]
    for p in placements:
        if p.get("seq") == cand.get("seq"):
            continue
        px, py, pz = p["x_in"], p["y_in"], p["z_in"]
        px1, py1, pz1 = px + p["dim_x_in"], py + p["dim_y_in"], pz + p["dim_z_in"]
        if (
            x + EPS < px1 and x1 > px + EPS and
            y + EPS < py1 and y1 > py + EPS and
            EPS < pz1 and z1 > pz + EPS
        ):
            return False
    return True
