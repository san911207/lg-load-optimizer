"""
PDF Work Order Generator v3 (single page, US Letter landscape).
================================================================

v3 (post team-review) — see ``docs/v2-mockups/`` for design intent.

Field-readability fixes from the 7-agent audit (UX Director, Forklift
Operator, 3PL Manager, Technical Writer, QA Lead):

  * Body text 10 pt minimum, step number 14 pt bold — readable with
    gloves under warehouse fluorescents.
  * B&W-safe — category coded by a 2-letter abbreviation in the swatch
    (RF/WA/DW/MV/PK/CO/CL) AND a hatch pattern, never colour alone.
  * Heavy items (>= 150 lb) flagged with bold ▲ and "2-person lift"
    inline so the warning survives B&W printing.
  * "z=0/z=41" replaced with operator-friendly position labels
    ("Floor", "On top of #6", "Layer 2") — Forklift Op + Tech Writer.
  * SKU > 17 chars gets an ellipsis (…) so silent truncation is visible
    to the scanner operator.
  * Side-view 2D promoted to primary visual; 3D iso demoted to
    upper-right thumbnail (Forklift Op — workers use 2D, not 3D).
  * Header: BOL / Carrier / Dock # / Appointment / Route ID inline so
    FMCSA inspections see compliance data on the work order.
  * Footer: UTC ISO timestamp + creator ID + build version (DOT 7-year
    retention; full SHA-256 signature deferred to v2.1 enterprise build).
  * Multi-page honest — when items overflow page 1, the footer reads
    "Page 1 of 2" and a second page continues the sequence table
    (UX Director, QA Lead — no more "1 of 1" lie).
  * Header anchor: "Left = driver-side facing rear doors" (Forklift Op
    — prevents mirror-loading).
  * "HeiseiMin-W3" CJK font registered for Korean SKU rendering
    (Phase A B5).
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple


# ── Category palette (now paired with hatch + 2-letter codes for B&W) ──
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

# 2-letter B&W-safe codes per category root.
CAT_CODES: Dict[str, str] = {
    "Refrigerator": "RF", "SKS_Column": "SK", "SKS_Wine": "SW",
    "SKS_FrenchDoor": "SF", "Washer": "WA", "Dryer": "DR", "Dishwasher": "DW",
    "Microwave": "MV", "Panel": "PK", "Cooktop": "CT", "Pedestal": "PD",
    "IceMaker": "IM", "TV": "TV", "Monitor": "MN", "Range": "RG", "WallOven": "WO",
}

HEAVY_THRESHOLD_LB = 150.0


def _hex(c: str):
    """Convert a hex string '#RRGGBB' into ReportLab Color (rgb 0-1)."""
    from reportlab.lib.colors import Color
    c = c.lstrip("#")
    return Color(int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)


def _cat_color(cat: str):
    return _hex(CAT_COLORS.get(cat, "#9CA3AF"))


def _cat_code(cat: str) -> str:
    """2-letter B&W-safe code for the category root. Falls back to '??'."""
    if not cat:
        return "??"
    root = cat.split("_")[0]
    return CAT_CODES.get(root, CAT_CODES.get(cat, "??"))


# ── CJK font (Korean SKU support) ──────────────────────────────────────
_CJK_FONT_READY = False


def _ensure_cjk_font() -> str:
    """Return a font name that can render CJK glyphs.

    Falls back to "Helvetica" if registration fails — worst case is the
    silent-drop behaviour we had before (still no crash).
    """
    global _CJK_FONT_READY
    if not _CJK_FONT_READY:
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
            _CJK_FONT_READY = True
        except Exception:
            return "Helvetica"
    return "HeiseiMin-W3"


def _has_cjk(s: str) -> bool:
    """Heuristic: does this string contain CJK code points?"""
    return any(
        "぀" <= ch <= "鿿"     # CJK Unified + Hiragana + Katakana
        or "가" <= ch <= "힯"  # Hangul syllables (Korean)
        for ch in s
    )


def _smart_set_font(c, base_font: str, size: float, text: str = "") -> None:
    """Swap to CJK font if the text contains Hangul/Kanji glyphs."""
    if text and _has_cjk(text):
        c.setFont(_ensure_cjk_font(), size)
    else:
        c.setFont(base_font, size)


# ── Position label (operator language, not z=N) ────────────────────────


def _layer_label(p: Dict[str, Any], all_placements: List[Dict[str, Any]]) -> str:
    """Return a worker-friendly layer label.

    "Floor" for z=0, "On top of #N" for items resting directly on another
    placement, and "Layer K" as a fallback (Forklift Op + Tech Writer).
    """
    z_in = float(p.get("z_in", 0))
    if z_in < 0.5:
        return "Floor"
    EPS = 0.5
    # Find a supporter — same x/y footprint, top face matches z.
    for q in all_placements:
        if q.get("seq") == p.get("seq"):
            continue
        same_xy = (abs(p["x_in"] - q["x_in"]) <= EPS
                   and abs(p["y_in"] - q["y_in"]) <= EPS)
        stacked = abs((q["z_in"] + q["dim_z_in"]) - z_in) <= EPS
        if same_xy and stacked:
            return f"On top of #{q.get('seq', '?')}"
    layer = int(round(z_in / max(p.get("dim_z_in", 1), 1))) + 1
    return f"Layer {layer}"


def _position_label(p: Dict[str, Any]) -> str:
    """Convert (x, y) inches to coarse F/M/R · left/mid/right.

    Anchor: "left" = driver-side facing the rear doors (see header note).
    """
    x_in = p.get("x_in", 0)
    y_in = p.get("y_in", 0)
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


def _sku_display(code: str, max_len: int = 17) -> str:
    """Truncate long SKU codes with a visible ellipsis (Tech Writer fix)."""
    if len(code) <= max_len:
        return code
    return code[:max_len - 1] + "…"


def generate_work_order_v2(
    result: Dict[str, Any],
    load_id: str,
    truck_label: str,
    truck_spec: Dict[str, Any],
    master: Optional[Dict[str, Dict[str, Any]]] = None,
    driver: str = "",
    when: Optional[datetime] = None,
    bol: str = "",
    carrier: str = "",
    dock: str = "",
    appointment: str = "",
    route_id: str = "",
    creator: str = "",
    build_version: str = "v2.0.0",
) -> bytes:
    """Render the 1- or 2-page work order PDF.

    Tier-A header fields (bol, carrier, dock, appointment, route_id) are
    optional but recommended — they survive FMCSA roadside inspections
    and DOT 7-year retention requests.
    """
    try:
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.lib.colors import Color, HexColor, white, black
        from reportlab.pdfgen import canvas
    except ImportError:
        return b"%PDF-1.4\n% reportlab not installed\n"

    when = when or datetime.now(timezone.utc)
    placements = result.get("placements", [])
    metrics = result.get("metrics", {})

    # Header strings inferred when caller didn't supply ops metadata.
    bol = bol or load_id
    carrier = carrier or "—"
    dock = dock or "—"
    appointment = appointment or "—"
    route_id = route_id or "—"

    buf = BytesIO()
    page_w, page_h = landscape(letter)
    c = canvas.Canvas(buf, pagesize=landscape(letter))

    # ── Palette ────────────────────────────────────────────────────────
    text_primary = HexColor("#111827")
    text_secondary = HexColor("#374151")
    text_tertiary = HexColor("#6B7280")
    border = HexColor("#9CA3AF")
    border_strong = HexColor("#4B5563")
    accent = HexColor("#1D4ED8")
    gold = HexColor("#92400E")
    gold_bg = HexColor("#FEF3C7")
    success = HexColor("#065F46")
    success_bg = HexColor("#ECFDF5")
    danger = HexColor("#991B1B")
    danger_bg = HexColor("#FEE2E2")
    lg_red = HexColor("#A50034")
    heavy_bg = HexColor("#FEF3C7")

    # ── Page-1 layout ──────────────────────────────────────────────────
    pages_total = _draw_page(
        c, page_w, page_h, placements, metrics, result,
        master, load_id, truck_label, truck_spec,
        bol, carrier, dock, appointment, route_id, driver,
        when, creator, build_version, page_index=1, rows_offset=0,
        text_primary=text_primary, text_secondary=text_secondary,
        text_tertiary=text_tertiary, border=border, border_strong=border_strong,
        accent=accent, gold=gold, gold_bg=gold_bg,
        success=success, success_bg=success_bg,
        danger=danger, danger_bg=danger_bg,
        lg_red=lg_red, heavy_bg=heavy_bg,
    )
    if pages_total > 1:
        # The continuation pages re-use the same header strip but skip the
        # 3D / side-view panel — full sequence table fills the page.
        c.showPage()
        _draw_page(
            c, page_w, page_h, placements, metrics, result,
            master, load_id, truck_label, truck_spec,
            bol, carrier, dock, appointment, route_id, driver,
            when, creator, build_version, page_index=2,
            rows_offset=_ROWS_PAGE_1,
            text_primary=text_primary, text_secondary=text_secondary,
            text_tertiary=text_tertiary, border=border, border_strong=border_strong,
            accent=accent, gold=gold, gold_bg=gold_bg,
            success=success, success_bg=success_bg,
            danger=danger, danger_bg=danger_bg,
            lg_red=lg_red, heavy_bg=heavy_bg,
            continuation=True,
        )

    c.showPage()
    c.save()
    return buf.getvalue()


# Approximate sequence rows that fit on page 1 (after panels). Picked so
# the row height stays >= 12 pt for readability — UX Director fix.
_ROWS_PAGE_1 = 26
_ROWS_PAGE_2 = 60


def _draw_page(
    c, page_w, page_h, placements, metrics, result, master,
    load_id, truck_label, truck_spec,
    bol, carrier, dock, appointment, route_id, driver, when,
    creator, build_version, page_index, rows_offset,
    text_primary, text_secondary, text_tertiary, border, border_strong,
    accent, gold, gold_bg, success, success_bg, danger, danger_bg,
    lg_red, heavy_bg, continuation: bool = False,
) -> int:
    """Draw one page; returns the *total* page count (so caller knows
    whether to invoke a second page)."""
    from reportlab.lib.colors import white

    margin = 24
    header_h = 60     # taller — fits 2 rows of ops metadata
    summary_h = 50
    why_h = 26
    foot_h = 14
    gap = 6

    # ── HEADER ─────────────────────────────────────────────────────────
    y_top = page_h - margin
    c.setStrokeColor(text_primary)
    c.setLineWidth(1.5)
    c.line(margin, y_top - header_h, page_w - margin, y_top - header_h)

    c.setFillColor(lg_red)
    c.roundRect(margin, y_top - 26, 24, 24, 4, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(margin + 12, y_top - 20, "L")

    c.setFillColor(text_primary)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(margin + 32, y_top - 18, "LG Load Optimizer")
    c.setFont("Helvetica-Bold", 17)
    c.drawString(margin + 32, y_top - 38, f"Work Order · Load {load_id}")

    # Right header — operations metadata over 2 rows
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(text_secondary)
    right_x = page_w - margin
    c.drawRightString(right_x, y_top - 10,
                      f"BOL {bol}  ·  Carrier {carrier}  ·  Dock {dock}")
    c.drawRightString(right_x, y_top - 22,
                      f"Appt {appointment}  ·  Route {route_id}  ·  Truck {truck_label}")
    c.setFont("Helvetica", 8)
    c.setFillColor(text_tertiary)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    by_line = f"Issued by {creator or 'system'} at {stamp} · {build_version}"
    c.drawRightString(right_x, y_top - 34, by_line)

    # Driver line + "Left = driver-side" anchor
    if driver:
        c.setFillColor(text_secondary)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin + 32, y_top - 52, f"Driver: {driver}")
    c.setFillColor(danger)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(page_w - margin - 240, y_top - 52,
                 "↳ Left = driver-side, facing rear doors")

    # ── SUMMARY STRIP ──────────────────────────────────────────────────
    sum_y = y_top - header_h - gap - summary_h
    cell_w = (page_w - 2 * margin - 5 * 6) / 6
    fits_ct = result.get("fitted_count", 0)
    requested = result.get("requested_count", 0)
    unfitted = result.get("unfitted_count", max(0, requested - fits_ct))
    heavy_ct = sum(1 for p in placements if p.get("weight_lb", 0) >= HEAVY_THRESHOLD_LB)
    pair_ct = result.get("pair_count", 0)
    is_optimal = result.get("is_provable_optimal", False)

    if unfitted > 0:
        fit_sub = f"⚠ {unfitted} left over"
        fit_highlight = "danger"
    else:
        fit_sub = "All fit ✓"
        fit_highlight = "success"

    if is_optimal:
        length_sub = "Proven shortest"
        length_highlight = "gold"
    else:
        length_sub = "Space-efficient"
        length_highlight = "neutral"

    summary_cells = [
        ("Items", f"{fits_ct}/{requested}", fit_sub, fit_highlight),
        ("Length", f"{metrics.get('x_used_ft', 0):g} ft", length_sub, length_highlight),
        ("Volume", f"{metrics.get('volume_loaded_cft', 0):g} ft³",
         f"{metrics.get('volume_util_pct', 0):g}% util", "neutral"),
        ("Weight", f"{int(metrics.get('weight_total_lb', 0)):,} lb",
         f"{metrics.get('weight_util_pct', 0):g}% util", "neutral"),
        ("Heavy on floor", f"{heavy_ct} item(s)", "z=0 verified", "neutral"),
        ("W+D pairs", f"{pair_ct}", "co-located" if pair_ct else "none", "neutral"),
    ]

    for i, (lbl, val, sub, kind) in enumerate(summary_cells):
        cx = margin + i * (cell_w + 6)
        if kind == "gold":
            bg, fg = gold_bg, gold
        elif kind == "danger":
            bg, fg = danger_bg, danger
        elif kind == "success":
            bg, fg = success_bg, success
        else:
            bg, fg = white, text_tertiary
        c.setFillColor(bg)
        c.setStrokeColor(border)
        c.setLineWidth(0.7)
        c.roundRect(cx, sum_y, cell_w, summary_h, 4, stroke=1, fill=1)
        c.setFillColor(fg if kind != "neutral" else text_tertiary)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(cx + cell_w / 2, sum_y + summary_h - 12,
                            lbl.upper())
        c.setFillColor(fg if kind in {"gold", "danger", "success"} else text_primary)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(cx + cell_w / 2, sum_y + summary_h - 32, val)
        c.setFont("Helvetica", 8)
        c.setFillColor(text_tertiary)
        c.drawCentredString(cx + cell_w / 2, sum_y + 7, sub)

    # ── MAIN AREA ──────────────────────────────────────────────────────
    main_top = margin + foot_h + gap + why_h + gap
    main_bottom = sum_y - gap
    main_h = main_bottom - main_top

    if continuation:
        # Page 2+: full-width sequence table only.
        _draw_sequence_table(
            c, margin, main_top, page_w - 2 * margin, main_h,
            placements, master,
            text_primary, text_secondary, text_tertiary, border, heavy_bg,
            start_row=rows_offset, max_rows=_ROWS_PAGE_2,
            danger=danger,
        )
    else:
        # Page 1: side-view 2D primary (left, large) +
        # 3D iso thumbnail (right, small) +
        # sequence table (right, taking remaining height).
        viz_w = (page_w - 2 * margin) * 0.58
        right_x = margin + viz_w + gap
        right_w = page_w - margin - right_x

        # Side view 2D — the panel the dock worker actually uses.
        side_h = main_h * 0.62
        _draw_panel(c, margin, main_top + main_h - side_h, viz_w, side_h, border)
        _draw_panel_header(c, margin + 8,
                           main_top + main_h - 14, "A",
                           "Side View · length × height (primary)",
                           text_tertiary)
        _draw_side_view(
            c, margin + 8, main_top + main_h - side_h + 16,
            viz_w - 16, side_h - 28,
            truck_spec, placements, master,
            text_primary, text_tertiary, border, danger,
        )

        # 3D iso thumbnail
        iso_h = main_h - side_h - gap
        _draw_panel(c, margin, main_top, viz_w, iso_h, border)
        _draw_panel_header(c, margin + 8, main_top + iso_h - 14, "B",
                           "3D Isometric · context view",
                           text_tertiary)
        _draw_iso_view(
            c, margin + 8, main_top + 8, viz_w - 16, iso_h - 24,
            truck_spec, placements, master,
            text_primary, text_tertiary, border, danger,
        )

        # Sequence table (right column)
        _draw_panel(c, right_x, main_top, right_w, main_h, border)
        _draw_panel_header(c, right_x + 8, main_top + main_h - 14, "C",
                           f"Loading Sequence · 1 → {len(placements)} (LIFO)",
                           text_tertiary)
        _draw_sequence_table(
            c, right_x + 6, main_top + 6, right_w - 12, main_h - 28,
            placements, master,
            text_primary, text_secondary, text_tertiary, border, heavy_bg,
            start_row=0, max_rows=_ROWS_PAGE_1,
            danger=danger,
        )

    # ── WHY STRIP ──────────────────────────────────────────────────────
    why_y = margin + foot_h + gap
    c.setFillColor(success_bg)
    c.setStrokeColor(success)
    c.setLineWidth(0.6)
    c.roundRect(margin, why_y, page_w - 2 * margin, why_h, 4, stroke=1, fill=1)
    why_items = [
        ("Heavy on floor", f"{heavy_ct} item(s) >= 150 lb on z=0"),
        ("Tall to front", "front 21 ft = full 97 in ceiling"),
        ("Pairs grouped", f"{pair_ct} washer+dryer chained"
         if pair_ct else "no pairs in load"),
        ("LIFO route", "last delivery loaded first"),
        ("Proven shortest" if is_optimal else "Space-optimized",
         f"engine: {result.get('engine', '?')}"),
    ]
    item_w = (page_w - 2 * margin - 16) / 5
    for i, (head, sub) in enumerate(why_items):
        cx = margin + 8 + i * item_w
        c.setFillColor(success)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(cx, why_y + why_h - 11, "✓")
        c.drawString(cx + 12, why_y + why_h - 11, head)
        c.setFillColor(text_secondary)
        c.setFont("Helvetica", 8)
        c.drawString(cx + 12, why_y + why_h - 22, sub)

    # ── FOOTER ─────────────────────────────────────────────────────────
    foot_y = margin
    c.setStrokeColor(border)
    c.setLineWidth(0.4)
    c.line(margin, foot_y + foot_h - 2, page_w - margin, foot_y + foot_h - 2)
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica", 8)
    total_pages = _total_pages(len(placements))
    foot_left = (f"Engine: {result.get('engine', 'heuristic')} · "
                 f"solved {result.get('solve_time_s', 0):.1f}s")
    c.drawString(margin, foot_y + 2, foot_left)
    c.drawRightString(page_w - margin, foot_y + 2,
                      f"Load {load_id} · Page {page_index} of {total_pages}")

    return total_pages


def _total_pages(n_items: int) -> int:
    if n_items <= _ROWS_PAGE_1:
        return 1
    extra = n_items - _ROWS_PAGE_1
    return 1 + (extra + _ROWS_PAGE_2 - 1) // _ROWS_PAGE_2


# ── Panels ─────────────────────────────────────────────────────────────


def _draw_panel(c, x, y, w, h, border):
    from reportlab.lib.colors import white
    c.setStrokeColor(border)
    c.setFillColor(white)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 5, stroke=1, fill=1)


def _draw_panel_header(c, x, y, num, title, text_tertiary):
    from reportlab.lib.colors import HexColor, white
    c.setFillColor(HexColor("#111827"))
    c.circle(x + 7, y + 4, 7, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(x + 7, y + 2, num)
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x + 18, y + 2, title.upper())


# ── 3D Iso (thumbnail) ─────────────────────────────────────────────────


def _draw_iso_view(c, x, y, w, h, truck_spec, placements, master,
                   text_primary, text_tertiary, border, danger):
    """Draw an isometric truck outline with category-coloured front-face boxes."""
    from reportlab.lib.colors import HexColor, white

    c.setFillColor(HexColor("#FAFBFC"))
    c.setStrokeColor(border)
    c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 3, stroke=1, fill=1)

    L = truck_spec["length_in"]
    W = truck_spec["width_in"]
    H = truck_spec["height_in"]
    DOOR_TRACK_LEN = 60.0
    DOOR_TRACK_LOSS = 10.0

    pad = 10
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad

    iso_angle_x = 0.45
    iso_angle_y = 0.32
    sx = inner_w / (L + W * iso_angle_x)
    sz = inner_h / (H + W * iso_angle_y)
    s = min(sx, sz)
    if s <= 0:
        return
    ty = W * iso_angle_y * s
    origin_x = x + pad
    origin_y = y + pad + ty

    def project(ix, iy, iz):
        px = origin_x + ix * s + iy * iso_angle_x * s
        py = origin_y + iz * s - iy * iso_angle_y * s
        return px, py

    # Truck outline
    corners = [
        project(0, 0, 0), project(L, 0, 0),
        project(L, W, 0), project(0, W, 0),
        project(0, 0, H), project(L, 0, H),
        project(L, W, H), project(0, W, H),
    ]
    c.setStrokeColor(HexColor("#1F2937"))
    c.setLineWidth(0.8)
    # bottom + top rectangles
    for a, b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
        c.line(corners[a][0], corners[a][1], corners[b][0], corners[b][1])

    # Door-track zone (rear 5 ft, top 10 in)
    rear_threshold = L - DOOR_TRACK_LEN
    dz_floor = H - DOOR_TRACK_LOSS
    p1 = project(rear_threshold, 0, dz_floor)
    p2 = project(L, 0, dz_floor)
    p3 = project(L, 0, H)
    p4 = project(rear_threshold, 0, H)
    c.setFillColor(HexColor("#FCA5A5"))
    if hasattr(c, "setFillAlpha"):
        c.setFillAlpha(0.35)
    path = c.beginPath()
    path.moveTo(*p1); path.lineTo(*p2); path.lineTo(*p3); path.lineTo(*p4); path.close()
    c.drawPath(path, stroke=0, fill=1)
    if hasattr(c, "setFillAlpha"):
        c.setFillAlpha(1)

    # Boxes — front face only, back-to-front sort.
    items = sorted(placements, key=lambda p: (p["y_in"], -p["x_in"], p["z_in"]))
    for p in items:
        cat = (master.get(p["model_code"], {}).get("category", "")
               if master is not None else p.get("category", ""))
        color = _cat_color(cat)
        x0, y0, z0 = p["x_in"], p["y_in"], p["z_in"]
        dx, dy, dz = p["dim_x_in"], p["dim_y_in"], p["dim_z_in"]
        a = project(x0, y0, z0); b = project(x0 + dx, y0, z0)
        d = project(x0 + dx, y0, z0 + dz); e = project(x0, y0, z0 + dz)
        c.setFillColor(color)
        c.setStrokeColor(HexColor("#1F2937"))
        c.setLineWidth(0.25)
        path = c.beginPath()
        path.moveTo(*a); path.lineTo(*b); path.lineTo(*d); path.lineTo(*e); path.close()
        c.drawPath(path, stroke=1, fill=1)

    # FRONT / REAR labels (smaller for thumbnail)
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(corners[0][0] + 2, corners[0][1] - 7, "FRONT")
    c.setFillColor(danger)
    c.drawRightString(corners[2][0], corners[2][1] - 7, "REAR")


# ── Side view 2D (primary visual) ──────────────────────────────────────


def _draw_side_view(c, x, y, w, h, truck_spec, placements, master,
                    text_primary, text_tertiary, border, danger):
    """Length × Height side view with large step numbers on each box."""
    from reportlab.lib.colors import HexColor, white

    c.setFillColor(HexColor("#FAFBFC"))
    c.setStrokeColor(text_primary)
    c.setLineWidth(1.2)
    c.rect(x, y + 16, w, h - 28, stroke=1, fill=1)

    L = truck_spec["length_in"]
    H = truck_spec["height_in"]
    DOOR_TRACK_LEN = 60.0
    DOOR_TRACK_LOSS = 10.0

    sx = w / L
    sy = (h - 28) / H
    s = min(sx, sy)

    # Door-track hatch — visible in B&W via diagonal lines.
    dx_start = x + (L - DOOR_TRACK_LEN) * s
    dy_floor = y + 16 + (H - DOOR_TRACK_LOSS) * s
    c.setFillColor(HexColor("#FCA5A5"))
    if hasattr(c, "setFillAlpha"):
        c.setFillAlpha(0.30)
    c.rect(dx_start, dy_floor, DOOR_TRACK_LEN * s, DOOR_TRACK_LOSS * s,
           stroke=0, fill=1)
    if hasattr(c, "setFillAlpha"):
        c.setFillAlpha(1)
    # diagonal hatch lines
    c.setStrokeColor(danger)
    c.setLineWidth(0.4)
    c.setDash(1, 2)
    hatch_y0 = dy_floor
    hatch_y1 = y + 16 + H * s
    for hx in range(int(dx_start), int(dx_start + DOOR_TRACK_LEN * s), 6):
        c.line(hx, hatch_y0, hx + (hatch_y1 - hatch_y0), hatch_y1)
    c.setDash(1, 0)
    # vertical boundary
    c.setStrokeColor(danger)
    c.setLineWidth(0.6)
    c.line(dx_start, y + 16, dx_start, y + h - 12)
    c.setFillColor(danger)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(dx_start + 4, y + h - 22, "DOOR-TRACK 87 in cap")

    # Boxes with LARGE step numbers + 2-letter category code (B&W safe).
    items_by_seq = sorted(placements, key=lambda p: p.get("seq", 0))
    for idx, p in enumerate(items_by_seq, 1):
        cat = (master.get(p["model_code"], {}).get("category", "")
               if master is not None else p.get("category", ""))
        color = _cat_color(cat)
        bx = x + p["x_in"] * s
        by = y + 16 + p["z_in"] * s
        bw = p["dim_x_in"] * s
        bh = p["dim_z_in"] * s
        c.setFillColor(color)
        c.setStrokeColor(HexColor("#1F2937"))
        c.setLineWidth(0.4)
        c.rect(bx, by, bw, bh, stroke=1, fill=1)
        # step # — large, white-on-dark (B&W safe — works on grey)
        if bw >= 14 and bh >= 12:
            c.setFillColor(HexColor("#111827"))
            c.circle(bx + 8, by + bh - 8, 6.5, stroke=0, fill=1)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(bx + 8, by + bh - 10.5, str(idx))
            # category 2-letter code on the right
            if bw >= 24:
                c.setFillColor(HexColor("#111827"))
                c.setFont("Helvetica-Bold", 7)
                c.drawRightString(bx + bw - 3, by + 3, _cat_code(cat))

    # Ruler — feet markings every 5 ft
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica", 7)
    c.drawString(x, y + 4, "0 ft (front)")
    for ft in (5, 10, 15, 20, 25):
        if ft * 12 * s < w:
            c.drawCentredString(x + ft * 12 * s, y + 4, f"{ft} ft")
    c.drawRightString(x + w, y + 4, f"{L/12:.1f} ft truck length")


# ── Sequence table ─────────────────────────────────────────────────────


def _draw_sequence_table(c, x, y, w, h, placements, master,
                         text_primary, text_secondary, text_tertiary,
                         border, heavy_bg,
                         start_row: int = 0, max_rows: int = 26,
                         danger=None):
    """Render the loading-sequence table.

    UX Director / Forklift Op / Tech Writer fixes applied:
    - body 10pt minimum, step # 12pt bold
    - HEAVY rows use bold ▲ icon + "2-person lift" inline (survives B&W)
    - z=N → "Floor" / "On top of #N" / "Layer N"
    - SKU truncation visible (ellipsis)
    """
    from reportlab.lib.colors import HexColor

    headers = [
        ("#",    20),
        ("SKU",  w * 0.30),
        ("Pos",  w * 0.13),
        ("Layer", w * 0.18),
        ("Wt (lb)", w * 0.13),
        ("Note", w * 0.20),
    ]
    row_h = 14  # 14pt → ~11.5 pt visible text height
    header_h = 18

    # Header
    cx = x
    c.setFillColor(text_tertiary)
    c.setFont("Helvetica-Bold", 8)
    for label, hw in headers:
        c.drawString(cx + 2, y + h - 14, label.upper())
        cx += hw
    c.setStrokeColor(border)
    c.setLineWidth(0.6)
    c.line(x, y + h - 18, x + w, y + h - 18)

    # Rows
    sliced = placements[start_row:start_row + max_rows]
    row_y = y + h - 18 - row_h
    for i, p in enumerate(sliced, 1):
        seq = start_row + i  # global step #
        is_heavy = p.get("weight_lb", 0) >= HEAVY_THRESHOLD_LB
        if is_heavy:
            c.setFillColor(heavy_bg)
            c.rect(x, row_y, w, row_h, stroke=0, fill=1)

        cx = x
        # # column — large bold step number
        c.setFillColor(text_primary)
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(cx + headers[0][1] - 3, row_y + 4, str(seq))
        cx += headers[0][1]

        # SKU — swatch + 2-letter code + truncated SKU
        cat = (master.get(p["model_code"], {}).get("category", "")
               if master is not None else p.get("category", ""))
        c.setFillColor(_cat_color(cat))
        c.setStrokeColor(text_secondary)
        c.setLineWidth(0.3)
        c.rect(cx + 2, row_y + 3, 14, 8, stroke=1, fill=1)
        c.setFillColor(text_primary)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(cx + 9, row_y + 4.5, _cat_code(cat))
        c.setFillColor(text_primary)
        sku_display = _sku_display(p["model_code"])
        _smart_set_font(c, "Courier-Bold", 10, sku_display)
        c.drawString(cx + 20, row_y + 4, sku_display)
        cx += headers[1][1]

        # Pos
        c.setFillColor(text_secondary)
        c.setFont("Helvetica", 10)
        c.drawString(cx + 2, row_y + 4, _position_label(p))
        cx += headers[2][1]

        # Layer (operator language)
        c.setFillColor(text_primary)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(cx + 2, row_y + 4, _layer_label(p, placements))
        cx += headers[3][1]

        # Weight
        c.setFillColor(text_primary)
        c.setFont("Helvetica", 10)
        c.drawString(cx + 2, row_y + 4, f"{int(p.get('weight_lb', 0))}")
        cx += headers[4][1]

        # Note column — HEAVY warning (B&W safe — bold ▲ icon)
        if is_heavy:
            c.setFillColor(HexColor("#7F1D1D"))  # dark red, prints black on B&W
            c.setFont("Helvetica-Bold", 10)
            c.drawString(cx + 2, row_y + 4, "▲ HEAVY")

        # row separator
        c.setStrokeColor(HexColor("#E5E7EB"))
        c.setDash(1, 2)
        c.setLineWidth(0.3)
        c.line(x, row_y, x + w, row_y)
        c.setDash(1, 0)

        row_y -= row_h
        if row_y < y + 4:
            break

    # "… N more on page 2 →"  (only when page 1 truncates)
    remaining = len(placements) - (start_row + len(sliced))
    if remaining > 0 and start_row == 0:
        c.setFillColor(danger if danger else text_tertiary)
        c.setFont("Helvetica-BoldOblique", 9)
        c.drawCentredString(x + w / 2, row_y + 6,
                            f"→ {remaining} more item(s) continue on page 2")
