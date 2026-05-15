"""
PDF Work Order Generator
========================
Generates a print-friendly 1-page work order for dock workers.

Phase 0: stub (returns placeholder)
Phase 1: full implementation with reportlab

Usage:
    from engine.pdf_gen import generate_work_order
    pdf_bytes = generate_work_order(simulation_result, load_id="L001")
    with open("output.pdf", "wb") as f:
        f.write(pdf_bytes)
"""
from pathlib import Path
from typing import Dict, Any


def generate_work_order(
    simulation_result: Dict[str, Any],
    load_id: str = "L001",
    truck_label: str = "26ft Box Truck",
) -> bytes:
    """
    Generate a 1-page PDF work order.

    Args:
        simulation_result: dict from engine.best_packer.simulate()
        load_id: e.g. "L001"
        truck_label: human-readable truck label (e.g. "26ft Box Truck", "53ft Dry Van")

    Layout:
      Header: Load ID, truck, date, planner name
      Body: 5 step cards in 2x3 grid with mini side-view + handling tips
      Footer: pre-load checklist + secure checklist

    Returns: PDF as bytes (suitable for HTTP response or file write)
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors
        from io import BytesIO
    except ImportError:
        # reportlab not installed → return placeholder
        return b"%PDF-1.4\n%PDF stub. Install reportlab to enable PDF generation.\n"

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    metrics = simulation_result["metrics"]

    # Header
    story.append(Paragraph(f"<b>Load Work Order — {load_id}</b>", styles["Title"]))
    story.append(Paragraph(
        f"Truck: {truck_label} · Units: {simulation_result['fitted_count']} · "
        f"Length used: {metrics['x_used_ft']} ft ({metrics['compactness_pct']}%)",
        styles["Normal"]
    ))
    story.append(Spacer(1, 12))

    # Zone breakdown table
    data = [["Step", "Zone · Model", "Qty", "Layout", "Position"]]
    zones = {}
    for p in simulation_result["placements"]:
        zones.setdefault(p["model_code"], []).append(p)
    step_num = 1
    for model, ps in zones.items():
        lanes = len(set(p["lane"] for p in ps))
        layers = len(set(p["layer"] for p in ps))
        rows = len(set(p["x_mm"] for p in ps))
        x_start = min(p["x_mm"] for p in ps) / 304.8
        x_end = max(p["x_mm"] + p["dim_x_mm"] for p in ps) / 304.8
        data.append([
            str(step_num),
            model,
            str(len(ps)),
            f"{rows}R × {lanes}L × {layers}T",
            f"{x_start:.1f} → {x_end:.1f} ft",
        ])
        step_num += 1

    t = Table(data, colWidths=[40, 120, 50, 110, 120])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F6E56")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # Pre-load checklist
    story.append(Paragraph("<b>Pre-load Checklist</b>", styles["Heading2"]))
    checklist = [
        "□ Hand truck × 2 (1 for refrigerators, 1 for general)",
        "□ Ratchet straps × 4 (zone separators)",
        "□ Moving blankets × 6",
        "□ 2 workers minimum (for refrigerators and stacking)",
        "□ Safety shoes + gloves",
        "□ Verify ↑ This Side Up arrows on all boxes before loading",
    ]
    for item in checklist:
        story.append(Paragraph(item, styles["Normal"]))
    story.append(Spacer(1, 12))

    # Secure & inspect
    story.append(Paragraph("<b>After loading — Secure & Inspect</b>", styles["Heading2"]))
    secure = [
        "□ 4 ratchet straps tightened (between zones + rear)",
        "□ All ↑ arrows verified pointing up",
        "□ Rear 5 ft clear of door track (10\" headroom)",
        "□ Door rolled down and sealed",
    ]
    for item in secure:
        story.append(Paragraph(item, styles["Normal"]))

    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def save_work_order(
    simulation_result: Dict[str, Any],
    load_id: str,
    out_path: "str | Path",
    truck_label: str = "26ft Box Truck",
):
    """Convenience function: generate and save to file."""
    pdf_bytes = generate_work_order(simulation_result, load_id, truck_label=truck_label)
    Path(out_path).write_bytes(pdf_bytes)
    return out_path
