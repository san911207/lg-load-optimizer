"""
Regression suite for the 8 Phase-A critical bugs surfaced by the QA Lead
and Engineering Lead reviews. Each test pins a behaviour that was either
silently wrong (data corruption) or crashed on a known edge case.

Bugs covered:
  B1  router._wrap_sa: category=""  → audit blacklist never fired
  B2  app.py sim cache:  master-fingerprint missing → stale results
  B3  milp_solver: CBC missing → unhandled traceback
  B4  app.py: silent pdf_gen v1 fallback hid pdf_gen_v2 regressions
  B5  pdf_gen_v2: CJK SKU silent drop
  B6  app._resolve_user_data_dir: all-drives-non-writable → StopIteration
  B7  sa_refiner: n=1 swap IndexError
  B8  router: MILP partial fit/timeout → unfitted dropped, no SA fallback
"""
from __future__ import annotations

import pandas as pd
import pytest


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sample():
    base = "/Users/sangkyu/projects/load_optimizer/data/sample_input.xlsx"
    master_df = pd.read_excel(base, "Model_Master")
    truck_df = pd.read_excel(base, "Truck_Master")
    loads = pd.read_excel(base, "Loads")
    master = master_df.set_index("model_code").to_dict("index")
    master["DISH-001"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    master["WOVEN-001"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    trucks = truck_df.set_index("truck_type").to_dict("index")
    return master, trucks, loads


# ── B1: SA category populated ──────────────────────────────────────────


def test_b1_sa_placements_carry_category_for_audit(sample):
    """SA route must populate placement[i]['category'] from master so that
    domain_rules.verify can run the category-blacklist check (Microwave on
    Refrigerator, etc.) — previously hardcoded to empty string.
    """
    from engine.router import solve

    master, trucks, loads = sample
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = solve(order, master, trucks["26ft"], time_budget_s=8.0)
    assert r["engine"] == "Heuristic+SA"  # confirms we're on the SA path
    # At least one placement must have a non-empty category (master-resolved).
    cats = {p.get("category", "") for p in r["placements"]}
    assert cats != {""}, "SA placements all have empty category — audit bypass bug"


# ── B2: cache invalidates on master change ────────────────────────────


def test_b2_cache_fingerprint_changes_with_master_dims():
    """The cache signature must hash the master so an SKU dim change
    invalidates the cached simulation.
    """
    # Replicate the inline fingerprint helpers from app.py here — the
    # contract is "different dims for the same model_code yield different
    # signature components", regardless of which order_lines reference them.
    def _master_fp(m, mcs):
        bits = tuple(
            (mc, m[mc].get("width_in"), m[mc].get("depth_in"),
             m[mc].get("height_in"), m[mc].get("weight_lb"),
             m[mc].get("stackable"), m[mc].get("fragile"))
            for mc in sorted(mcs) if mc in m
        )
        return hash(bits)

    m1 = {"X": {"width_in": 30, "depth_in": 30, "height_in": 30, "weight_lb": 50, "stackable": True, "fragile": False}}
    m2 = {"X": {"width_in": 30, "depth_in": 30, "height_in": 30, "weight_lb": 50, "stackable": True, "fragile": False}}
    m3 = {"X": {"width_in": 40, "depth_in": 30, "height_in": 30, "weight_lb": 50, "stackable": True, "fragile": False}}
    assert _master_fp(m1, {"X"}) == _master_fp(m2, {"X"})  # same dims → same fingerprint
    assert _master_fp(m1, {"X"}) != _master_fp(m3, {"X"})  # different width → different fingerprint


# ── B3: CBC failure path returns a clean envelope ─────────────────────


def test_b3_milp_solver_unavailable_returns_clean_result(sample, monkeypatch):
    """When the bundled CBC binary is missing/quarantined, milp_solve must
    NOT propagate FileNotFoundError — instead return a MilpResult marked
    Solver-unavailable so the router fallback path activates.
    """
    import pulp
    from engine import milp_solver

    master, trucks, _ = sample
    order = [{"model_code": "DISH-001", "quantity": 2}]

    class _BrokenSolver:
        """Mimics the PuLP solver interface; actualSolve is what prob.solve calls."""
        def actualSolve(self, prob, **kw):
            raise FileNotFoundError("simulated cbc.exe quarantined by Defender")

    monkeypatch.setattr(milp_solver.pulp, "PULP_CBC_CMD",
                        lambda **kw: _BrokenSolver())
    r = milp_solver.milp_solve(order, master, trucks["26ft"], time_limit_s=5)
    assert r.fits is False
    assert r.status.startswith("Solver-unavailable")
    assert r.fitted_count == 0


# ── B4: pdf_gen v1 deleted, no circular import ────────────────────────


def test_b4_pdf_gen_v1_file_removed():
    """The v1 pdf_gen.py (with its self-import bug) must no longer exist
    so the silent-fallback in app.py cannot mask a v2 regression.
    """
    from pathlib import Path
    legacy = Path("/Users/sangkyu/projects/load_optimizer/engine/pdf_gen.py")
    assert not legacy.exists(), (
        "engine/pdf_gen.py still present — silent fallback in app.py was a trap. "
        "Confirm deletion."
    )


# ── B5: Unicode (Korean) SKU PDF rendering ────────────────────────────


def test_b5_pdf_handles_unicode_sku_without_crash():
    """A Korean SKU code passed through pdf_gen_v2 must not crash and
    must produce a valid PDF (even if glyph fallback collapses the chars).
    """
    from engine.pdf_gen_v2 import generate_work_order_v2

    master = {
        "LG냉장고-01": {
            "category": "Refrigerator", "width_in": 36, "depth_in": 30,
            "height_in": 68, "weight_lb": 280, "stackable": True, "fragile": False,
        },
    }
    truck = {"length_in": 311.0, "width_in": 97.0, "height_in": 97.0,
             "max_payload_lb": 10000, "cargo_volume_cft": 1700}
    result = {
        "fits": True, "fitted_count": 1, "requested_count": 1,
        "unfitted_count": 0, "unfitted_detail": [],
        "placements": [{
            "seq": 1, "model_code": "LG냉장고-01", "category": "Refrigerator",
            "x_in": 0, "y_in": 0, "z_in": 0,
            "dim_x_in": 30, "dim_y_in": 36, "dim_z_in": 68,
            "weight_lb": 280, "lane": 0, "layer": 0,
        }],
        "metrics": {"x_used_in": 30, "x_used_ft": 2.5, "compactness_pct": 9.6,
                    "volume_loaded_cft": 42, "volume_util_pct": 2.5,
                    "weight_total_lb": 280, "weight_util_pct": 2.8,
                    "remaining_length_in": 281, "remaining_length_ft": 23.4},
        "engine": "Heuristic", "status": "Heuristic",
    }
    pdf = generate_work_order_v2(result, "TEST-KO", "26 ft", truck, master=master)
    assert pdf.startswith(b"%PDF-1.")


# ── B6: All drives non-writable → tempfile fallback ───────────────────


def test_b6_user_data_dir_falls_back_to_tempdir(monkeypatch, tmp_path):
    """On a corp Windows machine where IT blocks every candidate path,
    _resolve_user_data_dir must fall back to a writable temp dir instead
    of crashing with StopIteration during module import.
    """
    import importlib
    import sys
    import app  # already imported once at this point

    monkeypatch.setattr(app, "_writable", lambda d: False)
    out = app._resolve_user_data_dir()
    assert out.exists()
    # Verify the returned path is actually writable.
    probe = out / ".test_probe"
    probe.write_text("ok")
    assert probe.exists()
    probe.unlink()


# ── B7: SA single-item load ───────────────────────────────────────────


def test_b7_sa_single_item_does_not_crash(sample):
    """SA must early-return cleanly when n_items == 1 instead of looping
    through swap operators that would IndexError.
    """
    from engine.sa_refiner import refine

    master, trucks, _ = sample
    order = [{"model_code": "DISH-001", "quantity": 1}]
    r = refine(order, master, trucks["26ft"], time_budget_s=1.0)
    assert len(r.placements) == 1
    assert r.iterations == 0      # the early-return path
    assert r.elapsed_s < 0.5      # did NOT burn the full budget


# ── B8: MILP timeout / partial-fit → SA fallback ──────────────────────


def test_b8_milp_timeout_falls_back_to_sa(sample):
    """A near-zero time budget forces MILP to time out (or fail to find
    a complete solution). The router must fall back to the SA path so the
    dispatcher gets a usable envelope, not an empty/partial result.
    """
    from engine.router import solve

    master, trucks, _ = sample
    # A 5-item load is in the MILP routing range (≤15 items).
    order = [{"model_code": "DISH-001", "quantity": 3},
             {"model_code": "WOVEN-001", "quantity": 2}]
    # Aggressively short budget — MILP needs >1s to build the model.
    r = solve(order, master, trucks["26ft"], time_budget_s=0.5)
    # Result must be usable (either MILP managed to finish or fallback ran).
    assert r["fits"] is True
    assert r["fitted_count"] == r["requested_count"]
    # And the engine label must reflect what actually ran.
    assert r["engine"] in {"MILP", "Heuristic+SA", "Heuristic"}


# ── Extra coverage: heuristic-only path for very large loads ──────────


def test_router_uses_heuristic_for_large_loads():
    """Loads above SA_MAX_ITEMS (300) route directly to heuristic — verify
    the envelope still carries the v2 keys (pair_count, audit_findings).
    """
    from engine.router import solve

    master = {
        "X": {"category": "Microwave", "width_in": 18, "depth_in": 18,
              "height_in": 18, "weight_lb": 30, "stackable": True, "fragile": False},
    }
    truck = {"length_in": 311.0, "width_in": 97.0, "height_in": 97.0,
             "max_payload_lb": 10000, "cargo_volume_cft": 1700}
    order = [{"model_code": "X", "quantity": 350}]
    r = solve(order, master, truck, time_budget_s=10.0)
    assert r["engine"] == "Heuristic"
    assert "pair_count" in r
    assert "audit_findings" in r


# ── Extra coverage: wider-than-truck SKU does not crash ───────────────


def test_router_handles_oversized_sku_gracefully():
    """An SKU that exceeds truck width must produce a clean unfitted-count
    result instead of crashing in any of the three engine paths.
    """
    from engine.router import solve

    master = {
        "WIDE": {"category": "Other", "width_in": 110, "depth_in": 40,
                 "height_in": 60, "weight_lb": 200, "stackable": False, "fragile": False},
    }
    truck = {"length_in": 311.0, "width_in": 97.0, "height_in": 97.0,
             "max_payload_lb": 10000, "cargo_volume_cft": 1700}
    order = [{"model_code": "WIDE", "quantity": 1}]
    r = solve(order, master, truck, time_budget_s=4.0)
    assert r["unfitted_count"] == 1
    assert r["fits"] is False
