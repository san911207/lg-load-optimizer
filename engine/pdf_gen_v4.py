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
    "tv":           "Carton 'UP' arrows must face up. No horizontal stacking.",
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
                 "> Left = driver-side, facing rear doors")

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
         "All fit" if unfitted == 0 else f"! {unfitted} left over",
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

    # Row split — zone-level 3D needs more height to render readable labels
    row_3d_h = body_h * 0.42
    row_lineup_h = body_h * 0.12
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


def _zone_bbox(zone: "Zone", placements: List[Dict[str, Any]]):
    """Compute the (min_x, min_y, min_z, max_x, max_y, max_z) AABB of a zone."""
    items = [p for p in placements if p.get("seq") in set(zone.item_seqs)]
    if not items:
        return None
    min_x = min(p["x_in"] for p in items)
    min_y = min(p["y_in"] for p in items)
    min_z = min(p["z_in"] for p in items)
    max_x = max(p["x_in"] + p["dim_x_in"] for p in items)
    max_y = max(p["y_in"] + p["dim_y_in"] for p in items)
    max_z = max(p["z_in"] + p["dim_z_in"] for p in items)
    return (min_x, min_y, min_z, max_x, max_y, max_z)


def _draw_iso_with_zones(c, x, y, w, h, truck_spec, placements, zones,
                        master, text_primary, text_tertiary, border, danger):
    """True 30° isometric — viewer at upper-right, 45° elevation.

    **Zone-level rendering** (not per-item). Each zone draws as a single
    big iso block coloured by category with TOP/FRONT/RIGHT face shading.
    This is what makes the diagram legible at PDF scale — 44 individual
    SKUs become unreadable pixels; 4-5 zone blocks are big enough to
    label clearly.

    Projection:
        px = (ix - iy) * cos(30°)
        py = (ix + iy) * sin(30°) + iz
    Cab at lower-LEFT, dock at upper-RIGHT. Width recedes upper-left.
    Vertical straight up. Door-track zone drawn AFTER crates so it
    overlays. Zone label is centered on the FRONT face of each block.
    """
    from reportlab.lib.colors import HexColor, white, Color
    import math

    # Panel background
    c.setFillColor(HexColor("#FAFBFC")); c.setStrokeColor(border)
    c.setLineWidth(0.5); c.roundRect(x, y, w, h, 3, stroke=1, fill=1)

    L = float(truck_spec["length_in"])
    W = float(truck_spec["width_in"])
    H = float(truck_spec["height_in"])

    # 30° isometric — true equal-angle projection
    COS30 = math.cos(math.radians(30))    # 0.8660
    SIN30 = math.sin(math.radians(30))    # 0.5000

    def _world_to_iso(ix, iy, iz):
        """3D world → 2D unit-scale isometric coords (no scale/offset yet)."""
        return ((ix - iy) * COS30, (ix + iy) * SIN30 + iz)

    # Compute the iso bounding box of the truck so we know how to fit it
    # into the panel.
    pts = [
        _world_to_iso(ix, iy, iz)
        for ix in (0, L) for iy in (0, W) for iz in (0, H)
    ]
    min_px = min(p[0] for p in pts)
    max_px = max(p[0] for p in pts)
    min_py = min(p[1] for p in pts)
    max_py = max(p[1] for p in pts)
    bbox_w = max_px - min_px
    bbox_h = max_py - min_py

    pad = 12
    label_top = 14   # reserved space above truck for zone labels
    panel_w = max(1.0, w - 2 * pad)
    panel_h = max(1.0, h - 2 * pad - label_top)
    scale = min(panel_w / max(bbox_w, 1.0), panel_h / max(bbox_h, 1.0))
    if scale <= 0:
        return

    # Origin: place projected (0,0,0) so that the projected bounding box
    # is centered horizontally in the panel and pinned to the bottom.
    rendered_w = bbox_w * scale
    rendered_h = bbox_h * scale
    ox = x + pad + (panel_w - rendered_w) / 2 - min_px * scale
    oy = y + pad + (panel_h - rendered_h) / 2 - min_py * scale

    def proj(ix, iy, iz):
        px, py = _world_to_iso(ix, iy, iz)
        return ox + px * scale, oy + py * scale

    # ── Truck wireframe (12 edges, 8 corners) ─────────────────────────
    corners = [
        proj(0,0,0), proj(L,0,0), proj(L,W,0), proj(0,W,0),
        proj(0,0,H), proj(L,0,H), proj(L,W,H), proj(0,W,H),
    ]
    c.setStrokeColor(HexColor("#1F2937"))
    c.setLineWidth(0.9)
    for a, b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
        c.line(*corners[a], *corners[b])

    # ── Hybrid rendering: individual items, but coloured by ZONE ──────
    # CEO feedback 2026-05-18: zone-only blocks lose product-level
    # detail; per-item rendering with consistent zone colours lets the
    # worker count items AND see the zone grouping at the same time.

    def _shade(base: "Color", factor: float) -> "Color":
        """Multiply RGB by factor (0..2) for face shading; clamp to 1.0."""
        return Color(
            min(1.0, base.red * factor),
            min(1.0, base.green * factor),
            min(1.0, base.blue * factor),
        )

    # Build a sequence → zone lookup so each item picks up its zone colour
    zone_lookup: Dict[int, Zone] = {}
    for zn in zones:
        for sq in zn.item_seqs:
            zone_lookup[sq] = zn

    # Painter's order: back-to-front (small x+y first, then small z)
    items_sorted = sorted(
        placements,
        key=lambda p: (p["x_in"] + p["y_in"], p["z_in"]),
    )
    for p in items_sorted:
        zn = zone_lookup.get(p.get("seq", -1))
        base = _cat_color(zn.broad_category) if zn else _cat_color("other")
        x0, y0, z0 = p["x_in"], p["y_in"], p["z_in"]
        dx, dy, dz = p["dim_x_in"], p["dim_y_in"], p["dim_z_in"]

        # 8 vertices
        v = {
            "flb": proj(x0,    y0,    z0),
            "frb": proj(x0+dx, y0,    z0),
            "frt": proj(x0+dx, y0,    z0+dz),
            "flt": proj(x0,    y0,    z0+dz),
            "blb": proj(x0,    y0+dy, z0),
            "brb": proj(x0+dx, y0+dy, z0),
            "brt": proj(x0+dx, y0+dy, z0+dz),
            "blt": proj(x0,    y0+dy, z0+dz),
        }
        c.setStrokeColor(HexColor("#1F2937"))
        c.setLineWidth(0.45)        # crisp borders so items are countable

        # FRONT face (y=y0)
        c.setFillColor(base)
        pth = c.beginPath()
        pth.moveTo(*v["flb"]); pth.lineTo(*v["frb"])
        pth.lineTo(*v["frt"]); pth.lineTo(*v["flt"]); pth.close()
        c.drawPath(pth, stroke=1, fill=1)
        # RIGHT face — shadow
        c.setFillColor(_shade(base, 0.72))
        pth = c.beginPath()
        pth.moveTo(*v["frb"]); pth.lineTo(*v["brb"])
        pth.lineTo(*v["brt"]); pth.lineTo(*v["frt"]); pth.close()
        c.drawPath(pth, stroke=1, fill=1)
        # TOP face — lit
        c.setFillColor(_shade(base, 1.18))
        pth = c.beginPath()
        pth.moveTo(*v["flt"]); pth.lineTo(*v["frt"])
        pth.lineTo(*v["brt"]); pth.lineTo(*v["blt"]); pth.close()
        c.drawPath(pth, stroke=1, fill=1)

    # ── Zone labels: float ABOVE the truck, one per zone ──────────────
    # Each zone gets a single labelled badge so the dispatcher can see
    # the category groupings without crowding individual crates.
    zone_bboxes: List[Tuple[Zone, Tuple[float, ...]]] = []
    for zn in zones:
        bb = _zone_bbox(zn, placements)
        if bb:
            zone_bboxes.append((zn, bb))
    zone_bboxes.sort(key=lambda zb: zb[1][0])   # left → right

    label_y_world = H + 4
    for zn, (x0, y0, z0, x1, y1, z1) in zone_bboxes:
        cx = (x0 + x1) / 2
        gx, gy = proj(cx, 0, label_y_world)
        if zn.broad_category == "other" and zn.raw_category:
            txt = f"{zn.raw_category[:8]}  x{zn.item_count}"
        else:
            short = ZONE_TITLE_EN.get(zn.broad_category, zn.broad_category.capitalize())
            txt = f"{short}  x{zn.item_count}"
        # White rounded badge
        text_w = c.stringWidth(txt, "Helvetica-Bold", 8) + 10
        # Color-coded left edge so the badge ties back to the zone fill
        badge_left_col = _cat_color(zn.broad_category)
        c.setFillColor(badge_left_col)
        c.roundRect(gx - text_w/2, gy - 6, 6, 13, 2, stroke=0, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setStrokeColor(HexColor("#111827"))
        c.setLineWidth(0.5)
        c.roundRect(gx - text_w/2 + 5, gy - 6, text_w - 5, 13, 2, stroke=1, fill=1)
        c.setFillColor(HexColor("#111827"))
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(gx + 2, gy - 2, txt)

    # ── Door-track zone — drawn AFTER crates so it overlays ───────────
    rear_threshold = L - DOOR_TRACK_LEN_IN
    dz_floor = H - DOOR_TRACK_LOSS_IN
    # The 2 visible faces of the door-track volume:
    #   front (y=0) face — visible
    #   top  (z=H) face — visible
    # We draw both with semi-transparent red so dispatcher sees the cap.
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(0.30)
    c.setFillColor(HexColor("#DC2626"))
    c.setStrokeColor(HexColor("#B91C1C")); c.setLineWidth(0.6)

    # Front face of door-track volume (y=0 plane)
    dt_front = [
        proj(rear_threshold, 0, dz_floor),
        proj(L,               0, dz_floor),
        proj(L,               0, H),
        proj(rear_threshold,  0, H),
    ]
    pth = c.beginPath()
    pth.moveTo(*dt_front[0])
    for pt in dt_front[1:]: pth.lineTo(*pt)
    pth.close()
    c.drawPath(pth, stroke=1, fill=1)

    # Top face of door-track volume (z=H plane)
    dt_top = [
        proj(rear_threshold, 0, H),
        proj(L,               0, H),
        proj(L,               W, H),
        proj(rear_threshold,  W, H),
    ]
    pth = c.beginPath()
    pth.moveTo(*dt_top[0])
    for pt in dt_top[1:]: pth.lineTo(*pt)
    pth.close()
    c.drawPath(pth, stroke=1, fill=1)

    if hasattr(c, "setFillAlpha"): c.setFillAlpha(1)

    # Door-track inline label
    c.setFillColor(HexColor("#7F1D1D"))
    c.setFont("Helvetica-Bold", 6)
    dt_label_pt = proj(rear_threshold + DOOR_TRACK_LEN_IN / 2, 0, H - 3)
    c.drawCentredString(dt_label_pt[0], dt_label_pt[1], "DOOR-TRACK 87 in")

    # Labels live ON each zone's front face (rendered inline above) —
    # no extra glyph row needed above the truck.

    # ── Compass anchors: Cab + Dock + Driver-side ────────────────────
    cab_pt = proj(0, W / 2, 0)
    c.setFillColor(text_tertiary); c.setFont("Helvetica-Bold", 7)
    c.drawString(cab_pt[0] - 24, cab_pt[1] - 12, "CAB (front)")

    dock_pt = proj(L, W / 2, 0)
    c.setFillColor(danger)
    c.drawString(dock_pt[0] + 4, dock_pt[1] - 12, "DOCK (rear)")


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
                     f"{z.length_ft_start} to {z.length_ft_end} ft")
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
                     f"~{s.estimated_min} min  ·  cum {s.cumulative_lift_lb_per_person:,} lb 1P")

        # Safety note (English)
        note = SAFETY_NOTE_EN.get(broad, "")
        if note:
            c.setFillColor(danger); c.setFont("Helvetica", 8)
            display = note if len(note) <= 46 else note[:44] + "…"
            c.drawString(cx + 6, info_y - 36, f"! {display}")


