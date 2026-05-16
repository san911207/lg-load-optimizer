"""Tests for the v2 1-page PDF work order generator."""
from __future__ import annotations

import pandas as pd
import pytest

from engine.pdf_gen_v2 import generate_work_order_v2
from engine.router import solve


@pytest.fixture(scope="module")
def sample():
    base = "/Users/sangkyu/projects/load_optimizer/data/sample_input.xlsx"
    master_df = pd.read_excel(base, "Model_Master")
    truck_df = pd.read_excel(base, "Truck_Master")
    loads = pd.read_excel(base, "Loads")
    master = master_df.set_index("model_code").to_dict("index")
    master["LDFN4542S"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    master["LWS3063ST"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    trucks = truck_df.set_index("truck_type").to_dict("index")
    return master, trucks, loads


def test_pdf_v2_renders_for_real_load(sample):
    master, trucks, loads = sample
    order = loads[loads["load_id"] == "L001"][["model_code", "quantity"]].to_dict("records")
    r = solve(order, master, trucks["26ft"], time_budget_s=4.0)
    pdf = generate_work_order_v2(
        r, load_id="L001", truck_label="26 ft Penske",
        truck_spec=trucks["26ft"], master=master, driver="J. Martinez",
    )
    assert pdf.startswith(b"%PDF-1."), "Output is not a PDF file"
    assert len(pdf) > 4000, f"PDF suspiciously small ({len(pdf)} bytes)"
    # one page only — count "/Type /Page" entries (not Pages)
    page_count = pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ")
    assert page_count == 1, f"PDF should be exactly 1 page, found {page_count}"


def test_pdf_v2_handles_small_load(sample):
    """Tiny 3-item load — PDF must still render cleanly."""
    master, trucks, _ = sample
    order = [{"model_code": "LDFN4542S", "quantity": 3}]
    r = solve(order, master, trucks["26ft"], time_budget_s=5.0)
    pdf = generate_work_order_v2(r, "TEST", "26 ft", trucks["26ft"], master)
    assert pdf.startswith(b"%PDF-1.")


def test_pdf_v2_handles_empty_master(sample):
    """master=None must not crash — boxes fall back to neutral grey."""
    _, trucks, _ = sample
    minimal_master = {"X": {"category": "Unknown", "width_in": 10, "depth_in": 10,
                            "height_in": 10, "weight_lb": 5, "stackable": True, "fragile": False}}
    order = [{"model_code": "X", "quantity": 2}]
    r = solve(order, minimal_master, trucks["26ft"], time_budget_s=2.0)
    pdf = generate_work_order_v2(r, "EMPTY", "26 ft", trucks["26ft"], master=None)
    assert pdf.startswith(b"%PDF-1.")
