"""
MILP exact solver for 3D truck loading.
=======================================

This is the L1 layer of the v2 engine stack. PuLP + CBC (bundled, free) is
used to formulate the 3D bin-packing problem as a Mixed-Integer Linear
Program and produce a provably optimal arrangement, or near-optimal within
a time budget.

Day 2 additions:
    - Symmetry-breaking for identical SKUs (lexicographic x ordering).
    - Warm-start from a heuristic (CBC accepts MIP-start values).
    - Position-dependent door-track ceiling (rear 5 ft = H - 10 in).
    - Heavy-bottom rule (items >= ``heavy_threshold_lb`` pinned to z=0).

Day 3+ extensions (not yet wired):
    - Pair-packing chain constraint (washer + dryer co-located).
    - LIFO unloading order (per delivery stop).
    - Fragile / this-side-up / category separation.

The API contract mirrors ``engine.best_packer.simulate`` so the auto-router
in v2 can swap implementations transparently.

References:
    Chen-Lee-Shen, 1995. "An analytical model for the container loading
    problem", European Journal of Operational Research.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pulp


IN_PER_FT = 12.0
DOOR_TRACK_LOSS_IN = 10.0
DOOR_TRACK_LEN_IN = 60.0
HEAVY_THRESHOLD_LB = 150.0     # items at or above this weight must rest on z=0

# CBC (open-source) solves 3D bin packing well up to ~15 items. Beyond that
# the branch-and-bound tree explodes and the solver runs out of time before
# even finding a feasible integer solution. The router uses this constant
# to decide when to invoke MILP vs fall back to SA/heuristic.
MILP_MAX_ITEMS = 15


@dataclass
class MilpResult:
    fits: bool
    fitted_count: int
    requested_count: int
    placements: List[Dict[str, Any]]
    unfitted_detail: List[Dict[str, Any]]
    x_used_in: float
    solve_time_s: float
    status: str            # "Optimal", "Time-limit", "Infeasible", "Heuristic-fallback"
    is_provable_optimal: bool
    objective_value: float


def _expand_items(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Flatten {model_code, quantity} into per-unit items with dims."""
    out: List[Dict[str, Any]] = []
    seq = 0
    for ol in order_lines:
        mc = ol["model_code"]
        spec = master[mc]
        for _ in range(int(ol["quantity"])):
            seq += 1
            out.append({
                "seq": seq,
                "model_code": mc,
                "w": float(spec["width_in"]),    # along Y (across truck width)
                "d": float(spec["depth_in"]),    # along X (down truck length)
                "h": float(spec["height_in"]),   # along Z (up)
                "weight_lb": float(spec.get("weight_lb", 0.0)),
                "category": spec.get("category", ""),
                "stackable": bool(spec.get("stackable", False)),
            })
    return out


def _heuristic_warm_start(
    items: List[Dict[str, Any]],
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
) -> Optional[List[Tuple[float, float, float]]]:
    """
    Use the project's existing extreme-point heuristic (``engine.best_packer``)
    as a MIP start seed. ``best_packer`` runs 4 sort strategies and returns
    the best layout, so we get a high-quality feasible point in <1 s and
    CBC's branch-and-bound starts close to the optimum.

    Returns one (x, y, z) tuple per item in the same order as ``items``, or
    ``None`` if the heuristic failed to fit every item (in which case CBC
    runs cold — partial warm-starts confuse the solver more than they help).
    """
    # Local import to avoid a circular dep at module load time.
    from engine.best_packer import find_best  # noqa: E402

    result = find_best(order_lines, master, truck_spec)
    if result.unfitted_count > 0 or not result.placements:
        return None

    # Build a lookup from item seq -> (x, y, z). best_packer.placements stores
    # seq numbers that match _expand_items() order (both walk order_lines in
    # the same order and increment seq).
    seq_to_pos: Dict[int, Tuple[float, float, float]] = {}
    for p in result.placements:
        seq_to_pos[p.seq] = (p.x, p.y, p.z)

    warm: List[Tuple[float, float, float]] = []
    for it in items:
        pos = seq_to_pos.get(it["seq"])
        if pos is None:
            return None  # mismatched — abort warm-start
        warm.append(pos)
    return warm


