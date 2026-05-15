"""
Quick Fit Calculator — standalone Excel workbook generator.

Same formula as engine.best_packer.fits_formula() but exposed as Excel cells
so a planner can drop in SKUs and instantly see whether the load fits — no
Streamlit, no Python.

Layout:
  Sheet "Quick Fit Check"  — user inputs + auto-calc + verdict
  Sheet "Model_Master"     — reference SKU specs (with calibrations applied)
  Sheet "Truck_Master"     — reference truck specs
"""
from pathlib import Path
from typing import Any, Dict

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule

DOOR_TRACK_LOSS_MM = 250
N_INPUT_ROWS = 30

# Headers for the input table (US units throughout)
HEADERS = [
    "#", "Model Code", "Qty",
    "w (in)", "d (in)", "h (in)", "Stackable",
    "Layers", "Length A (ft)", "Length B (ft)",
    "Min Length (ft)", "Volume (ft³)", "Weight (lb)",
]

# CLAUDE.md calibrations (same as app.py apply_calibrations)
CALIBRATIONS = {
    "LDFN4542S": {"stackable": True, "load_bear_kg": 60, "fragile": False},
    "LWS3063ST": {"stackable": True, "load_bear_kg": 90, "fragile": False},
}

# Sample pre-fill (L001) so user opens to a working example
SAMPLE_L001 = [
    ("LF29H8330S", 6),
    ("WM4000HWA", 8),
    ("DLEX4000W", 8),
    ("LDFN4542S", 10),
    ("LMV1764ST", 12),
]