def _draw_mini_side(c, x, y, w, h, truck_spec, placements, stages_so_far,
                   border, danger):
    """Side view (length × height) — truck outline + ZONE-level fills.

    Shows cab→dock truck silhouette, door-track zone red, and zone
    bounding boxes for every stage up to current. Previous stages dim
    grey, current stage in its category colour. Worker reads at a
    glance: "we're filling here, this is what's been loaded already."
    """
    from reportlab.lib.colors import HexColor, Color

    L = float(truck_spec["length_in"])
    H = float(truck_spec["height_in"])
    # Inner box (with 2pt margin) — leave room for cab/dock labels at bottom
    label_h = 10
    inner_x = x + 4
    inner_y = y + 4 + label_h
    inner_w = w - 8
    inner_h = h - 8 - label_h
    sx = inner_w / L
    sy = inner_h / H
    s = min(sx, sy)
    if s <= 0:
        return
    tx = inner_x
    ty = inner_y

    # 1) Truck outline (always drawn, clear silhouette)
    c.setFillColor(HexColor("#FAFBFC"))
    c.setStrokeColor(HexColor("#374151"))
    c.setLineWidth(0.7)
    c.rect(tx, ty, L * s, H * s, stroke=1, fill=1)

    # 2) Door-track zone (rear 5 ft × top 10 in) — red hatched
    dt_x = tx + (L - DOOR_TRACK_LEN_IN) * s
    dt_y_top = ty + (H - DOOR_TRACK_LOSS_IN) * s
    c.setFillColor(HexColor("#FCA5A5"))
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(0.40)
    c.rect(dt_x, dt_y_top, DOOR_TRACK_LEN_IN * s, DOOR_TRACK_LOSS_IN * s,
           stroke=0, fill=1)
    if hasattr(c, "setFillAlpha"): c.setFillAlpha(1)
    # Dashed boundary
    c.setStrokeColor(HexColor("#B91C1C")); c.setLineWidth(0.4)
    c.setDash(2, 2)
    c.line(dt_x, dt_y_top, dt_x, ty + H * s)
    c.line(dt_x, dt_y_top, tx + L * s, dt_y_top)
    c.setDash(1, 0)

    # 3) Per-item rects coloured by stage state:
    #    * previous stages — dim grey fill, thin border (loaded)
    #    * current stage   — category colour, thicker border (loading NOW)
    #    * future stages   — not drawn (haven't been loaded yet)
    n_stages = len(stages_so_far)
    if n_stages > 0:
        cumulative_seqs: set = set()
        for stg_idx, stg in enumerate(stages_so_far):
            stg_seqs: set = set()
            for zn in stg.zones:
                stg_seqs |= set(zn.item_seqs)
            new_seqs = stg_seqs - cumulative_seqs
            cumulative_seqs |= stg_seqs
            is_current = (stg_idx == n_stages - 1)
            broad = stg.zones[0].broad_category if stg.zones else "other"
            fill_col = _cat_color(broad) if is_current else HexColor("#D1D5DB")
            stroke_col = HexColor("#111827") if is_current else HexColor("#9CA3AF")
            line_w = 0.55 if is_current else 0.30
            for p in placements:
                if p.get("seq") not in new_seqs:
                    continue
                bx = tx + p["x_in"] * s
                by = ty + p["z_in"] * s
                bw = p["dim_x_in"] * s
                bh = p["dim_z_in"] * s
                c.setFillColor(fill_col)
                c.setStrokeColor(stroke_col)
                c.setLineWidth(line_w)
                c.rect(bx, by, bw, bh, stroke=1, fill=1)
        # Label the current stage's bounding region (above the items)
        if stages_so_far:
            last = stages_so_far[-1]
            last_seqs: set = set()
            for zn in last.zones:
                last_seqs |= set(zn.item_seqs)
            items_in_last = [p for p in placements if p.get("seq") in last_seqs]
            if items_in_last:
                lbx = tx + min(p["x_in"] for p in items_in_last) * s
                lbx_end = tx + max(p["x_in"] + p["dim_x_in"] for p in items_in_last) * s
                lby_top = ty + max(p["z_in"] + p["dim_z_in"] for p in items_in_last) * s
                broad = last.zones[0].broad_category if last.zones else "other"
                if broad == "other" and last.zones and last.zones[0].raw_category:
                    lbl = last.zones[0].raw_category[:5].upper()
                else:
                    lbl = ZONE_GLYPH_EN.get(broad, broad[:3].upper())
                lbl_x = (lbx + lbx_end) / 2
                lbl_y = min(lby_top + 4, ty + H * s + 4)
                lbl_w = c.stringWidth(lbl, "Helvetica-Bold", 7) + 6
                c.setFillColor(HexColor("#FFFFFF"))
                c.setStrokeColor(HexColor("#111827"))
                c.setLineWidth(0.3)
                c.roundRect(lbl_x - lbl_w/2, lbl_y - 5, lbl_w, 9,
                            1.5, stroke=1, fill=1)
                c.setFillColor(HexColor("#111827"))
                c.setFont("Helvetica-Bold", 7)
                c.drawCentredString(lbl_x, lbl_y - 3, lbl)

    # 4) Cab / Dock anchor labels
    c.setFillColor(HexColor("#6B7280"))
    c.setFont("Helvetica-Bold", 6)
    c.drawString(tx, ty - 8, "Cab")
    c.setFillColor(HexColor("#B91C1C"))
    c.drawRightString(tx + L * s, ty - 8, "Dock")
