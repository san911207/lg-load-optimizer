"""
=============================================================================
Best-Pack Engine — LG Appliance Truck Loading Optimizer
=============================================================================

Pair-packing algorithm with auto-strategy selection.

Key features:
  - Groups same-dim models together (e.g. washer + dryer paired)
  - Forces max lane utilization (truck_width // box_width)
  - Handles upright-only constraint (this side up)
  - Applies door-track height loss for 26ft roll-up doors
  - Tries multiple sort strategies, picks best (max units fitted, min length)

Author: Sangkyu / LG Electronics US SCM
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass, field


DOOR_TRACK_LOSS_MM = 250  # Roll-up door track at top of rear (5ft × 250mm)


@dataclass
class Placement:
    """Single box placement in the truck."""
    seq: int
    model_code: str
    x: int           # length-wise position (mm from cab)
    y: int           # width-wise position (mm)
    z: int           # height position (mm)
    dim_x: int       # depth (mm, length axis)
    dim_y: int       # width (mm, width axis)
    dim_z: int       # height (mm, vertical axis)
    weight_kg: float
    lane: int        # 0-indexed lane number
    layer: int       # 0=floor, 1=stacked


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
    def x_used(self) -> int:
        return max((p.x + p.dim_x) for p in self.placements) if self.placements else 0

    @property
    def total_weight_kg(self) -> float:
        return sum(p.weight_kg for p in self.placements)

    def metrics(self, truck_spec: Dict[str, Any]) -> Dict[str, Any]:
        used_vol = sum(p.dim_x * p.dim_y * p.dim_z for p in self.placements)
        truck_vol = truck_spec["length_mm"] * truck_spec["width_mm"] * truck_spec["height_mm"]
        return {
            "strategy": self.strategy,
            "fitted_count": self.fitted_count,
            "unfitted_count": self.unfitted_count,
            "x_used_mm": self.x_used,
            "x_used_ft": round(self.x_used / 304.8, 2),
            "compactness_pct": round(self.x_used / truck_spec["length_mm"] * 100, 2),
            "volume_util_pct": round(used_vol / truck_vol * 100, 2),
            "weight_total_kg": round(self.total_weight_kg, 1),
            "weight_total_lb": round(self.total_weight_kg * 2.20462, 1),
            "weight_util_pct": round(self.total_weight_kg / truck_spec["max_payload_kg"] * 100, 2),
            "remaining_length_mm": truck_spec["length_mm"] - self.x_used,
            "remaining_length_ft": round((truck_spec["length_mm"] - self.x_used) / 304.8, 2),
        }


def _pick_best_orientation(
    w: int, d: int, h: int, total_qty: int,
    can_stack: bool, truck_width: int, truck_height_effective: int,
) -> Tuple[int, int, int, int]:
    """
    Choose horizontal orientation (w↔d) and tier count that minimizes total
    length used. Vertical axis (h) is fixed — appliances stay upright.

    Returns (orient_w, orient_d, layers, n_lanes).
    """
    layers_max = max(1, truck_height_effective // h) if can_stack else 1

    candidates = []
    for orient_w, orient_d in ((w, d), (d, w)):
        n_lanes = max(1, truck_width // orient_w)
        per_row = n_lanes * layers_max
        rows = -(-total_qty // per_row)  # ceil
        total_length = rows * orient_d
        candidates.append((total_length, -per_row, orient_w, orient_d, layers_max, n_lanes))

    candidates.sort()
    _, _, ow, od, layers, n_lanes = candidates[0]
    return ow, od, layers, n_lanes


def _lane_pack_group(
    group_items: List[Tuple[str, int]],
    group_spec: Dict[str, Any],
    master: Dict[str, Dict[str, Any]],
    x_start: int,
    truck_width: int,
    truck_height_effective: int,
) -> Tuple[List[Placement], int]:
    """
    Pack a group of items sharing the same dimensions.
    Max-fit mode (CEO 2026-05-15): stacks as many tiers as fit in effective
    height. Only the `stackable` flag gates tier stacking — `load_bear_kg`
    and `fragile` are intentionally ignored. Tries both horizontal orientations
    (w↔d) and picks the layout that minimizes total length used.
    """
    w = group_spec["width_mm"]
    d = group_spec["depth_mm"]
    h = group_spec["height_mm"]
    stackable = group_spec.get("stackable", False)

    # Max-fit mode (CEO 2026-05-15): only the stackable flag gates tier stacking.
    # load_bear and fragile are ignored — TVs / monitors / microwaves stack up to
    # the truck's effective height. Real-world bracing is the planner's call.
    can_stack = stackable

    total_qty = sum(qty for _, qty in group_items)
    w, d, layers, n_lanes = _pick_best_orientation(
        w, d, h, total_qty, can_stack, truck_width, truck_height_effective
    )

    # Expand items to flat queue
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
            seq=0,  # filled later
            model_code=mc,
            x=x, y=lane * w, z=layer * h,
            dim_x=d, dim_y=w, dim_z=h,
            weight_kg=master[mc]["weight_kg"],
            lane=lane, layer=layer,
        ))
        i_in_row += 1

    next_x = x + d
    return placements, next_x


def _find_dim_groups(order_lines: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]):
    """Group order lines by shared dimensions (e.g. washer + dryer)."""
    groups: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    for ol in order_lines:
        spec = master[ol["model_code"]]
        key = (spec["width_mm"], spec["depth_mm"], spec["height_mm"])
        if key not in groups:
            groups[key] = {"spec": spec, "items": [], "total_qty": 0, "total_weight": 0}
        groups[key]["items"].append((ol["model_code"], ol["quantity"]))
        groups[key]["total_qty"] += ol["quantity"]
        groups[key]["total_weight"] += spec["weight_kg"] * ol["quantity"]
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
    truck_height_eff = truck_spec["height_mm"] - DOOR_TRACK_LOSS_MM
    groups = _find_dim_groups(order_lines, master)

    if sort_strategy == "height_desc":
        groups.sort(key=lambda g: -g["spec"]["height_mm"])
    elif sort_strategy == "weight_desc":
        groups.sort(key=lambda g: -g["total_weight"])
    elif sort_strategy == "volume_desc":
        groups.sort(key=lambda g: -(
            g["spec"]["width_mm"] * g["spec"]["depth_mm"]
            * g["spec"]["height_mm"] * g["total_qty"]
        ))

    all_placements: List[Placement] = []
    x_cursor = 0
    unfitted: List[Dict[str, Any]] = []

    for g in groups:
        placements, next_x = _lane_pack_group(
            g["items"], g["spec"], master, x_cursor,
            truck_spec["width_mm"], truck_height_eff,
        )
        valid = [p for p in placements if p.x + p.dim_x <= truck_spec["length_mm"]]
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
    """
    Try all strategies, pick best: first by fitted_count desc, then by x_used asc.
    """
    strategies = ["height_desc", "weight_desc", "volume_desc"]
    results = [pair_pack(order_lines, master, truck_spec, s) for s in strategies]

    # Best = most fitted, then most compact
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

        eff_H        = H_truck − 250 mm                (door track loss)
        layers_g     = ⌊eff_H / h_g⌋   if stackable_g  else 1
        For each orientation (w*, d*) in {(w,d), (d,w)}:
            lanes    = ⌊W_truck / w*⌋
            per_row  = lanes × layers_g
            rows     = ⌈Q_g / per_row⌉
            length   = rows × d*
        length_g     = min length over both orientations

        FITS  iff  Σ length_g  ≤  L_truck

    No load_bear / fragile gating (max-fit mode).
    """
    eff_h = truck_spec["height_mm"] - DOOR_TRACK_LOSS_MM
    truck_w = truck_spec["width_mm"]
    truck_l = truck_spec["length_mm"]

    # Group by dimensions
    groups: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    for ol in order_lines:
        spec = master[ol["model_code"]]
        key = (spec["width_mm"], spec["depth_mm"], spec["height_mm"])
        if key not in groups:
            groups[key] = {
                "spec": spec, "qty": 0, "models": [],
            }
        groups[key]["qty"] += ol["quantity"]
        groups[key]["models"].append((ol["model_code"], ol["quantity"]))

    total_length = 0
    breakdown: List[Dict[str, Any]] = []
    for key, g in groups.items():
        w, d, h = key
        stackable = g["spec"].get("stackable", False)
        layers = max(1, eff_h // h) if stackable else 1

        best = None
        for orient_w, orient_d in ((w, d), (d, w)):
            lanes = max(1, truck_w // orient_w)
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
            "w_mm": w, "d_mm": d, "h_mm": h,
            "stackable": stackable,
            "orient_w_mm": best["orient_w"],
            "orient_d_mm": best["orient_d"],
            "rotated": (best["orient_w"] != w),
            "lanes": best["lanes"],
            "layers": best["layers"],
            "per_row": best["per_row"],
            "rows": best["rows"],
            "length_mm": best["length"],
            "length_ft": round(best["length"] / 304.8, 2),
        })

    return {
        "fits": total_length <= truck_l,
        "predicted_length_mm": int(total_length),
        "predicted_length_ft": round(total_length / 304.8, 2),
        "truck_length_mm": truck_l,
        "truck_length_ft": round(truck_l / 304.8, 2),
        "remaining_mm": int(truck_l - total_length),
        "remaining_ft": round((truck_l - total_length) / 304.8, 2),
        "effective_height_mm": eff_h,
        "breakdown": breakdown,
    }


def simulate(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Main entrypoint. Returns full simulation report with metrics + placements.

    Usage:
        result = simulate(order_lines, master, truck_spec)
        if result['fits']:
            for p in result['placements']:
                print(p['model_code'], p['x'], p['y'], p['z'])
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
            "length_mm": truck_spec["length_mm"],
            "width_mm": truck_spec["width_mm"],
            "height_mm": truck_spec["height_mm"],
            "effective_height_mm": truck_spec["height_mm"] - DOOR_TRACK_LOSS_MM,
            "max_payload_kg": truck_spec["max_payload_kg"],
        },
        "placements": [
            {
                "seq": p.seq,
                "model_code": p.model_code,
                "x_mm": p.x, "y_mm": p.y, "z_mm": p.z,
                "dim_x_mm": p.dim_x, "dim_y_mm": p.dim_y, "dim_z_mm": p.dim_z,
                "weight_kg": p.weight_kg,
                "lane": p.lane, "layer": p.layer,
            }
            for p in best.placements
        ],
    }
