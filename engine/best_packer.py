"""
=============================================================================
Best-Pack Engine — LG Appliance Truck Loading Optimizer
=============================================================================

Pair-packing algorithm with auto-strategy selection.

Units (US, post-2026-05-15 refactor):
  - All linear dims in inches (in)
  - Weight in pounds (lb)
  - Volume in cubic feet (cft)
  - Door track loss = 10 inches at top of rear

Key features:
  - Groups same-dim models together (e.g. washer + dryer paired)
  - Forces max lane utilization (truck_width // box_width)
  - Handles upright-only constraint (this side up)
  - Tries both horizontal orientations (w↔d) — picks shortest length
  - Multiple sort strategies, auto-picks best (max units fitted, min length)
  - Max-fit mode: only `stackable` flag gates tier stacking;
    `load_bear_lb` and `fragile` are ignored (CEO decision 2026-05-15).

Author: Sangkyu / LG Electronics US SCM
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, field


DOOR_TRACK_LOSS_IN = 10  # Roll-up door track at top of rear (~5ft × 10in)
IN_PER_FT = 12.0
CUIN_PER_CFT = 1728.0  # 12³


@dataclass
class Placement:
    """Single box placement in the truck (all dims in inches, weight in lb)."""
    seq: int
    model_code: str
    x: float          # length-wise position (in, from cab)
    y: float          # width-wise position (in)
    z: float          # height position (in)
    dim_x: float      # depth along truck length (in)
    dim_y: float      # width along truck width (in)
    dim_z: float      # height (in)
    weight_lb: float
    lane: int         # 0-indexed lane number
    layer: int        # 0=floor, 1=stacked, ...


@dataclass
class PackResult:
    """Result of a single packing strategy."""
    strategy: str
    placements: List[Placement]
    unfitted: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def fitted_count(self) -> int:
        return len(self.placements)

    @property
    def unfitted_count(self) -> int:
        return sum(u["quantity"] for u in self.unfitted)

    @property
    def x_used(self) -> float:
        return max((p.x + p.dim_x) for p in self.placements) if self.placements else 0.0

    @property
    def total_weight_lb(self) -> float:
        return sum(p.weight_lb for p in self.placements)

    def metrics(self, truck_spec: Dict[str, Any]) -> Dict[str, Any]:
        used_vol_cuin = sum(p.dim_x * p.dim_y * p.dim_z for p in self.placements)
        truck_vol_cuin = (
            truck_spec["length_in"] * truck_spec["width_in"] * truck_spec["height_in"]
        )
        truck_cargo_cft = truck_spec.get(
            "cargo_volume_cft", truck_vol_cuin / CUIN_PER_CFT
        )
        used_vol_cft = used_vol_cuin / CUIN_PER_CFT

        return {
            "strategy": self.strategy,
            "fitted_count": self.fitted_count,
            "unfitted_count": self.unfitted_count,
            "x_used_in": round(self.x_used, 2),
            "x_used_ft": round(self.x_used / IN_PER_FT, 2),
            "compactness_pct": round(self.x_used / truck_spec["length_in"] * 100, 2),
            "volume_loaded_cft": round(used_vol_cft, 2),
            "volume_util_pct": round(used_vol_cft / truck_cargo_cft * 100, 2),
            "weight_total_lb": round(self.total_weight_lb, 1),
            "weight_util_pct": round(
                self.total_weight_lb / truck_spec["max_payload_lb"] * 100, 2
            ),
            "remaining_length_in": round(truck_spec["length_in"] - self.x_used, 2),
            "remaining_length_ft": round(
                (truck_spec["length_in"] - self.x_used) / IN_PER_FT, 2
            ),
        }


def _pick_best_orientation(
    w: float, d: float, h: float, total_qty: int,
    can_stack: bool, truck_width: float, truck_height_effective: float,
) -> Tuple[float, float, int, int]:
    """
    Choose horizontal orientation (w↔d) and tier count that minimizes total
    length used. Vertical axis (h) is fixed — appliances stay upright.

    Returns (orient_w, orient_d, layers, n_lanes).
    """
    layers_max = max(1, int(truck_height_effective // h)) if can_stack else 1

    candidates = []
    for orient_w, orient_d in ((w, d), (d, w)):
        n_lanes = max(1, int(truck_width // orient_w))
        per_row = n_lanes * layers_max
        rows = -(-total_qty // per_row)  # ceil
        total_length = rows * orient_d
        candidates.append(
            (total_length, -per_row, orient_w, orient_d, layers_max, n_lanes)
        )

    candidates.sort()
    _, _, ow, od, layers, n_lanes = candidates[0]
    return ow, od, layers, n_lanes


def _lane_pack_group(
    group_items: List[Tuple[str, int]],
    group_spec: Dict[str, Any],
    master: Dict[str, Dict[str, Any]],
    x_start: float,
    truck_width: float,
    truck_height_effective: float,
) -> Tuple[List[Placement], float]:
    """
    Pack a group of items sharing the same dimensions.
    Max-fit mode: stacks as many tiers as fit in effective height.
    Only `stackable` flag gates tier stacking. Tries both horizontal
    orientations (w↔d) and picks shortest total length.
    """
    w = group_spec["width_in"]
    d = group_spec["depth_in"]
    h = group_spec["height_in"]
    can_stack = group_spec.get("stackable", False)

    total_qty = sum(qty for _, qty in group_items)
    w, d, layers, n_lanes = _pick_best_orientation(
        w, d, h, total_qty, can_stack, truck_width, truck_height_effective
    )

    queue: List[str] = []
    for mc, qty in group_items:
        queue.extend([mc] * qty)

    placements: List[Placement] = []
    x = x_start
    i_in_row = 0

    while queue:
        if i_in_row >= n_lanes * layers:
            x += d
            i_in_row = 0
        mc = queue.pop(0)
        lane = i_in_row % n_lanes
        layer = i_in_row // n_lanes
        placements.append(Placement(
            seq=0,
            model_code=mc,
            x=x, y=lane * w, z=layer * h,
            dim_x=d, dim_y=w, dim_z=h,
            weight_lb=master[mc]["weight_lb"],
            lane=lane, layer=layer,
        ))
        i_in_row += 1

    next_x = x + d
    return placements, next_x


def _find_dim_groups(
    order_lines: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]
):
    """Group order lines by shared dimensions (e.g. washer + dryer)."""
    groups: Dict[Tuple[float, float, float], Dict[str, Any]] = {}
    for ol in order_lines:
        spec = master[ol["model_code"]]
        key = (spec["width_in"], spec["depth_in"], spec["height_in"])
        if key not in groups:
            groups[key] = {"spec": spec, "items": [], "total_qty": 0, "total_weight": 0.0}
        groups[key]["items"].append((ol["model_code"], ol["quantity"]))
        groups[key]["total_qty"] += ol["quantity"]
        groups[key]["total_weight"] += spec["weight_lb"] * ol["quantity"]
    return list(groups.values())


def pair_pack(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
    sort_strategy: str = "height_desc",
) -> PackResult:
    """
    Pair-packing strategy: group same-dim models, sort, pack with max lanes.

    sort_strategy options:
      - "height_desc": tallest groups first (typical, front-loaded)
      - "weight_desc": heaviest groups first
      - "volume_desc": largest total volume first
    """
    truck_height_eff = truck_spec["height_in"] - DOOR_TRACK_LOSS_IN
    groups = _find_dim_groups(order_lines, master)

    if sort_strategy == "height_desc":
        groups.sort(key=lambda g: -g["spec"]["height_in"])
    elif sort_strategy == "weight_desc":
        groups.sort(key=lambda g: -g["total_weight"])
    elif sort_strategy == "volume_desc":
        groups.sort(key=lambda g: -(
            g["spec"]["width_in"] * g["spec"]["depth_in"]
            * g["spec"]["height_in"] * g["total_qty"]
        ))

    all_placements: List[Placement] = []
    x_cursor: float = 0.0
    unfitted: List[Dict[str, Any]] = []

    for g in groups:
        placements, next_x = _lane_pack_group(
            g["items"], g["spec"], master, x_cursor,
            truck_spec["width_in"], truck_height_eff,
        )
        valid = [p for p in placements if p.x + p.dim_x <= truck_spec["length_in"]]
        if len(valid) < len(placements):
            missing_by_model: Dict[str, int] = {}
            for p in placements[len(valid):]:
                missing_by_model[p.model_code] = missing_by_model.get(p.model_code, 0) + 1
            for mc, q in missing_by_model.items():
                unfitted.append({"model_code": mc, "quantity": q})
        all_placements.extend(valid)
        if valid:
            x_cursor = max(x_cursor, max((p.x + p.dim_x) for p in valid))

    for i, p in enumerate(all_placements, 1):
        p.seq = i

    return PackResult(
        strategy=f"pair_{sort_strategy}",
        placements=all_placements,
        unfitted=unfitted,
    )


def find_best(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> PackResult:
    """Try all strategies, pick best: most fitted then most compact."""
    strategies = ["height_desc", "weight_desc", "volume_desc"]
    results = [pair_pack(order_lines, master, truck_spec, s) for s in strategies]
    results.sort(key=lambda r: (-r.fitted_count, r.x_used))
    return results[0]


def fits_formula(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Closed-form fit predictor — answers "will this load fit?" without running
    the packer simulation. Matches `simulate()` exactly under max-fit mode.

    Formula (per dim-group g sharing same w×d×h):

        eff_H        = H_truck − 10 in                  (door track loss)
        layers_g     = ⌊eff_H / h_g⌋   if stackable_g  else 1
        For each orientation (w*, d*) in {(w,d), (d,w)}:
            lanes    = ⌊W_truck / w*⌋
            per_row  = lanes × layers_g
            rows     = ⌈Q_g / per_row⌉
            length   = rows × d*
        length_g     = min length over both orientations

        FITS  iff  Σ length_g  ≤  L_truck

    All linear units in inches. No load_bear / fragile gating (max-fit mode).
    """
    eff_h = truck_spec["height_in"] - DOOR_TRACK_LOSS_IN
    truck_w = truck_spec["width_in"]
    truck_l = truck_spec["length_in"]

    groups: Dict[Tuple[float, float, float], Dict[str, Any]] = {}
    for ol in order_lines:
        spec = master[ol["model_code"]]
        key = (spec["width_in"], spec["depth_in"], spec["height_in"])
        if key not in groups:
            groups[key] = {"spec": spec, "qty": 0, "models": []}
        groups[key]["qty"] += ol["quantity"]
        groups[key]["models"].append((ol["model_code"], ol["quantity"]))

    total_length: float = 0.0
    breakdown: List[Dict[str, Any]] = []
    for key, g in groups.items():
        w, d, h = key
        stackable = g["spec"].get("stackable", False)
        layers = max(1, int(eff_h // h)) if stackable else 1

        best = None
        for orient_w, orient_d in ((w, d), (d, w)):
            lanes = max(1, int(truck_w // orient_w))
            per_row = lanes * layers
            rows = -(-g["qty"] // per_row)
            length = rows * orient_d
            if best is None or length < best["length"]:
                best = {
                    "orient_w": orient_w, "orient_d": orient_d,
                    "lanes": lanes, "layers": layers,
                    "per_row": per_row, "rows": rows, "length": length,
                }

        total_length += best["length"]
        breakdown.append({
            "models": g["models"],
            "qty": g["qty"],
            "w_in": w, "d_in": d, "h_in": h,
            "stackable": stackable,
            "orient_w_in": best["orient_w"],
            "orient_d_in": best["orient_d"],
            "rotated": (best["orient_w"] != w),
            "lanes": best["lanes"],
            "layers": best["layers"],
            "per_row": best["per_row"],
            "rows": best["rows"],
            "length_in": round(best["length"], 2),
            "length_ft": round(best["length"] / IN_PER_FT, 2),
        })

    return {
        "fits": total_length <= truck_l,
        "predicted_length_in": round(total_length, 2),
        "predicted_length_ft": round(total_length / IN_PER_FT, 2),
        "truck_length_in": truck_l,
        "truck_length_ft": round(truck_l / IN_PER_FT, 2),
        "remaining_in": round(truck_l - total_length, 2),
        "remaining_ft": round((truck_l - total_length) / IN_PER_FT, 2),
        "effective_height_in": eff_h,
        "breakdown": breakdown,
    }


def simulate(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Main entrypoint. Returns full simulation report with metrics + placements.
    All linear units in inches, weight in pounds.

    Usage:
        result = simulate(order_lines, master, truck_spec)
        if result['fits']:
            for p in result['placements']:
                print(p['model_code'], p['x_in'], p['y_in'], p['z_in'])
    """
    best = find_best(order_lines, master, truck_spec)
    m = best.metrics(truck_spec)
    requested_qty = sum(ol["quantity"] for ol in order_lines)

    return {
        "fits": best.unfitted_count == 0,
        "strategy": best.strategy,
        "requested_count": requested_qty,
        "fitted_count": best.fitted_count,
        "unfitted_count": best.unfitted_count,
        "unfitted_detail": best.unfitted,
        "metrics": m,
        "truck": {
            "length_in": truck_spec["length_in"],
            "width_in": truck_spec["width_in"],
            "height_in": truck_spec["height_in"],
            "effective_height_in": truck_spec["height_in"] - DOOR_TRACK_LOSS_IN,
            "max_payload_lb": truck_spec["max_payload_lb"],
            "cargo_volume_cft": truck_spec.get("cargo_volume_cft"),
        },
        "placements": [
            {
                "seq": p.seq,
                "model_code": p.model_code,
                "x_in": round(p.x, 2), "y_in": round(p.y, 2), "z_in": round(p.z, 2),
                "dim_x_in": round(p.dim_x, 2),
                "dim_y_in": round(p.dim_y, 2),
                "dim_z_in": round(p.dim_z, 2),
                "weight_lb": round(p.weight_lb, 1),
                "lane": p.lane, "layer": p.layer,
            }
            for p in best.placements
        ],
    }
