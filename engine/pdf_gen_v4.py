"""
PDF v4 — work-order, final design (2026-05-18 CEO mockup approved).

Layout (single US Letter landscape, 1 page):

  ┌─ HEADER ─────────────────────────────────────────────────────────┐
  │ L · LG Load Optimizer · Work Order Load <id>                     │
  │ BOL · Carrier · Dock · Appt · Route · Truck · Driver             │
  │ ↳ Left = driver-side facing rear doors  (red anchor)             │
  ├──────────────────────────────────────────────────────────────────┤
  │ KPI 5 — Items / Length / Weight / Volume / Heavy on floor        │
  ├──────────────────────────────────────────────────────────────────┤
  │ A 3D Isometric (zone-coloured, REF/W+D/DW/OV)  │ B Zone breakdown │
  ├──────────────────────────────────────────────────────────────────┤
  │ C Dock Lineup — Wave 1 / Wave 2                                  │
  ├──────────────────────────────────────────────────────────────────┤
  │ D 5-Stage loading — side view (progressively filled)             │
  │   ① Refrigerator      [2P]  · ⏱ 12 min · cum 0 lb  · ⚠ note      │
  │   ② Washer (floor)    [2P]  · ⏱ 12 min · cum 0 lb  · ⚠ note      │
  │   ③ Dryer (top stack) [2P]  · ⏱ 8 min  · cum 0 lb  · ⚠ note      │
  │   ④ Dishwasher        [1P]  · ⏱ 8 min  · cum 1,210 lb · ⚠ note   │
  │   ⑤ Wall Oven + close [1P]  · ⏱ 6 min  · cum 1,958 lb · ⚠ note   │
  ├──────────────────────────────────────────────────────────────────┤
  │ Footer · Engine · Load # · Page 1 of 1                           │
  └──────────────────────────────────────────────────────────────────┘

Removed vs earlier drafts (CEO decisions 2026-05-18):
  - Pre-load safety panel ("C 사전 안전")
  - Axle balance KPI cell (DOT compliance — defer to v2.2)
  - Secure & Inspect / Damage Log / Shift Handoff close-out row
  - Korean labels (English only)
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from engine.zone_aggregator import (
    Stage,
    Zone,
    aggregate_zones,
    stages_from_zones,
)


CAT_COLORS: Dict[str, str] = {
    "refrigerator":       "#85B7EB",
    "washer":             "#ED93B1",
    "washer_dryer_pair":  "#E89BB0",
    "dryer":              "#F4C0D1",
    "dishwasher":         "#AFA9EC",
    "microwave":          "#FBBF24",
    "oven":               "#F4A07A",
    "tv":                 "#60A5FA",
    "monitor":            "#22D3EE",
    "av":                 "#34D399",
    "other":              "#D1D5DB",
}

# English short tags used as on-canvas zone glyphs (B&W safe).
ZONE_GLYPH_EN: Dict[str, str] = {
    "refrigerator":      "REF",
    "washer":            "WAS",
    "washer_dryer_pair": "W+D",
    "dryer":             "DRY",
    "dishwasher":        "DW",
    "microwave":         "MW",
    "oven":              "OV",
    "tv":                "TV",
    "monitor":           "MN",
    "av":                "AV",
    "other":             "??",
}

# English zone titles (replaces the Korean labels used in v3 drafts).
ZONE_TITLE_EN: Dict[str, str] = {
    "refrigerator":      "Refrigerator",
    "washer":            "Washer",
    "washer_dryer_pair": "Washer + Dryer (paired)",
    "dryer":             "Dryer",
    "dishwasher":        "Dishwasher",
    "microwave":         "Microwave",
    "oven":              "Wall Oven",
    "tv":                "TV",
    "monitor":           "Monitor",
    "av":                "Audio",
    "other":             "Other",
}

HEAVY_THRESHOLD_LB = 150.0
DOOR_TRACK_LEN_IN = 60.0
DOOR_TRACK_LOSS_IN = 10.0


def _hex(c: str):
    from reportlab.lib.colors import Color
    c = c.lstrip("#")
    return Color(int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255)


def _cat_color(broad: str):
    return _hex(CAT_COLORS.get(broad, "#9CA3AF"))


# ── Stage-title English overrides for the 5 cards ──────────────────────


def _stage_title_en(stage: Stage) -> str:
    """Translate the (possibly Korean) stage.title_kr into the canonical
    English label used on the work-order cards.

    Falls back to the *raw* Division name when broad_category resolved
    to "other" — gives the dispatcher a clue about what wasn't matched
    instead of an opaque "Other".
    """
    if not stage.zones:
        return stage.title_en or "Stage"
    z = stage.zones[0]
    broad = z.broad_category
    if broad == "other" and z.raw_category:
        return z.raw_category[:24]
    base = ZONE_TITLE_EN.get(broad, broad.capitalize())
    if broad == "washer_dryer_pair":
        if "위" in stage.title_kr or "top" in stage.title_kr.lower() or "tier 2" in stage.layout:
            return "Dryer (top stack)"
        return "Washer (floor)"
    if broad == "washer" and ("바닥" in stage.title_kr or "floor" in stage.title_kr.lower()):
        return "Washer (floor)"
    if broad == "dryer":
        return "Dryer (top stack)"
    if broad == "oven":
        return "Wall Oven + close-out"
    return base


SAFETY_NOTE_EN: Dict[str, str] = {
    "refrigerator": "Strap to cab wall first. Hand-truck required.",
    "washer":       "Verify 4 transit bolts engaged.",
    "dryer":        "Align precisely on washer. 2-person lift.",
    "dishwasher":   "Hose side up. Prevent residual water leak.",
    "microwave":    "Protect glass-door corners. Light item.",
    "oven":         "Verify door lock. No items on glass-top.",
    "tv":           "Carton ↑ arrows must face up. No horizontal stacking.",
    "monitor":      "Top-of-package impact-sensitive.",
}


def generate_work_order_v4(
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
    build_version: str = "v2.1.0",
) -> bytes:
    """Render the v4 work order PDF (final CEO-approved layout)."""
    try:
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.lib.colors import HexColor, white
        from reportlab.pdfgen import canvas
    except ImportError:
        return b"%PDF-1.4\n% reportlab not installed\n"

    when = when or datetime.now(timezone.utc)
    placements = result.get("placements", [])
    metrics = result.get("metrics", {})
    pair_count = result.get("pair_count", 0)

    zones = aggregate_zones(placements, master or {}, pair_count=pair_count)
    stages = stages_from_zones(zones)[:5]   # at most 5 cards

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
    gold = HexColor("#92400E")
    gold_bg = HexColor("#FEF3C7")
    success = HexColor("#065F46")
    success_bg = HexColor("#ECFDF5")
    danger = HexColor("#991B1B")
    danger_bg = HexColor("#FEE2E2")
    lg_red = HexColor("#A50034")

    margin = 20
    header_h = 56
    kpi_h = 44
    foot_h = 16
    gap = 6

    # ── HEADER ─────────────────────────────────────────────────────────
    y_top = page_h - margin
    c.setStrokeColor(text_primary); c.setLineWidth(1.5)
    c.line(margin, y_top - header_h, page_w - margin, y_top - header_h)

    c.setFillColor(lg_red)
    c.roundRect(margin, y_top - 24, 24, 24, 4, stroke=0, fill=1)
    c.setFillColor(white); c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(margin + 12, y_top - 18, "L")

    c.setFillColor(text_primary)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin + 32, y_top - 14, "LG Load Optimizer")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin + 32, y_top - 32, f"Work Order  ·  Load {load_id}")

    right_x = page_w - margin
    c.setFont("Helvetica-Bold", 9); c.setFillColor(text_secondary)
    c.drawRightString(right_x, y_top - 12,
                      f"BOL {bol}  ·  Carrier {carrier}  ·  Dock {dock}")
    c.drawRightString(right_x, y_top - 24,
                      f"Appt {appointment}  ·  Route {route_id}  ·  Truck {truck_label}")
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    c.setFont("Helvetica", 8); c.setFillColor(text_tertiary)
    c.drawRightString(right_x, y_top - 36,
                      f"Issued by {creator or 'system'} · {stamp} · {build_version}")

    if driver:
        c.setFillColor(text_secondary); c.setFont("Helvetica-Bold", 9)
        c.drawString(margin + 32, y_top - 50, f"Driver: {driver}")
    c.setFillColor(danger); c.setFont("Helvetica-Bold", 8)
    c.drawString(page_w - margin - 230, y_top - 50,
                 "↳ Left = driver-side, facing rear doors")

    # ── KPI STRIP (5 cells) ────────────────────────────────────────────
    kpi_y = y_top - header_h - gap - kpi_h
    cell_w = (page_w - 2 * margin - 4 * 5) / 5
    fits_ct = result.get("fitted_count", 0)
    requested = result.get("requested_count", 0)
    unfitted = result.get("unfitted_count", max(0, requested - fits_ct))
    heavy_ct = sum(1 for p in placements if p.get("weight_lb", 0) >= HEAVY_THRESHOLD_LB)
    is_optimal = result.get("is_provable_optimal", False)

    cells = [
        ("Items",
         f"{fits_ct} / {requested}",
         "All fit ✓" if unfitted == 0 else f"⚠ {unfitted} left over",
         "success" if unfitted == 0 else "danger"),
        ("Length",
         f"{metrics.get('x_used_ft', 0):g} ft",
         "Proven shortest" if is_optimal else "Space-optimized",
         "gold" if is_optimal else "gold"),
        ("Weight",
         f"{int(metrics.get('weight_total_lb', 0)):,} lb",
         f"{metrics.get('weight_util_pct', 0):g}% util",
         "neutral"),
        ("Volume",
         f"{metrics.get('volume_loaded_cft', 0):g} ft³",
         f"{metrics.get('volume_util_pct', 0):g}% util",
         "neutral"),
        ("Heavy on floor",
         f"{heavy_ct}",
         "z=0 verified",
         "neutral"),
    ]
    for i, (lbl, val, sub, kind) in enumerate(cells):
        cx = margin + i * (cell_w + 5)
        if kind == "gold":
            bg, fg = gold_bg, gold
        elif kind == "danger":
            bg, fg = danger_bg, danger
        elif kind == "success":
            bg, fg = success_bg, success
        else:
            bg, fg = white, text_tertiary
        c.setFillColor(bg); c.setStrokeColor(border); c.setLineWidth(0.6)
        c.roundRect(cx, kpi_y, cell_w, kpi_h, 4, stroke=1, fill=1)
        c.setFillColor(fg); c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(cx + cell_w/2, kpi_y + kpi_h - 11, lbl.upper())
        c.setFillColor(fg if kind in {"gold", "danger", "success"} else text_primary)
        c.setFont("Helvetica-Bold", 17)
        c.drawCentredString(cx + cell_w/2, kpi_y + kpi_h - 29, val)
        c.setFillColor(text_tertiary if kind == "neutral" else fg)
        c.setFont("Helvetica", 8)
        c.drawCentredString(cx + cell_w/2, kpi_y + 6, sub)

    # ── Body geometry ──────────────────────────────────────────────────
    body_top = kpi_y - gap
    body_bottom = margin + foot_h + gap
    body_h = body_top - body_bottom

    # Row split (no pre-load / no close-out):
    row_3d_h = body_h * 0.34
    row_lineup_h = body_h * 0.14
    row_stage_h = body_h - row_3d_h - row_lineup_h - 2 * gap

    # ── Row 1: 3D + Zone breakdown ─────────────────────────────────────
    row_3d_top = body_top
    row_3d_bottom = row_3d_top - row_3d_h
    iso_w = (page_w - 2 * margin) * 0.52
    table_x = margin + iso_w + gap
    table_w = page_w - margin - table_x

    _draw_panel(c, margin, row_3d_bottom, iso_w, row_3d_h, border)
    _draw_panel_header(c, margin + 6, row_3d_top - 12, "A",
                       "Isometric — rows × lanes × tiers", text_tertiary)
    _draw_iso_with_zones(c, margin + 6, row_3d_bottom + 6,
                         iso_w - 12, row_3d_h - 22,
                         truck_spec, placements, zones, master or {},
                         text_primary, text_tertiary, border, danger)

    _draw_panel(c, table_x, row_3d_bottom, table_w, row_3d_h, border)
    _draw_panel_header(c, table_x + 6, row_3d_top - 12, "B",
                       "Zone breakdown", text_tertiary)
    _draw_zone_table(c, table_x + 6, row_3d_bottom + 6,
                     table_w - 12, row_3d_h - 22, zones,
                     text_primary, text_secondary, text_tertiary, border)

    # ── Row 2: Dock lineup (full width) ────────────────────────────────
    row_line_top = row_3d_bottom - gap
    row_line_bottom = row_line_top - row_lineup_h
    _draw_panel(c, margin, row_line_bottom, page_w - 2 * margin,
                row_lineup_h, border)
    _draw_panel_header(c, margin + 6, row_line_top - 12, "C",
                       "Dock Lineup — wave split", text_tertiary)
    _draw_lineup(c, margin + 6, row_line_bottom + 4,
                 page_w - 2 * margin - 12, row_lineup_h - 16,
                 stages, text_primary, text_secondary, text_tertiary)

    # ── Row 3: 5 Stage cards ───────────────────────────────────────────
    row_stg_top = row_line_bottom - gap
    row_stg_bottom = row_stg_top - row_stage_h
    _draw_stage_cards(c, margin, row_stg_bottom,
                      page_w - 2 * margin, row_stage_h, stages,
                      truck_spec, placements,
                      text_primary, text_secondary, text_tertiary,
                      border, danger, gold, gold_bg, success_bg)

    # ── FOOTER ─────────────────────────────────────────────────────────
    foot_y = margin
    c.setStrokeColor(border); c.setLineWidth(0.4)
    c.line(margin, foot_y + foot_h - 2, page_w - margin, foot_y + foot_h - 2)
    c.setFillColor(text_tertiary); c.setFont("Helvetica", 8)
    c.drawString(margin, foot_y + 4,
                 f"Engine: {result.get('engine','heuristic')} · "
                 f"solved {result.get('solve_time_s', 0):.1f}s · "
                 f"demoted {result.get('demoted_items', 0)} item(s)")
    c.drawRightString(page_w - margin, foot_y + 4,
                      f"Load {load_id} · Page 1 of 1")

    c.showPage()
    c.save()
    return buf.getvalue()


# ── Panel helpers ──────────────────────────────────────────────────────


def _draw_panel(c, x, y, w, h, border):
    from reportlab.lib.colors import white
    c.setStrokeColor(border); c.setFillColor(white); c.setLineWidth(0.5)
    c.roundRect(x, y, w, h, 5, stroke=1, fill=1)


def _draw_panel_header(c, x, y, num, title, text_tertiary):
    from reportlab.lib.colors import HexColor, white
    c.setFillColor(HexColor("#111827")); c.circle(x + 7, y + 4, 7, stroke=0, fill=1)
    c.setFillColor(white); c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(x + 7, y + 2, num)
    c.setFillColor(text_tertiary); c.setFont("Helvetica-Bold", 9)
    c.drawString(x + 18, y + 2, title.upper())


# ── 3D Iso with zone glyphs ────────────────────────────────────────────


def _draw_iso_with_zones(c, x, y, w, h, truck_spec, placements, zones,
                        master, text_primary, text_tertiary, border, danger):
    from reportlab.lib.colors import HexColor, white

    c.setFillColor(HexColor("#FAFBFC")); c.setStrokeColor(border)
    c.setLineWidth(0.5); c.roundRect(x, y, w, h, 3, stroke=1, fill=1)

    L = truck_spec["length_in"]; W = truck_spec["width_in"]; H = truck_spec["height_in"]
    pad = 10
    iso_ax = 0.45; iso_ay = 0.32
    sx = (w - 2 * pad) / (L + W * iso_ax)
    sz = (h - 2 * pad) / (H + W * iso_ay)
    s = min(sx, sz)
    if s <= 0:
        return
    ty = W * iso_ay * s
    ox = x + pad; oy = y + pad + ty

    def proj(ix, iy, iz):
        return ox + ix * s + iy * iso_ax * s, oy + iz * s - iy * iso_ay * s

    corners = [proj(0,0,0), proj(L,0,0), proj(L,W,0), proj(0,W,0),
               proj(0,0,H), proj(L,0,H), proj(L,W,H), proj(0,W,H)]
    c.setStrokeColor(HexColor("#1F2937")); c.setLineWidth(0.8)
    for a, b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
        c.line(*corners[a], *corners[b])

    rear_threshold = L - DOOR_TRACK_LEN_IN
    dz_floor = H - DOOR_TRACK_LOSS_IN
    p1 = proj(rear_threshold, 0, dz_floor); p2 = proj(L, 0, dz_floor)
    p3 = proj(L, 0, H);                     p4 = proj(rear_threshold, 0, H)
    c.setFillColor(HexColor("#FCA5A5"))
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(0.35)
    path = c.beginPath(); path.moveTo(*p1); path.lineTo(*p2); path.lineTo(*p3); path.lineTo(*p4); path.close()
    c.drawPath(path, stroke=0, fill=1)
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(1)

    zone_lookup: Dict[int, Zone] = {}
    for z in zones:
        for sq in z.item_seqs:
            zone_lookup[sq] = z

    items = sorted(placements, key=lambda p: (p["y_in"], -p["x_in"], p["z_in"]))
    for p in items:
        z = zone_lookup.get(p.get("seq", -1))
        color = _cat_color(z.broad_category) if z else _cat_color("other")
        x0, y0, z0 = p["x_in"], p["y_in"], p["z_in"]
        dx, dy, dz = p["dim_x_in"], p["dim_y_in"], p["dim_z_in"]
        a = proj(x0, y0, z0); b = proj(x0+dx, y0, z0)
        d = proj(x0+dx, y0, z0+dz); e = proj(x0, y0, z0+dz)
        c.setFillColor(color); c.setStrokeColor(HexColor("#1F2937")); c.setLineWidth(0.3)
        path = c.beginPath(); path.moveTo(*a); path.lineTo(*b); path.lineTo(*d); path.lineTo(*e); path.close()
        c.drawPath(path, stroke=1, fill=1)

    # English glyph labels above each zone — use raw category for "other"
    # so the dispatcher sees the unmatched Division name instead of "??".
    for z in zones:
        if not z.item_seqs:
            continue
        zps = [p for p in placements if p.get("seq") in set(z.item_seqs)]
        if not zps:
            continue
        cx = sum(p["x_in"] + p["dim_x_in"]/2 for p in zps) / len(zps)
        cy = sum(p["y_in"] + p["dim_y_in"]/2 for p in zps) / len(zps)
        cz_top = max(p["z_in"] + p["dim_z_in"] for p in zps)
        gx, gy = proj(cx, cy, cz_top + 3)
        if z.broad_category == "other" and z.raw_category:
            glyph = z.raw_category[:4].upper()
        else:
            glyph = ZONE_GLYPH_EN.get(z.broad_category, z.broad_category[:3].upper())
        c.setFillColor(HexColor("#111827"))
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(gx, gy, glyph)

    c.setFillColor(text_tertiary); c.setFont("Helvetica-Bold", 7)
    c.drawString(corners[0][0] + 2, corners[0][1] - 7, "← Cab (front)")
    c.setFillColor(danger)
    c.drawRightString(corners[2][0], corners[2][1] - 7, "Dock (rear) →")


# ── Zone breakdown table (English) ─────────────────────────────────────


def _draw_zone_table(c, x, y, w, h, zones, text_primary, text_secondary,
                    text_tertiary, border):
    """Draw the Zone breakdown table.

    Column widths re-tuned 2026-05-18 after production overflow: the
    Layout column was 27% which fits ~14 chars at Courier 9, but rows
    like "8 rows × 6 lanes × 5 tiers" need 26 chars → text bled into
    the Length column. Now Layout uses a compact "8R × 6L × 5T" form
    and gets 22% width; the freed budget goes to Length/Weight.
    """
    from reportlab.lib.colors import HexColor
    headers = [
        ("Zone · Model",          w * 0.40),
        ("Qty",                   w * 0.08),
        ("Layout (R × L × T)",    w * 0.18),
        ("Length",                w * 0.18),
        ("Wt",                    w * 0.16),
    ]
    rows = max(len(zones), 4)
    row_h = max(13, (h - 18) / max(rows + 1, 5))
    if row_h > 18: row_h = 18

    cx = x
    c.setFillColor(text_tertiary); c.setFont("Helvetica-Bold", 7)
    for label, hw in headers:
        c.drawString(cx + 2, y + h - 12, label.upper())
        cx += hw
    c.setStrokeColor(border); c.setLineWidth(0.4)
    c.line(x, y + h - 16, x + w, y + h - 16)

    row_y = y + h - 16 - row_h
    for z in zones:
        cx = x
        c.setFillColor(_cat_color(z.broad_category))
        c.rect(cx + 2, row_y + 4, 8, 8, stroke=0, fill=1)
        c.setFillColor(text_primary); c.setFont("Helvetica-Bold", 9)
        c.drawString(cx + 14, row_y + 5, f"{z.zone_id} ·")
        c.setFont("Helvetica", 9)
        # Use raw division name when broad_category fell to "other" so
        # the dispatcher sees the actual ERP value instead of "Other".
        if z.broad_category == "other" and z.raw_category:
            zone_title = z.raw_category[:18]
        else:
            zone_title = ZONE_TITLE_EN.get(z.broad_category, z.broad_category)
        c.drawString(cx + 28, row_y + 5, zone_title)
        cx += headers[0][1]

        # Qty
        c.setFont("Helvetica", 10); c.setFillColor(text_primary)
        if z.is_pair:
            half = z.item_count // 2
            other = z.item_count - half
            c.drawString(cx + 2, row_y + 5, f"{half} + {other}")
        else:
            c.drawString(cx + 2, row_y + 5, str(z.item_count))
        cx += headers[1][1]

        # Layout — compact form "8R × 6L × 5T" so it fits the column
        c.setFont("Courier", 9); c.setFillColor(text_secondary)
        layout_compact = f"{z.rows}R × {z.lanes}L × {z.tiers}T"
        c.drawString(cx + 2, row_y + 5, layout_compact)
        cx += headers[2][1]

        # Length
        c.setFont("Helvetica", 10); c.setFillColor(text_primary)
        c.drawString(cx + 2, row_y + 5,
                     f"{z.length_ft_start} → {z.length_ft_end} ft")
        cx += headers[3][1]

        # Weight (right-aligned)
        c.drawRightString(cx + headers[4][1] - 4, row_y + 5,
                          f"{z.weight_lb:,} lb")
        c.setStrokeColor(HexColor("#E5E7EB")); c.setDash(1, 2); c.setLineWidth(0.3)
        c.line(x, row_y, x + w, row_y); c.setDash(1, 0)
        row_y -= row_h
        if row_y < y + 4:
            break


# ── Dock lineup (Wave 1 / Wave 2) ──────────────────────────────────────


def _draw_lineup(c, x, y, w, h, stages, text_primary, text_secondary,
                text_tertiary):
    from reportlab.lib.colors import HexColor
    mid = max(1, len(stages) // 2)
    waves = [("Wave 1 (lanes A · B · C simultaneous)", stages[:mid]),
             ("Wave 2 (start when Wave 1 half-cleared)", stages[mid:])]
    col_w = w / 2 if len(waves) == 2 else w
    for wi, (wname, ws) in enumerate(waves):
        cx = x + wi * col_w
        c.setFillColor(text_primary); c.setFont("Helvetica-Bold", 9.5)
        c.drawString(cx, y + h - 12, wname)
        for i, s in enumerate(ws):
            c.setFillColor(text_secondary); c.setFont("Helvetica", 9)
            title = _stage_title_en(s)
            c.drawString(cx, y + h - 26 - i * 11,
                         f"{s.step_no}. {title} × {s.units}")


# ── 5 Stage cards ──────────────────────────────────────────────────────


def _draw_stage_cards(c, x, y, w, h, stages, truck_spec, placements,
                     text_primary, text_secondary, text_tertiary,
                     border, danger, gold, gold_bg, success_bg):
    from reportlab.lib.colors import HexColor, white
    n = max(1, len(stages))
    card_w = (w - (n - 1) * 5) / n if n > 0 else w

    c.setFillColor(text_tertiary); c.setFont("Helvetica-Bold", 9)
    c.drawString(x, y + h - 4, "D · 5-STAGE LOADING — side view (progressively filled)")

    title_h = 14
    for i, s in enumerate(stages):
        cx = x + i * (card_w + 5)
        card_h = h - title_h
        cy = y
        _draw_panel(c, cx, cy, card_w, card_h, border)

        broad = s.zones[0].broad_category if s.zones else "other"

        # Step number badge
        c.setFillColor(_cat_color(broad))
        c.circle(cx + 12, cy + card_h - 18, 9, stroke=0, fill=1)
        c.setFillColor(white); c.setFont("Helvetica-Bold", 11)
        c.drawCentredString(cx + 12, cy + card_h - 21, str(s.step_no))

        # Title (English) + crew chip
        title = _stage_title_en(s)
        c.setFillColor(text_primary); c.setFont("Helvetica-Bold", 11)
        c.drawString(cx + 26, cy + card_h - 18, title)
        crew_label = f"{s.crew}P"
        chip_bg = gold_bg if s.crew == 2 else success_bg
        chip_fg = gold if s.crew == 2 else HexColor("#065F46")
        c.setFillColor(chip_bg)
        c.roundRect(cx + card_w - 32, cy + card_h - 24, 26, 13, 3, stroke=0, fill=1)
        c.setFillColor(chip_fg); c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(cx + card_w - 19, cy + card_h - 15, crew_label)

        # Mini side view (taller now)
        mini_h = card_h * 0.46
        mini_y = cy + card_h - 30 - mini_h
        _draw_mini_side(c, cx + 5, mini_y, card_w - 10, mini_h,
                        truck_spec, placements, stages[:i + 1],
                        border, danger)

        # Stats block
        info_y = mini_y - 12
        c.setFillColor(text_secondary); c.setFont("Helvetica", 9)
        c.drawString(cx + 6, info_y, f"{s.units} units · {s.unit_weight_lb} lb each")
        c.drawString(cx + 6, info_y - 11, s.layout)
        c.setFont("Helvetica-Bold", 9); c.setFillColor(text_primary)
        c.drawString(cx + 6, info_y - 23,
                     f"⏱ {s.estimated_min} min · cum {s.cumulative_lift_lb_per_person:,} lb 1P")

        # Safety note (English)
        note = SAFETY_NOTE_EN.get(broad, "")
        if note:
            c.setFillColor(danger); c.setFont("Helvetica", 8)
            display = note if len(note) <= 46 else note[:44] + "…"
            c.drawString(cx + 6, info_y - 36, f"⚠ {display}")


def _draw_mini_side(c, x, y, w, h, truck_spec, placements, stages_so_far,
                   border, danger):
    from reportlab.lib.colors import HexColor

    c.setFillColor(HexColor("#FAFBFC")); c.setStrokeColor(border); c.setLineWidth(0.4)
    c.rect(x, y, w, h, stroke=1, fill=1)
    L = truck_spec["length_in"]; H = truck_spec["height_in"]
    sx = (w - 4) / L; sy = (h - 4) / H
    s = min(sx, sy)
    if s <= 0:
        return

    dt_x = x + 2 + (L - DOOR_TRACK_LEN_IN) * s
    dt_y = y + 2 + (H - DOOR_TRACK_LOSS_IN) * s
    c.setFillColor(HexColor("#FCA5A5"))
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(0.30)
    c.rect(dt_x, dt_y, DOOR_TRACK_LEN_IN * s, DOOR_TRACK_LOSS_IN * s, stroke=0, fill=1)
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(1)

    seq_in_scope = set()
    for st in stages_so_far:
        for z in st.zones:
            seq_in_scope |= set(z.item_seqs)
    last_zone_seqs = set()
    if stages_so_far:
        for z in stages_so_far[-1].zones:
            last_zone_seqs |= set(z.item_seqs)

    for p in placements:
        if p.get("seq") not in seq_in_scope:
            continue
        bx = x + 2 + p["x_in"] * s
        by = y + 2 + p["z_in"] * s
        bw = p["dim_x_in"] * s
        bh = p["dim_z_in"] * s
        is_current = p.get("seq") in last_zone_seqs
        c.setFillColor(HexColor("#1F2937") if is_current else HexColor("#D1D5DB"))
        c.setStrokeColor(HexColor("#1F2937")); c.setLineWidth(0.2)
        c.rect(bx, by, bw, bh, stroke=1, fill=1)