def _set_warm_start(
    x_vars: List[pulp.LpVariable],
    y_vars: List[pulp.LpVariable],
    z_vars: List[pulp.LpVariable],
    sep_vars: Dict[Tuple[int, int, str], pulp.LpVariable],
    heavy_at_floor: Dict[int, pulp.LpVariable],
    items: List[Dict[str, Any]],
    warm: List[Tuple[float, float, float]],
):
    """
    Push the warm-start placements into the PuLP variable .varValue field so
    CBC picks them up when ``warmStart=True`` is passed to PULP_CBC_CMD.

    Only fully-resolved placements (no None) are set; CBC will branch on the
    rest. We also infer the separation binaries from the geometry so CBC
    starts from a fully-feasible integer solution.
    """
    if any(p is None for p in warm):
        # Partial warm-start is unreliable for CBC; skip it.
        return False

    n = len(items)
    # Clamp warm-start values to each var's [lowBound, upBound] — the heuristic
    # uses EPS tolerance when placing, so it can return positions that float
    # ~1e-3 in past the strict MILP upper bound.
    def _clip(val: float, var: pulp.LpVariable) -> float:
        if var.upBound is not None and val > var.upBound:
            return var.upBound
        if var.lowBound is not None and val < var.lowBound:
            return var.lowBound
        return val

    for i in range(n):
        wx, wy, wz = warm[i]
        x_vars[i].setInitialValue(_clip(wx, x_vars[i]))
        y_vars[i].setInitialValue(_clip(wy, y_vars[i]))
        z_vars[i].setInitialValue(_clip(wz, z_vars[i]))
        if i in heavy_at_floor:
            heavy_at_floor[i].setInitialValue(1)

    eps = 0.01
    for i in range(n):
        for j in range(i + 1, n):
            d_i, w_i, h_i = items[i]["d"], items[i]["w"], items[i]["h"]
            d_j, w_j, h_j = items[j]["d"], items[j]["w"], items[j]["h"]
            xi, yi, zi = warm[i]
            xj, yj, zj = warm[j]
            # determine which separation holds (one or more may hold; we set 1 to first true)
            sets = {"a": 0, "b": 0, "c": 0, "d": 0, "e": 0, "g": 0}
            if xi + d_i <= xj + eps: sets["a"] = 1
            elif xj + d_j <= xi + eps: sets["b"] = 1
            elif yi + w_i <= yj + eps: sets["c"] = 1
            elif yj + w_j <= yi + eps: sets["d"] = 1
            elif zi + h_i <= zj + eps: sets["e"] = 1
            elif zj + h_j <= zi + eps: sets["g"] = 1
            else:
                # Should not happen — warm-start was supposed to be feasible.
                return False
            for k, v in sets.items():
                sep_vars[(i, j, k)].setInitialValue(v)
    return True