def build_fit_calculator(master_xlsx_path: Path, out_path: Path) -> Path:
    master_df = pd.read_excel(master_xlsx_path, sheet_name="Model_Master")
    truck_df = pd.read_excel(master_xlsx_path, sheet_name="Truck_Master")

    # Apply calibrations so Excel matches the simulator behavior
    for mc, vals in CALIBRATIONS.items():
        mask = master_df["model_code"] == mc
        for col, v in vals.items():
            master_df.loc[mask, col] = v

    wb = Workbook()
    ws_main = wb.active
    ws_main.title = "Quick Fit Check"
    _build_main_sheet(ws_main, master_df)

    ws_master = wb.create_sheet("Model_Master")
    _write_df_sheet(ws_master, master_df)

    ws_trucks = wb.create_sheet("Truck_Master")
    _write_df_sheet(ws_trucks, truck_df)

    wb.save(out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────────────
# Internal: layout the main sheet
# ─────────────────────────────────────────────────────────────────────────
def _build_main_sheet(ws, master_df: pd.DataFrame) -> None:
    # Title
    ws["A1"] = "LG Quick Fit Calculator"
    ws["A1"].font = Font(size=18, bold=True, color="0F6E56")
    ws.merge_cells("A1:M1")
    ws["A2"] = (
        "Closed-form fit predictor (max-fit mode — load_bear / fragile ignored, "
        "door track auto-applied)"
    )
    ws["A2"].font = Font(italic=True, color="6B7280")
    ws.merge_cells("A2:M2")

    # Instructions
    ws["A4"] = "How to use"
    ws["A4"].font = Font(bold=True, size=12)
    ws["A5"] = "1. Pick truck (26ft or 53ft) in cell C9 (dropdown)."
    ws["A6"] = "2. For each item: enter SKU code in column B (dropdown) and quantity in column C."
    ws["A7"] = (
        "3. Same-dim SKUs (e.g. washer + dryer): combine into ONE row "
        "to get the simulator-exact answer."
    )

    # Truck selector + specs (all US units displayed)
    ws["B9"] = "Truck:"
    ws["B9"].font = Font(bold=True)
    ws["C9"] = "26ft"
    ws["C9"].font = Font(bold=True, size=13, color="0F6E56")
    ws["C9"].fill = PatternFill("solid", fgColor="F0FDF4")
    truck_dv = DataValidation(type="list", formula1='"26ft,53ft"', allow_blank=False)
    truck_dv.add("C9")
    ws.add_data_validation(truck_dv)

    # Truck dims in US units (internal math uses mm via fresh VLOOKUPs below)
    ws["B10"] = "Truck length (ft):"
    ws["C10"] = "=VLOOKUP(C9,Truck_Master!A:G,3,FALSE)/304.8"
    ws["C10"].number_format = "0.00"

    ws["B11"] = "Truck width (ft):"
    ws["C11"] = "=VLOOKUP(C9,Truck_Master!A:G,4,FALSE)/304.8"
    ws["C11"].number_format = "0.00"

    ws["B12"] = "Truck height (ft):"
    ws["C12"] = "=VLOOKUP(C9,Truck_Master!A:G,5,FALSE)/304.8"
    ws["C12"].number_format = "0.00"

    ws["B13"] = "Effective height (ft):"
    ws["C13"] = "=(VLOOKUP(C9,Truck_Master!A:G,5,FALSE)-250)/304.8"
    ws["C13"].number_format = "0.00"
    ws["D13"] = "(after 250 mm door track loss ≈ 10 in)"
    ws["D13"].font = Font(italic=True, color="6B7280")

    ws["B14"] = "Max payload (lb):"
    ws["C14"] = "=VLOOKUP(C9,Truck_Master!A:G,6,FALSE)*2.20462"
    ws["C14"].number_format = "#,##0"

    # Input table header
    header_row = 16
    for i, h in enumerate(HEADERS, start=1):
        c = ws.cell(row=header_row, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0F6E56")
        c.alignment = Alignment(horizontal="center")

    first_input = header_row + 1
    last_input = first_input + N_INPUT_ROWS - 1

    model_codes = master_df["model_code"].tolist()
    model_dv = DataValidation(
        type="list",
        formula1=f"=Model_Master!$A$2:$A${len(model_codes) + 1}",
        allow_blank=True,
    )

    # Shorthand for VLOOKUPs (B{r} = model_code cell)
    # Master columns: 1=code, 3=width_mm, 4=depth_mm, 5=height_mm, 6=weight_kg, 8=stackable
    # Truck columns: 3=length_mm, 4=width_mm, 5=height_mm, 6=max_payload_kg
    for r in range(first_input, last_input + 1):
        ws.cell(row=r, column=1, value=r - header_row)
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=r, column=1).font = Font(color="9CA3AF")
        for col in (2, 3):
            ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor="EFF6FF")
        model_dv.add(f"B{r}")

        # Master VLOOKUPs (returning raw mm/kg)
        w_mm = f'VLOOKUP(B{r},Model_Master!A:L,3,FALSE)'
        d_mm = f'VLOOKUP(B{r},Model_Master!A:L,4,FALSE)'
        h_mm = f'VLOOKUP(B{r},Model_Master!A:L,5,FALSE)'
        kg = f'VLOOKUP(B{r},Model_Master!A:L,6,FALSE)'
        stack = f'VLOOKUP(B{r},Model_Master!A:L,8,FALSE)'
        truck_W_mm = 'VLOOKUP($C$9,Truck_Master!A:G,4,FALSE)'
        eff_H_mm = '(VLOOKUP($C$9,Truck_Master!A:G,5,FALSE)-250)'

        # D: w (in), E: d (in), F: h (in)
        ws.cell(row=r, column=4, value=f'=IF(B{r}="","",{w_mm}/25.4)')
        ws.cell(row=r, column=4).number_format = "0.0"
        ws.cell(row=r, column=5, value=f'=IF(B{r}="","",{d_mm}/25.4)')
        ws.cell(row=r, column=5).number_format = "0.0"
        ws.cell(row=r, column=6, value=f'=IF(B{r}="","",{h_mm}/25.4)')
        ws.cell(row=r, column=6).number_format = "0.0"

        # G: stackable
        ws.cell(row=r, column=7, value=f'=IF(B{r}="","",{stack})')

        # H: layers — floor(eff_H_mm / h_mm) if stackable else 1
        ws.cell(row=r, column=8, value=(
            f'=IF(B{r}="","",IF({stack}=TRUE,FLOOR({eff_H_mm}/{h_mm},1),1))'
        ))

        # I: Length A (ft) — orient A uses w_mm along width, d_mm along length
        ws.cell(row=r, column=9, value=(
            f'=IF(B{r}="","",CEILING(C{r}/(FLOOR({truck_W_mm}/{w_mm},1)*H{r}),1)*{d_mm}/304.8)'
        ))
        ws.cell(row=r, column=9).number_format = "0.00"

        # J: Length B (ft) — orient B rotates 90° horizontally
        ws.cell(row=r, column=10, value=(
            f'=IF(B{r}="","",CEILING(C{r}/(FLOOR({truck_W_mm}/{d_mm},1)*H{r}),1)*{w_mm}/304.8)'
        ))
        ws.cell(row=r, column=10).number_format = "0.00"

        # K: Min Length (ft)
        ws.cell(row=r, column=11, value=f'=IF(B{r}="","",MIN(I{r},J{r}))')
        ws.cell(row=r, column=11).number_format = "0.00"

        # L: Volume per row (ft³) = qty × w × d × h / 28,316,846.6
        ws.cell(row=r, column=12, value=(
            f'=IF(B{r}="","",C{r}*{w_mm}*{d_mm}*{h_mm}/28316846.6)'
        ))
        ws.cell(row=r, column=12).number_format = "0.0"

        # M: Weight per row (lb) = qty × kg × 2.20462
        ws.cell(row=r, column=13, value=(
            f'=IF(B{r}="","",C{r}*{kg}*2.20462)'
        ))
        ws.cell(row=r, column=13).number_format = "#,##0"

    ws.add_data_validation(model_dv)

    # Pre-fill L001 sample
    for i, (mc, qty) in enumerate(SAMPLE_L001):
        ws.cell(row=first_input + i, column=2, value=mc)
        ws.cell(row=first_input + i, column=3, value=qty)

    # Totals + Result block (rows below input table)
    total_row = last_input + 2
    truck_row = total_row + 1
    verdict_row = total_row + 2
    margin_row = total_row + 3

    # Totals row — Σ Min Length, Σ Volume, Σ Weight
    ws.cell(row=total_row, column=8, value="Σ Total:").font = Font(bold=True)
    ws.cell(row=total_row, column=8).alignment = Alignment(horizontal="right")

    sum_length = ws.cell(row=total_row, column=11,
                         value=f"=SUM(K{first_input}:K{last_input})")
    sum_length.font = Font(bold=True, size=12)
    sum_length.number_format = "0.00"

    sum_vol = ws.cell(row=total_row, column=12,
                      value=f"=SUM(L{first_input}:L{last_input})")
    sum_vol.font = Font(bold=True, size=12)
    sum_vol.number_format = "0.0"

    sum_wt = ws.cell(row=total_row, column=13,
                     value=f"=SUM(M{first_input}:M{last_input})")
    sum_wt.font = Font(bold=True, size=12)
    sum_wt.number_format = "#,##0"

    # Truck capacity row
    ws.cell(row=truck_row, column=8, value="Truck capacity:").font = Font(bold=True)
    ws.cell(row=truck_row, column=8).alignment = Alignment(horizontal="right")
    ws.cell(row=truck_row, column=11, value="=C10")
    ws.cell(row=truck_row, column=11).number_format = "0.00"
    ws.cell(row=truck_row, column=11).font = Font(bold=True)
    ws.cell(row=truck_row, column=13, value="=C14")
    ws.cell(row=truck_row, column=13).number_format = "#,##0"
    ws.cell(row=truck_row, column=13).font = Font(bold=True)

    # Verdict row — length AND payload must both pass
    ws.cell(row=verdict_row, column=8, value="Verdict:").font = Font(bold=True, size=14)
    ws.cell(row=verdict_row, column=8).alignment = Alignment(horizontal="right")
    verdict_cell = ws.cell(
        row=verdict_row, column=11,
        value=(
            f'=IF(AND(K{total_row}<=K{truck_row},M{total_row}<=M{truck_row}),'
            f'"✓ FITS","✗ DOES NOT FIT")'
        ),
    )
    verdict_cell.font = Font(bold=True, size=14, color="FFFFFF")
    verdict_cell.alignment = Alignment(horizontal="center")
    ws.merge_cells(start_row=verdict_row, start_column=11,
                   end_row=verdict_row, end_column=13)

    fits_range = f"K{verdict_row}:M{verdict_row}"
    ws.conditional_formatting.add(
        fits_range,
        FormulaRule(formula=[f'$K${verdict_row}="✓ FITS"'],
                    fill=PatternFill("solid", fgColor="1D9E75")),
    )
    ws.conditional_formatting.add(
        fits_range,
        FormulaRule(formula=[f'$K${verdict_row}="✗ DOES NOT FIT"'],
                    fill=PatternFill("solid", fgColor="DC2626")),
    )

    # Margin row — length buffer + payload remaining
    ws.cell(row=margin_row, column=8, value="Margin:").font = Font(bold=True)
    ws.cell(row=margin_row, column=8).alignment = Alignment(horizontal="right")
    ws.cell(row=margin_row, column=11, value=f"=K{truck_row}-K{total_row}")
    ws.cell(row=margin_row, column=11).number_format = '+0.00" ft";-0.00" ft"'
    ws.cell(row=margin_row, column=13, value=f"=M{truck_row}-M{total_row}")
    ws.cell(row=margin_row, column=13).number_format = '+#,##0" lb";-#,##0" lb"'

    # Column widths + freeze
    widths = {"A": 5, "B": 16, "C": 7, "D": 9, "E": 9, "F": 9,
              "G": 11, "H": 9, "I": 14, "J": 14, "K": 14, "L": 14, "M": 14}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A17"


def _write_df_sheet(ws, df: pd.DataFrame) -> None:
    for row in dataframe_to_rows(df, index=False, header=True):
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="374151")
    # Auto-fit width (approximate)
    for col_idx, column_cells in enumerate(ws.columns, start=1):
        max_len = max((len(str(c.value or "")) for c in column_cells), default=10)
        ws.column_dimensions[
            ws.cell(row=1, column=col_idx).column_letter
        ].width = min(max_len + 3, 30)


# ─────────────────────────────────────────────────────────────────────────
# CLI entry point: build into outputs/vantage_fit_calculator.xlsx
# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    proj_dir = Path(__file__).resolve().parent.parent
    src = proj_dir / "data" / "sample_input.xlsx"
    out = proj_dir / "outputs" / "LG_Quick_Fit_Calculator.xlsx"
    out.parent.mkdir(exist_ok=True)
    build_fit_calculator(src, out)
    print(f"Built: {out}")
