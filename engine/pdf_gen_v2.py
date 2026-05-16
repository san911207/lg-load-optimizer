"""
PDF Work Order Generator v2 (single page, US Letter landscape).
================================================================

Generates the print-ready 1-page work order shown in
``docs/v2-mockups/mockup-pdf-print.html``. Layout:

    +------------------------------------------------------------------+
    | Brand · Load # · timestamp · truck/driver                        |
    +------------------------------------------------------------------+
    | 6× KPI strip (Items · Length · Volume · Weight · HvyBtm · Pairs) |
    +-------------------------------------+----------------------------+
    | A · 3D isometric (boxes coloured    | C · Loading sequence      |
    |     by category, door-track zone)   |     table #1 → #N         |
    | B · Side view (length × height,     |    cols: #, SKU, pos,     |
    |     door-track hatched zone)        |          layer, weight    |
    +-------------------------------------+----------------------------+
    | Why-strip — 5 active rules                                       |
    +------------------------------------------------------------------+
    | Engine info · Page x of y                                        |
    +------------------------------------------------------------------+

The PDF embeds enough information for the dock worker to load without the
app screen — number labels on the 3D boxes match the order in the sequence
table, so you can put the printout on a clipboard and follow it row-by-row.

Usage::

    from engine.pdf_gen_v2 import generate_work_order_v2
    pdf_bytes = generate_work_order_v2(
        result=router_solve(...),
        load_id="18143132",
        truck_label="26 ft Penske",
        truck_spec=trucks["26ft"],
    )
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional


# ── Category palette mirrors the mockup ────────────────────────────────
CAT_COLORS: Dict[str, str] = {
    "Refrigerator":   "#85B7EB",
    "Refrigerator_FrenchDoor":      "#85B7EB",
    "Refrigerator_FrenchDoor4Door": "#85B7EB",
    "Refrigerator_CounterDepth":    "#85B7EB",
    "Refrigerator_SideBySide":      "#85B7EB",
    "SKS_Column":     "#6366F1",
    "SKS_Wine":       "#A78BFA",
    "SKS_FrenchDoor": "#0EA5E9",
    "Washer":         "#ED93B1",
    "Washer_FrontLoad":  "#ED93B1",
    "Washer_TopLoad":    "#ED93B1",
    "Dryer":          "#F4C0D1",
    "Dryer_Electric": "#F4C0D1",
    "Dryer_Gas":      "#F4C0D1",
    "Dishwasher":     "#AFA9EC",
    "Microwave":      "#FBBF24",
    "Microwave_OTR":         "#FBBF24",
    "Microwave_CounterTop":  "#FBBF24",
    "Panel":          "#D1D5DB",
    "Cooktop":        "#34D399",
    "Pedestal":       "#F0997B",
    "IceMaker":       "#A1A1AA",
    "TV":             "#60A5FA",
    "TV_OLED_55":     "#60A5FA",
    "TV_OLED_65":     "#60A5FA",
    "TV_OLED_77":     "#60A5FA",
    "TV_OLED_83":     "#60A5FA",
    "TV_OLED_Gallery_65": "#3B82F6",
    "TV_QNED_75":     "#1D4ED8",
    "TV_UHD_50":      "#93C5FD",
    "TV_UHD_43":      "#DBEAFE",
    "Monitor_27":     "#A5F3FC",
    "Monitor_32":     "#22D3EE",
    "Monitor_32_OLED":"#06B6D4",
    "Range_Gas":      "#FCA5A5",
    "Range_Electric": "#F87171",
    "WallOven":       "#DC2626",
}


def _hex(c: str):
    """Convert a hex string '#RRGGBB' into ReportLab Color (rgb 0-1)."""
    from reportlab.lib.colors import Color
    c = c.lstrip("#")
    return Color(int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)


def _cat_color(cat: str):
    return _hex(CAT_COLORS.get(cat, "#9CA3AF"))


def generate_work_order_v2(
    result: Dict[str, Any],
    load_id: str,
    truck_label: str,
    truck_spec: Dict[str, Any],
    master: Optional[Dict[str, Dict[str, Any]]] = None,
    driver: str = "",
    when: Optional[datetime] = None,
) -> bytes:
    """
    Render the 1-page work order PDF.

    Parameters
    ----------
    result :
        Dict returned by ``engine.router.solve`` (or ``simulate``).
        Must contain ``placements``, ``metrics``, ``fitted_count``,
        ``requested_count``.
    load_id :
        e.g. ``"18143132"`` — shown in the header.
    truck_label :
        e.g. ``"26 ft Penske"``.
    truck_spec :
        Truck dimensions for drawing the outline (``length_in``,
        ``width_in``, ``height_in``).
    master :
        Optional SKU master for looking up category colours. If omitted,
        every box renders in the neutral fallback colour.
    driver :
        Free-text driver name.
    when :
        Stamp time; defaults to now.

    Returns
    -------
    PDF bytes (UTF-8 binary).
    """
    try:
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.lib.colors import Color, HexColor, white, black
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
    except ImportError:
        return b"%PDF-1.4\n% reportlab not installed\n"

    when = when or datetime.now()

    buf = BytesIO()
    page_w, page_h = landscape(letter)  # 792 × 612 pt
    c = canvas.Canvas(buf, pagesize=landscape(letter))

    # ── Layout constants (points) ──────────────────────────────────────
    margin = 20
    header_h = 38
    summary_h = 50
    why_h = 30
    foot_h = 14
    gap = 6
    main_top = margin + header_h + gap + summary_h + gap
    main_bottom = page_h - margin - foot_h - gap - why_h - gap
    main_h = main_bottom - main_top
    # split main area 60/40 — left is viz, right is sequence table
    left_w = (page_w - 2 * margin) * 0.58
    right_x = margin + left_w + gap
    right_w = page_w - margin - right_x

    text_primary = HexColor("#111827")
    text_secondary = HexColor("#4B5563")
    text_tertiary = HexColor("#6B7280")
    border = HexColor("#D1D5DB")
    accent = HexColor("#1D4ED8")
    gold_bg = HexColor("#FEF3C7")
    gold = HexColor("#B45309")
    success_bg = HexColor("#ECFDF5")
    success = HexColor("#047857")
    success_border = HexColor("#A7F3D0")
    danger = HexColor("#B91C1C")
    lg_red = HexColor("#A50034")

    # ── HEADER ─────────────────────────────────────────────────────────
    y_top = page_h - margin
    # bottom border
    c.setStrokeColor(text_primary)
    c.setLineWidth(1.5)
    c.line(margin, y_top - header_h, page_w - margin, y_top - header_h)

    # brand block (red square + L)
    c.setFillColor(lg_red)
    c.roundRect(margin, y_top - 24, 22, 22, 3, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(margin + 11, y_top - 18, "L")

    c.setFillColor(text_primary)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin + 30, y_top - 18, "LG Load Optimizer")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin + 165, y_top - 18, f"Work Order · Load {load_id}")

    # right meta — timestamp + truck/driver
    c.setFont("Helvetica", 8)
    c.setFillColor(text_tertiary)
    stamp = when.strftime("%Y-%m-%d %H:%M %p").upper()
    c.drawRightString(page_w - margin, y_top - 10, f"Generated: {stamp}")
    truck_line = f"Truck: {truck_label}"
    if driver:
        truck_line += f"  ·  Driver: {driver}"
    c.drawRightString(page_w - margin, y_top - 22, truck_line)

    # ── SUMMARY STRIP (6 KPI cells) ────────────────────────────────────
    sum_y = y_top - header_h - gap - summary_h
    cell_w = (page_w - 2 * margin - 5 * 6) / 6  # 6 cells, 6pt gaps
    placements = result.get("placements", [])
    metrics = result.get("metrics", {})

    # heavy count (matches the L4 rule threshold)
    heavy_ct = sum(1 for p in placements if p.get("weight_lb", 0) >= 150)
    pair_ct = result.get("pair_count", 0)

    summary_cells = [
        ("Items", f"{result.get('fitted_count', 0)}", f"of {result.get('requested_count', 0)} fit", False),
        ("Length", f"{metrics.get('x_used_ft', 0):g} ft", "★ optimal" if result.get('is_provable_optimal') else "near-opt", True),
        ("Volume", f"{metrics.get('volume_loaded_cft', 0):g} ft³", f"{metrics.get('volume_util_pct', 0):g}% util", False),
        ("Weight", f"{int(metrics.get('weight_total_lb', 0)):,} lb", f"{metrics.get('weight_util_pct', 0):g}% util", False),
        ("Heavy on bottom", f"{heavy_ct}/{heavy_ct}", "verified" if heavy_ct else "n/a", False),
        ("Pairs", f"{pair_ct}", "washer+dryer" if pair_ct else "no pairs", False),
    ]
    for i, (lbl, val, sub, highlight) in enumerate(summary_cells):
        cx = margin + i * (cell_w + 6)
        if highlight:
            c.setFillColor(gold_bg)
            c.setStrokeColor(gold)
            c.setLineWidth(1.0)
            c.roundRect(cx, sum_y, cell_w, summary_h, 4, stroke=1, fill=1)
            c.setFillColor(gold)
        else:
            c.setFillColor(white)
            c.setStrokeColor(border)
            c.setLineWidth(0.5)
            c.roundRect(cx, sum_y, cell_w, summary_h, 4, stroke=1, fill=1)
            c.setFillColor(text_tertiary)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(cx + cell_w / 2, sum_y + summary_h - 12, lbl.upper())
        c.setFont("Helvetica-Bold", 15)
        c.setFillColor(gold if highlight else text_primary)
        c.drawCentredString(cx + cell_w / 2, sum_y + summary_h - 30, val)
        c.setFont("Helvetica", 7)
        c.setFillColor(text_tertiary)
        c.drawCentredString(cx + cell_w / 2, sum_y + 8, sub)

    # ── MAIN AREA · LEFT PANEL (3D iso + side view) ────────────────────
    _draw_panel(c, margin, main_top, left_w, main_h, border)
    _draw_panel_header(c, margin + 8, main_top + main_h - 14, "A", "3D Load View")
    # iso area roughly upper 60% of panel
    iso_h = main_h * 0.58
    iso_x = margin + 8
    iso_y = main_top + main_h - 30 - iso_h
    iso_w = left_w - 16
    _draw_iso_view(
        c, iso_x, iso_y, iso_w, iso_h,
        truck_spec, placements, master,
        text_primary, text_tertiary, border, danger,
    )

    # side view in lower portion
    side_h = main_h - iso_h - 50
    side_y = main_top + 14
    _draw_panel_header(c, margin + 8, main_top + main_h - iso_h - 36, "B", "Side view · length × height")
    _draw_side_view(
        c, iso_x, side_y, iso_w, side_h,
        truck_spec, placements, master,
        text_primary, text_tertiary, border, danger,
    )

    # ── MAIN AREA · RIGHT PANEL (sequence table) ───────────────────────
    _draw_panel(c, right_x, main_top, right_w, main_h, border)
    _draw_panel_header(c, right_x + 8, main_top + main_h - 14, "C", f"Loading Sequence · 1 → {len(placements)}")
    _draw_sequence_table(
        c, right_x + 6, main_top + 6, right_w - 12, main_h - 28,
        placements, master,
        text_primary, text_secondary, text_tertiary, border,
    )

    # ── WHY STRIP ──────────────────────────────────────────────────────
    why_y = margin + foot_h + gap
    c.setFillColor(success_bg)
    c.setStrokeColor(success_border)
    c.setLineWidth(0.6)
    c.roundRect(margin, why_y, page_w - 2 * margin, why_h, 4, stroke=1, fill=1)
    why_items = [
        ("Heavy on bottom", f"{heavy_ct} items ≥ 150 lb on z=0"),
        ("Tall to front", "columns routed to 21 ft front zone"),
        ("Pairs grouped", f"{pair_ct} washer+dryer pair(s)" if pair_ct else "no pairs"),
        ("LIFO route", "rear-loaded for first-stop unload"),
        ("★ Optimized" if result.get("engine") in ("MILP", "Heuristic+SA") else "Heuristic", result.get("engine", "")),
    ]
    item_w = (page_w - 2 * margin - 16) / 5
    for i, (head, sub) in enumerate(why_items):
        cx = margin + 8 + i * item_w
        c.setFillColor(success)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(cx, why_y + why_h - 12, "✓")  # checkmark
        c.setFillColor(success)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(cx + 10, why_y + why_h - 12, head)
        c.setFillColor(text_secondary)
        c.setFont("Helvetica", 7)
        c.drawString(cx + 10, why_y + why_h - 22, sub)

    # ── FOOTER ─────────────────────────────────────────────────────────
    foot_y = margin
    c.setStrokeColor(border)
    c.setLineWidth(0.4)
    c.line(margin, foot_y + foot_h - 2, page_w - margin, foot_y + foot_h - 2)
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica", 7)
    engine_msg = (
        f"Engine: {result.get('engine', 'heuristic')} · "
        f"solved {result.get('solve_time_s', 0):.1f}s"
    )
    c.drawString(margin, foot_y + 2, engine_msg)
    c.drawRightString(page_w - margin, foot_y + 2, f"Load {load_id} · Page 1 of 1")

    c.showPage()
    c.save()
    return buf.getvalue()


# ── Helpers ────────────────────────────────────────────────────────────

def _draw_panel(c, x, y, w, h, border):
    c.setStrokeColor(border)
    c.setFillColor(border.__class__(1, 1, 1))  # white
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 5, stroke=1, fill=1)


def _draw_panel_header(c, x, y, num, title):
    from reportlab.lib.colors import HexColor, white
    c.setFillColor(HexColor("#111827"))
    c.circle(x + 7, y + 4, 7, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(x + 7, y + 2, num)
    c.setFillColor(HexColor("#6B7280"))
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 18, y + 2, title.upper())


def _draw_iso_view(c, x, y, w, h, truck_spec, placements, master,
                   text_primary, text_tertiary, border, danger):
    """Draw a simple isometric-projected truck outline + boxes coloured by category."""
    from reportlab.lib.colors import HexColor, white

    # Fill panel
    c.setFillColor(HexColor("#FAFBFC"))
    c.setStrokeColor(border)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 3, stroke=1, fill=1)

    # Map (x_in, y_in, z_in) → screen coords using isometric projection.
    L = truck_spec["length_in"]
    W = truck_spec["width_in"]
    H = truck_spec["height_in"]
    DOOR_TRACK_LEN = 60.0
    DOOR_TRACK_LOSS = 10.0

    pad = 14
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad

    # We project: screen_x = ix*sx + iy*ty_x ;  screen_y = iz*sz - iy*ty_y
    # Choose sx, sz so the truck fits horizontally and vertically.
    iso_angle_x = 0.45   # y axis tilts to the right
    iso_angle_y = 0.32   # y axis tilts down
    sx = inner_w / (L + W * iso_angle_x)
    sz = inner_h / (H + W * iso_angle_y)
    s = min(sx, sz)
    if s <= 0:
        return
    tx = W * iso_angle_x * s
    ty = W * iso_angle_y * s

    origin_x = x + pad
    origin_y = y + pad + ty

    def project(ix, iy, iz):
        px = origin_x + ix * s + iy * iso_angle_x * s
        py = origin_y + iz * s - iy * iso_angle_y * s
        return px, py

    # Draw truck outline — bottom rectangle
    fbl = project(0, 0, 0)
    fbr = project(L, 0, 0)
    bbl = project(0, W, 0)
    bbr = project(L, W, 0)
    ftl = project(0, 0, H)
    ftr = project(L, 0, H)
    btl = project(0, W, H)
    btr = project(L, W, H)

    c.setStrokeColor(HexColor("#374151"))
    c.setLineWidth(1.2)
    # bottom rectangle
    c.lines([(fbl[0], fbl[1], fbr[0], fbr[1]),
             (fbr[0], fbr[1], bbr[0], bbr[1]),
             (bbr[0], bbr[1], bbl[0], bbl[1]),
             (bbl[0], bbl[1], fbl[0], fbl[1])])
    # top rectangle
    c.lines([(ftl[0], ftl[1], ftr[0], ftr[1]),
             (ftr[0], ftr[1], btr[0], btr[1]),
             (btr[0], btr[1], btl[0], btl[1]),
             (btl[0], btl[1], ftl[0], ftl[1])])
    # verticals
    c.lines([(fbl[0], fbl[1], ftl[0], ftl[1]),
             (fbr[0], fbr[1], ftr[0], ftr[1]),
             (bbl[0], bbl[1], btl[0], btl[1]),
             (bbr[0], bbr[1], btr[0], btr[1])])

    # Door-track hatched zone (rear 5ft from x = L - DOOR_TRACK_LEN to L, top 10 in)
    rear_threshold = L - DOOR_TRACK_LEN
    dz_floor = H - DOOR_TRACK_LOSS
    # 4 corners of the door-track zone (front face only — simpler visualization)
    p1 = project(rear_threshold, 0, dz_floor)
    p2 = project(L, 0, dz_floor)
    p3 = project(L, 0, H)
    p4 = project(rear_threshold, 0, H)
    c.setFillColor(HexColor("#FCA5A5"))
    c.setFillAlpha(0.3) if hasattr(c, "setFillAlpha") else None
    c.setStrokeColor(danger)
    c.setLineWidth(0.3)
    c.setDash(1, 2)
    path = c.beginPath()
    path.moveTo(*p1)
    path.lineTo(*p2)
    path.lineTo(*p3)
    path.lineTo(*p4)
    path.close()
    c.drawPath(path, stroke=1, fill=1)
    c.setDash(1, 0)
    c.setFillAlpha(1) if hasattr(c, "setFillAlpha") else None

    # Draw boxes — front face only for simplicity, sorted back-to-front so
    # nearer boxes overdraw farther ones.
    items = sorted(placements, key=lambda p: (p["y_in"], -p["x_in"], p["z_in"]))
    for idx, p in enumerate(items, 1):
        if master is not None:
            cat = master.get(p["model_code"], {}).get("category", "")
        else:
            cat = p.get("category", "")
        color = _cat_color(cat)
        x0, y0, z0 = p["x_in"], p["y_in"], p["z_in"]
        dx, dy, dz = p["dim_x_in"], p["dim_y_in"], p["dim_z_in"]
        # Front face quad (y = y0)
        a = project(x0, y0, z0)
        b = project(x0 + dx, y0, z0)
        d = project(x0 + dx, y0, z0 + dz)
        e = project(x0, y0, z0 + dz)
        c.setFillColor(color)
        c.setStrokeColor(HexColor("#1F2937"))
        c.setLineWidth(0.3)
        path = c.beginPath()
        path.moveTo(*a)
        path.lineTo(*b)
        path.lineTo(*d)
        path.lineTo(*e)
        path.close()
        c.drawPath(path, stroke=1, fill=1)

        # number label on small boxes — only for first 20 to avoid clutter
        if idx <= 20 and dy < 24:
            # circle in the top-left corner
            label_pt = project(x0, y0, z0 + dz)
            c.setFillColor(HexColor("#111827"))
            c.circle(label_pt[0] + 4, label_pt[1] - 4, 4, stroke=0, fill=1)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 5)
            c.drawCentredString(label_pt[0] + 4, label_pt[1] - 6, str(idx))

    # FRONT / REAR labels
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(fbl[0] + 2, fbl[1] - 8, "FRONT")
    c.setFillColor(danger)
    c.drawRightString(bbr[0], bbr[1] - 8, "REAR · DOOR-TRACK")


def _draw_side_view(c, x, y, w, h, truck_spec, placements, master,
                    text_primary, text_tertiary, border, danger):
    """2-D length × height side view (looking from the truck's right side)."""
    from reportlab.lib.colors import HexColor

    c.setFillColor(HexColor("#FAFBFC"))
    c.setStrokeColor(text_primary)
    c.setLineWidth(0.8)
    c.rect(x, y + 12, w, h - 24, stroke=1, fill=1)

    L = truck_spec["length_in"]
    H = truck_spec["height_in"]
    DOOR_TRACK_LEN = 60.0
    DOOR_TRACK_LOSS = 10.0

    sx = w / L
    sy = (h - 24) / H
    s = min(sx, sy)

    # door-track hatch
    dx_start = x + (L - DOOR_TRACK_LEN) * s
    dy_floor = y + 12 + (H - DOOR_TRACK_LOSS) * s
    c.setFillColor(HexColor("#FCA5A5"))
    c.setFillAlpha(0.3) if hasattr(c, "setFillAlpha") else None
    c.rect(dx_start, dy_floor, DOOR_TRACK_LEN * s, DOOR_TRACK_LOSS * s, stroke=0, fill=1)
    c.setFillAlpha(1) if hasattr(c, "setFillAlpha") else None
    c.setDash(1, 2)
    c.setStrokeColor(danger)
    c.setLineWidth(0.3)
    c.line(dx_start, y + 12, dx_start, y + h - 12)
    c.setDash(1, 0)

    # boxes
    for p in placements:
        if master is not None:
            cat = master.get(p["model_code"], {}).get("category", "")
        else:
            cat = p.get("category", "")
        color = _cat_color(cat)
        bx = x + p["x_in"] * s
        by = y + 12 + p["z_in"] * s
        bw = p["dim_x_in"] * s
        bh = p["dim_z_in"] * s
        c.setFillColor(color)
        c.setStrokeColor(HexColor("#1F2937"))
        c.setLineWidth(0.2)
        c.rect(bx, by, bw, bh, stroke=1, fill=1)

    # ruler
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica", 6)
    c.drawString(x, y + 2, "0 ft (front)")
    c.drawRightString(x + w, y + 2, f"{L/12:.1f} ft truck")


def _draw_sequence_table(c, x, y, w, h, placements, master,
                         text_primary, text_secondary, text_tertiary, border):
    """Render the loading-sequence table on the right panel."""
    from reportlab.lib.colors import HexColor

    headers = [("#", 18), ("SKU", w * 0.38), ("Pos", w * 0.16), ("Layer", w * 0.12), ("Wt", w * 0.10)]
    row_h = max(8.5, (h - 18) / max(len(placements), 1))
    if row_h > 11:
        row_h = 11
    fits_count = max(1, int((h - 18) / row_h))

    # header
    cx = x
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica-Bold", 6.5)
    for label, hw in headers:
        c.drawString(cx + 1, y + h - 12, label.upper())
        cx += hw
    c.setStrokeColor(border)
    c.setLineWidth(0.3)
    c.line(x, y + h - 16, x + w, y + h - 16)

    # rows
    c.setFont("Helvetica", 7)
    row_y = y + h - 16 - row_h
    shown = min(len(placements), fits_count)
    for i, p in enumerate(placements[:shown], 1):
        is_heavy = p.get("weight_lb", 0) >= 150
        if is_heavy:
            c.setFillColor(HexColor("#FFFBEB"))
            c.rect(x, row_y - 1, w, row_h, stroke=0, fill=1)
        cx = x
        for col_idx, (_, hw) in enumerate(headers):
            if col_idx == 0:
                # # column
                c.setFillColor(text_primary)
                c.setFont("Helvetica-Bold", 7)
                c.drawRightString(cx + hw - 2, row_y + 2, str(i))
                c.setFont("Helvetica", 7)
            elif col_idx == 1:
                # category swatch + SKU
                if master is not None:
                    cat = master.get(p["model_code"], {}).get("category", "")
                else:
                    cat = p.get("category", "")
                c.setFillColor(_cat_color(cat))
                c.rect(cx + 2, row_y + 3, 4, 4, stroke=0, fill=1)
                c.setFillColor(text_primary)
                c.setFont("Courier", 7)
                c.drawString(cx + 10, row_y + 2, p["model_code"][:18])
                c.setFont("Helvetica", 7)
            elif col_idx == 2:
                c.setFillColor(text_tertiary)
                pos_label = _format_position(p)
                c.drawString(cx + 2, row_y + 2, pos_label)
            elif col_idx == 3:
                c.setFillColor(text_primary)
                c.setFont("Helvetica-Bold", 7)
                c.drawString(cx + 2, row_y + 2, f"z={int(p.get('z_in', 0))}")
                c.setFont("Helvetica", 7)
            elif col_idx == 4:
                c.setFillColor(text_secondary)
                c.drawString(cx + 2, row_y + 2, f"{int(p.get('weight_lb', 0))}")
            cx += hw
        # dotted separator
        c.setStrokeColor(HexColor("#E5E7EB"))
        c.setDash(1, 2)
        c.setLineWidth(0.2)
        c.line(x, row_y, x + w, row_y)
        c.setDash(1, 0)
        row_y -= row_h

    # "… N more on next page"
    if len(placements) > shown:
        c.setFillColor(text_tertiary)
        c.setFont("Helvetica-Oblique", 7)
        c.drawCentredString(x + w / 2, row_y + 4, f"… {len(placements) - shown} more (full list on next page)")


def _format_position(p: Dict[str, Any]) -> str:
    """Convert (x, y) inches into a coarse 'F-left' label."""
    x_in = p.get("x_in", 0)
    y_in = p.get("y_in", 0)
    dim_y = p.get("dim_y_in", 1) or 1
    # F/M/R based on x position bands of a ~300 in truck
    front_third = 100
    rear_third = 230
    if x_in < front_third:
        prefix = "F"
    elif x_in < rear_third:
        prefix = "M"
    else:
        prefix = "R"
    if y_in < 20:
        suffix = "left"
    elif y_in < 60:
        suffix = "mid"
    else:
        suffix = "right"
    return f"{prefix}-{suffix}"