def milp_solve(
    order_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    truck_spec: Dict[str, Any],
    time_limit_s: float = 60.0,
    msg: bool = False,
    door_track: bool = True,
    heavy_bottom: bool = True,
    warm_start: bool = True,
) -> MilpResult:
    """
    Formulate and solve the 3D bin-packing problem as a MILP.

    Parameters
    ----------
    order_lines :
        ``[{"model_code": str, "quantity": int}, ...]``
    master :
        SKU dim/weight lookup keyed by model_code.
    truck_spec :
        ``{"length_in", "width_in", "height_in", ...}``
    time_limit_s :
        Hard wallclock budget for the solver. CBC stops at this point and
        returns the best integer-feasible solution found.
    msg :
        Print solver progress to stdout (debug only).
    door_track :
        Enforce the rear 5 ft door-track ceiling (87 in for a 26 ft truck).
    heavy_bottom :
        Pin items with ``weight_lb >= HEAVY_THRESHOLD_LB`` to z=0.
    warm_start :
        Seed CBC with a heuristic placement. Cuts time-to-first-feasible
        from several seconds to ~0.
    """
    start = time.monotonic()

    items = _expand_items(order_lines, master)
    n = len(items)
    L = float(truck_spec["length_in"])
    W = float(truck_spec["width_in"])
    H = float(truck_spec["height_in"])

    if n == 0:
        return MilpResult(
            fits=True, fitted_count=0, requested_count=0,
            placements=[], unfitted_detail=[],
            x_used_in=0.0, solve_time_s=0.0,
            status="Trivial", is_provable_optimal=True, objective_value=0.0,
        )

    # Big-M values per axis — tight bounds help CBC converge faster.
    M_x = L + max(it["d"] for it in items)
    M_y = W + max(it["w"] for it in items)
    M_z = H + max(it["h"] for it in items)

    prob = pulp.LpProblem("truck_loading_3d", pulp.LpMinimize)

    # Position variables (front-left-bottom corner of each item).
    x_vars = [
        pulp.LpVariable(f"x_{i}", lowBound=0, upBound=L - items[i]["d"])
        for i in range(n)
    ]
    y_vars = [
        pulp.LpVariable(f"y_{i}", lowBound=0, upBound=W - items[i]["w"])
        for i in range(n)
    ]
    z_vars = [
        pulp.LpVariable(f"z_{i}", lowBound=0, upBound=H - items[i]["h"])
        for i in range(n)
    ]

    L_used = pulp.LpVariable("L_used", lowBound=0, upBound=L)
    for i in range(n):
        prob += L_used >= x_vars[i] + items[i]["d"], f"len_envelope_{i}"

    # ── Disjunctive non-overlap ─────────────────────────────────────────
    # For each unordered pair {i, j} (i < j), at least one of 6 spatial
    # separation conditions must hold.
    sep_vars: Dict[Tuple[int, int, str], pulp.LpVariable] = {}
    for i in range(n):
        for j in range(i + 1, n):
            for k in ("a", "b", "c", "d", "e", "g"):
                sep_vars[(i, j, k)] = pulp.LpVariable(f"{k}_{i}_{j}", cat="Binary")

            a = sep_vars[(i, j, "a")]
            b = sep_vars[(i, j, "b")]
            c = sep_vars[(i, j, "c")]
            d_v = sep_vars[(i, j, "d")]
            e = sep_vars[(i, j, "e")]
            g = sep_vars[(i, j, "g")]

            prob += a + b + c + d_v + e + g >= 1, f"sep_{i}_{j}"

            prob += x_vars[i] + items[i]["d"] <= x_vars[j] + M_x * (1 - a), f"sepx_lt_{i}_{j}"
            prob += x_vars[j] + items[j]["d"] <= x_vars[i] + M_x * (1 - b), f"sepx_gt_{i}_{j}"
            prob += y_vars[i] + items[i]["w"] <= y_vars[j] + M_y * (1 - c), f"sepy_lt_{i}_{j}"
            prob += y_vars[j] + items[j]["w"] <= y_vars[i] + M_y * (1 - d_v), f"sepy_gt_{i}_{j}"
            prob += z_vars[i] + items[i]["h"] <= z_vars[j] + M_z * (1 - e), f"sepz_lt_{i}_{j}"
            prob += z_vars[j] + items[j]["h"] <= z_vars[i] + M_z * (1 - g), f"sepz_gt_{i}_{j}"

    # ── Symmetry-breaking for identical SKUs ────────────────────────────
    # Group items by (model_code) — identical SKUs are interchangeable; without
    # ordering CBC explores n_g! redundant arrangements. Force lex-increasing
    # x positions inside each group: x_{group[0]} <= x_{group[1]} <= ...
    # This collapses the symmetric search space by a factor of group-size!.
    groups: Dict[str, List[int]] = {}
    for i, it in enumerate(items):
        groups.setdefault(it["model_code"], []).append(i)
    for mc, idxs in groups.items():
        if len(idxs) < 2:
            continue
        for a, b in zip(idxs, idxs[1:]):
            prob += x_vars[a] <= x_vars[b], f"sym_{mc}_{a}_{b}"

    # ── Door-track region (position-dependent ceiling) ──────────────────
    # Items reaching past x = L - DOOR_TRACK_LEN_IN must respect the rear
    # ceiling H - DOOR_TRACK_LOSS_IN.  delta_i = 1 if item i's rear face
    # extends into the rear zone.
    delta_vars: Dict[int, pulp.LpVariable] = {}
    if door_track:
        rear_threshold = L - DOOR_TRACK_LEN_IN
        for i in range(n):
            delta = pulp.LpVariable(f"delta_{i}", cat="Binary")
            delta_vars[i] = delta
            # delta_i = 1  iff  x_i + d_i > rear_threshold
            #   if delta = 0:  x_i + d_i <= rear_threshold
            #   if delta = 1:  x_i + d_i >= rear_threshold + eps  (eps = 0.01)
            prob += (
                x_vars[i] + items[i]["d"] <= rear_threshold + M_x * delta,
                f"dt_front_{i}",
            )
            prob += (
                x_vars[i] + items[i]["d"] >= rear_threshold + 0.01 - M_x * (1 - delta),
                f"dt_rear_{i}",
            )
            # ceiling: z_i + h_i <= H - delta_i * DOOR_TRACK_LOSS_IN
            prob += (
                z_vars[i] + items[i]["h"] <= H - DOOR_TRACK_LOSS_IN * delta,
                f"dt_ceil_{i}",
            )

    # ── Heavy-bottom rule ───────────────────────────────────────────────
    # Items >= HEAVY_THRESHOLD_LB rest on the floor (z = 0). The optimizer
    # then arranges them in x/y to fit, but they cannot be stacked on
    # anything. This matches the user's "무거운 거 1단" requirement and
    # eliminates a large chunk of search space (z is fixed for heavy items).
    heavy_at_floor: Dict[int, pulp.LpVariable] = {}
    if heavy_bottom:
        for i, it in enumerate(items):
            if it["weight_lb"] >= HEAVY_THRESHOLD_LB:
                prob += z_vars[i] == 0, f"heavy_floor_{i}"
                # Track the (trivially=1) heavy flag for warm-start setup.
                heavy_at_floor[i] = pulp.LpVariable(f"heavy_{i}", cat="Binary", lowBound=1, upBound=1)

    # ── Objective: minimize trailer length used ─────────────────────────
    prob += L_used

    # ── Warm start from heuristic (optional) ────────────────────────────
    used_warm_start = False
    if warm_start:
        warm = _heuristic_warm_start(items, order_lines, master, truck_spec)
        if warm is not None:
            used_warm_start = _set_warm_start(
                x_vars, y_vars, z_vars, sep_vars, heavy_at_floor, items, warm
            )

    # ── Solve with CBC ──────────────────────────────────────────────────
    solver = pulp.PULP_CBC_CMD(
        msg=int(msg),
        timeLimit=time_limit_s,
        warmStart=used_warm_start,
    )
    status_code = prob.solve(solver)
    elapsed = time.monotonic() - start

    pulp_status = pulp.LpStatus[status_code]

    if pulp_status == "Optimal":
        provable = elapsed < time_limit_s * 0.95
        status_label = "Optimal" if provable else "Time-limit"
    elif pulp_status == "Infeasible":
        return MilpResult(
            fits=False, fitted_count=0, requested_count=n,
            placements=[],
            unfitted_detail=[{"model_code": it["model_code"], "quantity": 1} for it in items],
            x_used_in=L, solve_time_s=elapsed,
            status="Infeasible", is_provable_optimal=True, objective_value=L,
        )
    else:
        return MilpResult(
            fits=False, fitted_count=0, requested_count=n,
            placements=[],
            unfitted_detail=[{"model_code": it["model_code"], "quantity": 1} for it in items],
            x_used_in=L, solve_time_s=elapsed,
            status=pulp_status, is_provable_optimal=False, objective_value=L,
        )

    placements: List[Dict[str, Any]] = []
    for i, it in enumerate(items):
        y_val = pulp.value(y_vars[i]) or 0.0
        z_val = pulp.value(z_vars[i]) or 0.0
        placements.append({
            "seq": it["seq"],
            "model_code": it["model_code"],
            "category": it["category"],
            "x_in": round(pulp.value(x_vars[i]) or 0.0, 3),
            "y_in": round(y_val, 3),
            "z_in": round(z_val, 3),
            "dim_x_in": it["d"],
            "dim_y_in": it["w"],
            "dim_z_in": it["h"],
            "weight_lb": it["weight_lb"],
            "lane": int(round(y_val / it["w"])) if it["w"] > 0 else 0,
            "layer": int(round(z_val / it["h"])) if it["h"] > 0 else 0,
        })

    obj_val = float(pulp.value(L_used) or 0.0)
    return MilpResult(
        fits=True, fitted_count=n, requested_count=n,
        placements=placements, unfitted_detail=[],
        x_used_in=round(obj_val, 3), solve_time_s=elapsed,
        status=status_label,
        is_provable_optimal=(status_label == "Optimal"),
        objective_value=obj_val,
    )
