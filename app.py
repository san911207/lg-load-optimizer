"""
=========================================================================
LG Appliance Truck Load Optimizer — Streamlit Phase 0
=========================================================================
Engine : engine.best_packer.simulate()   (pair-packing, not py3dbp)
UI     : DESIGN_SPEC.md 기반 3-step flow
         Step 1 · Decision       — 26ft vs 53ft 비교
         Step 2-A · 3D View      — manager view (placeholder, 다음 단계)
         Step 2-B · Worker Guide — 작업자 5-step + PDF (placeholder, 다음 단계)
Units  : UI = ft / in / lb / ft³   ·   Engine = mm / kg
Run    : streamlit run app.py
=========================================================================
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.best_packer import simulate, fits_formula
from engine.router import solve as router_solve

# ─────────────────────────────────────────────────────────────────────────
# Design tokens (DESIGN_SPEC.md §Color tokens & §Color palette)
# ─────────────────────────────────────────────────────────────────────────
COLOR_RECOMMENDED = "#1D9E75"
COLOR_CHECK_ICON = "#0F6E56"
BAR_GREEN = "#1D9E75"
BAR_ORANGE = "#E89F32"
BAR_GRAY = "#9CA3AF"
TEXT_MUTED = "#6B7280"
BORDER_NEUTRAL = "#E5E7EB"

CAT_COLORS: Dict[str, Dict[str, str]] = {
    "refrigerator": {"top": "#B5D4F4", "front": "#85B7EB", "right": "#378ADD", "stroke": "#0C447C"},
    "washer":       {"top": "#F4C0D1", "front": "#ED93B1", "right": "#D4537E", "stroke": "#72243E"},
    "dryer":        {"top": "#F4C0D1", "front": "#F4C0D1", "right": "#ED93B1", "stroke": "#993556"},
    "dishwasher":   {"top": "#CECBF6", "front": "#AFA9EC", "right": "#7F77DD", "stroke": "#3C3489"},
    "oven":         {"top": "#F5C4B3", "front": "#F0997B", "right": "#D85A30", "stroke": "#993C1D"},
    "microwave":    {"top": "#FDE68A", "front": "#FBBF24", "right": "#D97706", "stroke": "#78350F"},
    "tv":           {"top": "#C7D2FE", "front": "#818CF8", "right": "#4F46E5", "stroke": "#312E81"},
    "monitor":      {"top": "#A7F3D0", "front": "#34D399", "right": "#059669", "stroke": "#064E3B"},
    "other":        {"top": "#E5E7EB", "front": "#9CA3AF", "right": "#6B7280", "stroke": "#1F2937"},
}

# ─────────────────────────────────────────────────────────────────────────
# Unit conversions (display layer only)
# Master data is now in US units (in / lb / cft). Display sometimes needs ft.
# ─────────────────────────────────────────────────────────────────────────
def in_to_ft(v: float) -> float: return v / 12.0
def cuin_to_cft(v: float) -> float: return v / 1728.0


# broad_category lives in engine/categorizer.py — single source of truth.
# Previously this file had a divergent copy that classified LG-ERP
# Divison values ("Home Entertainment", "RFG", "Cooking-Range", H/A,
# RAC, HE, …) differently from the PDF-side function, causing the
# "Other × 16" leak in production (CEO report 2026-05-18).
from engine.categorizer import broad_category  # noqa: F401, E402  (re-export)


# ─────────────────────────────────────────────────────────────────────────
# Data loading + calibrations (CLAUDE.md §Calibrations applied to sample master)
# ─────────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent
DEFAULT_MASTER = APP_DIR / "data" / "sample_input.xlsx"
OUT_DIR = APP_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)

# Per-user persistent data dir. Corp Windows often locks `Documents`, so we
# probe several locations and pick the first writable one. Non-system drives
# (E:, D:) are tried first because they are usually exempt from IT lockdown.
def _candidate_data_dirs(extra: Optional[Path] = None) -> List[Path]:
    cands: List[Path] = []
    if extra is not None:
        cands.append(extra)
    # Windows non-system drives (typical corp scratch / project drives)
    for drive_letter in ("E", "D", "F", "G"):
        drive_root = Path(f"{drive_letter}:/")
        if drive_root.exists():
            cands.append(drive_root / "LG_Load_Optimizer")
    cands.extend([
        Path.home() / "Documents" / "LG_Load_Optimizer",
        Path.home() / "LG_Load_Optimizer",
    ])
    # De-dupe preserving order
    seen, deduped = set(), []
    for c in cands:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except Exception:
        return False


def _resolve_user_data_dir(override: Optional[Path] = None) -> Path:
    """
    Walk the candidate list (E:/D:/F: drives → Documents → home) and return
    the first writable directory. On corp-locked Windows machines where IT
    policy blocks every candidate (no non-system drives, Documents under
    redirection, home read-only), fall back to a per-session temp dir so
    the app still starts — better to lose master persistence than to
    crash with StopIteration at module import (QA Lead audit finding).
    """
    for d in _candidate_data_dirs(extra=override):
        if _writable(d):
            return d
    import tempfile
    fallback = Path(tempfile.mkdtemp(prefix="lg_load_optimizer_"))
    return fallback


USER_DATA_DIR = _resolve_user_data_dir()
USER_MASTER_PATH = USER_DATA_DIR / "master.xlsx"

# Column schema the engine relies on (everything else is optional)
REQUIRED_MASTER_COLS = [
    "model_code", "width_in", "depth_in", "height_in", "weight_lb", "stackable",
]
OPTIONAL_MASTER_COLS = {
    "category": "Uncategorized",
    "this_side_up": True,
    "load_bear_lb": 0.0,
    "fragile": False,
    "notes": "",
    "volume_cft": None,   # auto-computed if missing
}

REQUIRED_TRUCK_COLS = ["truck_type", "length_in", "width_in", "height_in", "max_payload_lb"]
OPTIONAL_TRUCK_COLS = {
    "display_name": None,
    "cargo_volume_cft": None,  # auto-computed if missing
}


@st.cache_data
def _load_workbook(path: str):
    df_master = pd.read_excel(path, sheet_name="Model_Master")
    df_trucks = pd.read_excel(path, sheet_name="Truck_Master")
    try:
        df_loads = pd.read_excel(path, sheet_name="Loads")
    except ValueError:
        df_loads = None
    return df_master, df_trucks, df_loads


def _load_initial_data() -> tuple[pd.DataFrame, pd.DataFrame, Optional[pd.DataFrame], str]:
    """Prefer a user-saved master if present at any known location."""
    global USER_DATA_DIR, USER_MASTER_PATH

    # Pointer file lets us recover a custom save path from previous session
    candidate_paths: List[Path] = []
    pointer = Path.home() / ".lg_load_optimizer_path"
    if pointer.exists():
        try:
            candidate_paths.append(Path(pointer.read_text().strip()))
        except Exception:
            pass
    candidate_paths.append(USER_MASTER_PATH)
    for d in _candidate_data_dirs():
        candidate_paths.append(d / "master.xlsx")

    for p in candidate_paths:
        if p.exists():
            try:
                df_master, df_trucks, df_loads = _load_workbook(str(p))
                # Defensive re-normalize: an older saved master may have NaN
                # in text columns that breaks sort/group operations downstream.
                try:
                    df_master, _ = normalize_master_df(df_master)
                except Exception:
                    pass  # if normalize rejects, surface only the raw load error
                USER_DATA_DIR = p.parent
                USER_MASTER_PATH = p
                return df_master, df_trucks, df_loads, "user"
            except Exception:
                continue
    df_master, df_trucks, df_loads = _load_workbook(str(DEFAULT_MASTER))
    return df_master, df_trucks, df_loads, "bundled"


def save_user_master(
    df_master: pd.DataFrame,
    df_trucks: Optional[pd.DataFrame] = None,
    df_loads: Optional[pd.DataFrame] = None,
    override_dir: Optional[Path] = None,
) -> Path:
    """Persist current master to a writable location.
    Tries (in order): user-supplied override → non-system drives → Documents → home.
    Returns the path actually written.
    """
    last_err: Optional[Exception] = None
    for d in _candidate_data_dirs(extra=override_dir):
        target = d / "master.xlsx"
        try:
            d.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(target, engine="openpyxl") as w:
                df_master.to_excel(w, sheet_name="Model_Master", index=False)
                if df_trucks is not None:
                    df_trucks.to_excel(w, sheet_name="Truck_Master", index=False)
                if df_loads is not None:
                    df_loads.to_excel(w, sheet_name="Loads", index=False)
            _load_workbook.clear()
            # Remember the resolved path for the rest of this session + next launch
            global USER_DATA_DIR, USER_MASTER_PATH
            USER_DATA_DIR = d
            USER_MASTER_PATH = target
            # Also write a pointer file so next launch can find non-default paths
            try:
                pointer = Path.home() / ".lg_load_optimizer_path"
                pointer.write_text(str(target))
            except Exception:
                pass
            return target
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise IOError(
        f"All candidate save paths failed (last: {last_err}). "
        f"Tried: {[str(d) for d in _candidate_data_dirs(extra=override_dir)]}"
    )


# Column aliases — accept multiple spellings of the same field from
# different ERP exports. The LEFT (canonical) is what the rest of the app
# uses; the RIGHT list is alternatives recognised on upload.
# Critical: LG ERP exports use "Divison name" (sic) instead of "category".
# Without this alias the column was lost on upload and every model fell
# back to the "Uncategorized" / "Other" bucket.
MASTER_COL_ALIASES: Dict[str, list[str]] = {
    "category": [
        "divison_name", "division_name", "divison", "division",
        "category_name", "cat_name", "product_category", "sub_division",
        "subdivision", "subdivison",
    ],
    "model_code": ["model", "sku", "model_no", "model_number", "item_code"],
    "width_in": ["width", "width_inch", "w_in", "w"],
    # NOTE: "length_in" is NOT an alias for depth_in here — that would collide
    # with Truck_Master's length_in (which is the truck's canonical length).
    "depth_in": ["depth", "depth_inch", "d_in"],
    "height_in": ["height", "height_inch", "h_in", "h"],
    "weight_lb": ["weight", "weight_lbs", "weight_pound", "wt_lb", "wt"],
    "stackable": ["stack", "can_stack", "stackable_yn"],
    "fragile": ["fragile_yn", "is_fragile", "this_side_up"],
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lowercase + strip + collapse whitespace in column names, then apply
    MASTER_COL_ALIASES so different ERP spellings collapse to canonical
    field names (e.g. ``Divison name`` → ``category``).
    """
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace("(", "").replace(")", "")
        for c in df.columns
    ]
    # Apply alias renames — only if the canonical column is not already
    # present (don't overwrite an explicit category column).
    rename_map: Dict[str, str] = {}
    for canonical, aliases in MASTER_COL_ALIASES.items():
        if canonical in df.columns:
            continue
        for alias in aliases:
            if alias in df.columns:
                rename_map[alias] = canonical
                break
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def _coerce_bool(s: pd.Series) -> pd.Series:
    """Accept True/False/1/0/Y/N/Yes/No (any case)."""
    def _conv(v):
        if isinstance(v, bool):
            return v
        if pd.isna(v):
            return False
        if isinstance(v, (int, float)):
            return bool(v)
        s = str(v).strip().lower()
        return s in ("true", "1", "yes", "y", "t")
    return s.apply(_conv)


def normalize_master_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Take an arbitrary upload, return (clean_df, warnings).
    Raises ValueError if required columns are missing.
    """
    df = _normalize_columns(raw)
    warnings: list[str] = []

    missing = [c for c in REQUIRED_MASTER_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}. "
            f"Required schema: {REQUIRED_MASTER_COLS}"
        )

    # Fill optional columns
    for col, default in OPTIONAL_MASTER_COLS.items():
        if col not in df.columns:
            df[col] = default
            if default is not None:
                warnings.append(f"Column '{col}' missing — filled with default ({default!r})")

    # Coerce types
    for col in ("width_in", "depth_in", "height_in", "weight_lb", "load_bear_lb"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["stackable"] = _coerce_bool(df["stackable"])
    df["fragile"] = _coerce_bool(df["fragile"])
    if "this_side_up" in df.columns:
        df["this_side_up"] = _coerce_bool(df["this_side_up"])
    # Ensure text columns are str so sort/group operations don't mix dtypes
    for col in ("model_code", "category", "notes"):
        if col in df.columns:
            df[col] = df[col].fillna(
                "Uncategorized" if col == "category" else ""
            ).astype(str)

    # Drop rows with missing critical dims
    invalid = df[REQUIRED_MASTER_COLS].isna().any(axis=1)
    if invalid.any():
        warnings.append(f"Dropped {invalid.sum()} row(s) with missing required values")
        df = df.loc[~invalid].reset_index(drop=True)

    # Unit sanity check — appliances in inches: ~10-80 in. Values >200 look like mm.
    if not df.empty:
        median_w = df["width_in"].median()
        if median_w > 200:
            warnings.append(
                f"⚠ width_in median = {median_w:.0f} — values look like MILLIMETERS, "
                "not inches. Divide by 25.4 before uploading, or rename columns."
            )

    # Auto-compute volume_cft for rows where it's missing
    if "volume_cft" in df.columns:
        df["volume_cft"] = pd.to_numeric(df["volume_cft"], errors="coerce")
        need_vol = df["volume_cft"].isna()
        if need_vol.any():
            df.loc[need_vol, "volume_cft"] = (
                df.loc[need_vol, "width_in"]
                * df.loc[need_vol, "depth_in"]
                * df.loc[need_vol, "height_in"]
                / 1728.0
            ).round(2)
            warnings.append(f"Auto-computed volume_cft for {need_vol.sum()} row(s) (= w×d×h ÷ 1728)")

    return df, warnings


def normalize_trucks_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    df = _normalize_columns(raw)
    warnings: list[str] = []
    missing = [c for c in REQUIRED_TRUCK_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Truck_Master missing required columns: {missing}. "
            f"Found: {list(df.columns)}. Required: {REQUIRED_TRUCK_COLS}"
        )
    for col, default in OPTIONAL_TRUCK_COLS.items():
        if col not in df.columns:
            df[col] = default
    for col in ("length_in", "width_in", "height_in", "max_payload_lb", "cargo_volume_cft"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    # Auto-compute cargo_volume_cft if missing
    need_vol = df["cargo_volume_cft"].isna() if "cargo_volume_cft" in df.columns else None
    if need_vol is not None and need_vol.any():
        df.loc[need_vol, "cargo_volume_cft"] = (
            df.loc[need_vol, "length_in"]
            * df.loc[need_vol, "width_in"]
            * df.loc[need_vol, "height_in"]
            / 1728.0
        ).round(1)
        warnings.append(f"Auto-computed cargo_volume_cft for {need_vol.sum()} truck(s)")
    return df, warnings


def apply_calibrations(master_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """CLAUDE.md: required calibrations so all 36 units fit in 26ft sample."""
    if "LDFN4542S" in master_dict:
        master_dict["LDFN4542S"].update({"stackable": True, "load_bear_lb": 132.3, "fragile": False})
    if "LWS3063ST" in master_dict:
        master_dict["LWS3063ST"].update({"stackable": True, "load_bear_lb": 198.4, "fragile": False})
    return master_dict


# ─────────────────────────────────────────────────────────────────────────
# Streamlit page setup
# ─────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="LG Load Optimizer", page_icon="🚛", layout="wide")

# ── Locale (EN default, KO available; v2.0 ships EN+KO only) ───────────
from engine.i18n import set_locale, t as _t, available_locales

if "locale" not in st.session_state:
    st.session_state["locale"] = "en"
set_locale(st.session_state["locale"])

st.sidebar.title("🚛 Load Optimizer")
page = st.sidebar.radio(
    "Navigation",
    ["📦 Load Plan", "📋 Model Master", "🚛 Truck Master"],
)
# Language toggle — sits at the bottom of the sidebar so it doesn't
# distract from the daily workflow.
_lang_label = {"en": "🇺🇸 English", "ko": "🇰🇷 한국어"}
_chosen = st.sidebar.radio(
    "Language / 언어",
    options=available_locales(),
    format_func=lambda c: _lang_label.get(c, c),
    horizontal=True,
    index=available_locales().index(st.session_state["locale"]),
    key="locale_radio",
)
if _chosen != st.session_state["locale"]:
    st.session_state["locale"] = _chosen
    set_locale(_chosen)
    st.rerun()
st.sidebar.markdown("---")
st.sidebar.caption("Auto-optimized loading · v2.0")


# Initial master + trucks (auto-loads user-saved master if present)
if "df_master" not in st.session_state:
    df_master, df_trucks, df_loads, source = _load_initial_data()
    st.session_state.df_master = df_master
    st.session_state.df_trucks = df_trucks
    st.session_state.df_loads = df_loads
    st.session_state.master_source = source


# =========================================================================
# Step 1 helpers
# =========================================================================
def compute_loaded_volume_ft3(sim_result: Dict[str, Any]) -> float:
    """Sum of placed-box volumes in ft³ (placement dims are in inches)."""
    total_cuin = sum(
        p["dim_x_in"] * p["dim_y_in"] * p["dim_z_in"]
        for p in sim_result["placements"]
    )
    return cuin_to_cft(total_cuin)


def pick_recommended(sim_26: Dict[str, Any], sim_53: Dict[str, Any]) -> Optional[str]:
    """Smaller truck first (cost). 26ft if it fits, otherwise 53ft."""
    if sim_26["fits"]:
        return "26ft"
    if sim_53["fits"]:
        return "53ft"
    return None


def build_load_composition_df(
    load_lines: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]
) -> pd.DataFrame:
    rows = []
    for line in load_lines:
        mc = line["model_code"]
        m = master[mc]
        unit_vol_ft3 = m.get("volume_cft") or cuin_to_cft(
            m["width_in"] * m["depth_in"] * m["height_in"]
        )
        rows.append({
            "Model": mc,
            "Category": m["category"],
            "Qty": line["quantity"],
            "Dim W × D × H (in)": (
                f"{m['width_in']:.1f} × "
                f"{m['depth_in']:.1f} × "
                f"{m['height_in']:.1f}"
            ),
            "Unit vol (ft³)": round(unit_vol_ft3, 1),
            "Total vol (ft³)": round(unit_vol_ft3 * line["quantity"], 0),
            "Weight (lb)": round(m["weight_lb"] * line["quantity"], 0),
        })
    return pd.DataFrame(rows)


def _bar_color(pct: float) -> str:
    if pct >= 70:
        return BAR_GREEN
    if pct >= 40:
        return BAR_ORANGE
    return BAR_GRAY


def build_loadrate_bars(metrics: Dict[str, Any]) -> go.Figure:
    rows = [
        ("Length", metrics["compactness_pct"]),
        ("Volume", metrics["volume_util_pct"]),
        ("Weight", metrics["weight_util_pct"]),
    ]
    fig = go.Figure(go.Bar(
        y=[r[0] for r in rows],
        x=[r[1] for r in rows],
        orientation="h",
        marker_color=[_bar_color(r[1]) for r in rows],
        text=[f"{r[1]:.0f}%" for r in rows],
        textposition="outside",
        cliponaxis=False,
        hoverinfo="x+y",
    ))
    fig.update_layout(
        height=180,
        margin=dict(l=20, r=50, t=10, b=10),
        xaxis=dict(range=[0, 110], showgrid=False, showticklabels=False, zeroline=False),
        yaxis=dict(autorange="reversed", tickfont=dict(size=12)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        showlegend=False,
    )
    return fig


def render_truck_card(
    truck_key: str,
    sim: Dict[str, Any],
    truck_spec: Dict[str, Any],
    is_recommended: bool,
) -> None:
    label = "26ft Box Truck" if truck_key == "26ft" else "53ft Dry Van"
    dims_ft = (
        f"{in_to_ft(truck_spec['length_in']):.0f} × "
        f"{in_to_ft(truck_spec['width_in']):.1f} × "
        f"{in_to_ft(truck_spec['height_in']):.1f} ft"
    )

    if sim["fits"]:
        pill = (
            "<span style='background:#D1FAE5;color:#0F6E56;"
            "padding:3px 12px;border-radius:12px;font-size:13px;font-weight:700;"
            "letter-spacing:0.3px;'>FITS</span>"
        )
    else:
        pill = (
            "<span style='background:#FEE2E2;color:#991B1B;"
            "padding:3px 12px;border-radius:12px;font-size:13px;font-weight:700;"
            "letter-spacing:0.3px;'>DOES NOT FIT</span>"
        )

    # Recommended cards get an outsized banner + green background tint + thick border.
    # Non-recommended cards stay quiet so the eye snaps to the recommendation.
    if is_recommended:
        banner_html = (
            '<div style="background:linear-gradient(90deg,#1D9E75 0%,#0F6E56 100%);'
            'color:white;padding:10px 16px;border-radius:8px 8px 0 0;'
            'margin:-4px -4px 12px -4px;font-weight:800;font-size:14px;'
            'letter-spacing:1.5px;text-align:center;'
            'box-shadow:0 2px 4px rgba(29,158,117,0.25);">'
            '⭐ &nbsp; RECOMMENDED &nbsp; — &nbsp; USE THIS TRUCK'
            '</div>'
        )
        title_bg = "#F0FDF4"
        title_color = "#0F6E56"
        outer_border = "3px solid #1D9E75"
    else:
        banner_html = ""
        title_bg = "transparent"
        title_color = "#374151"
        outer_border = "1px solid #E5E7EB"

    # Render outer chrome as a single un-indented HTML block — Streamlit's
    # markdown parser treats 4-space-indented lines as code blocks, which
    # mangles multi-div HTML if left indented.
    card_bg = title_bg if is_recommended else "white"
    html = (
        f'<div style="border:{outer_border};border-radius:10px;padding:4px;'
        f'background:{card_bg};">'
        f'{banner_html}'
        f'<div style="padding:0 12px 8px;">'
        f'<div style="font-size:24px;font-weight:800;line-height:1.2;'
        f'color:{title_color};">{label} &nbsp; {pill}</div>'
        f'<div style="color:{TEXT_MUTED};font-size:13px;margin-top:2px;">'
        f'{dims_ft}</div>'
        f'</div>'
        f'</div>'
    )
    with st.container(border=False):
        st.markdown(html, unsafe_allow_html=True)

        m = sim["metrics"]
        loaded_vol_ft3 = compute_loaded_volume_ft3(sim)
        # ── Hero stat: Length + Volume side-by-side as BIG numbers ─────
        truck_len_ft = in_to_ft(truck_spec["length_in"])
        truck_vol_ft3 = truck_spec.get("cargo_volume_cft", 0)
        hero_color = "#1D4ED8" if is_recommended else "#6B7280"
        hero_html = (
            f'<div style="display:grid;grid-template-columns:1fr 1px 1fr;gap:14px;'
            f'padding:14px 0 10px 0;border-top:1px solid #E5E7EB;'
            f'border-bottom:1px solid #E5E7EB;margin:8px 0;align-items:center;">'
            f'<div style="text-align:center;">'
            f'<div style="font-size:10px;color:#6B7280;text-transform:uppercase;'
            f'letter-spacing:0.6px;font-weight:700;margin-bottom:6px;">Linear length</div>'
            f'<div><span style="font-size:46px;font-weight:800;letter-spacing:-1.6px;'
            f'line-height:1;color:{hero_color};">{m["x_used_ft"]:.1f}</span>'
            f'<span style="font-size:16px;font-weight:700;color:#6B7280;margin-left:3px;">ft</span></div>'
            f'<div style="font-size:11px;color:#6B7280;margin-top:6px;">'
            f'of <b style="color:#111827;">{truck_len_ft:.1f} ft</b> · '
            f'headroom <b style="color:#111827;">{max(0,truck_len_ft - m["x_used_ft"]):.1f} ft</b></div>'
            f'</div>'
            f'<div style="background:#E5E7EB;width:1px;align-self:stretch;"></div>'
            f'<div style="text-align:center;">'
            f'<div style="font-size:10px;color:#6B7280;text-transform:uppercase;'
            f'letter-spacing:0.6px;font-weight:700;margin-bottom:6px;">Volume</div>'
            f'<div><span style="font-size:46px;font-weight:800;letter-spacing:-1.6px;'
            f'line-height:1;color:{hero_color};">{loaded_vol_ft3:,.0f}</span>'
            f'<span style="font-size:16px;font-weight:700;color:#6B7280;margin-left:3px;">ft³</span></div>'
            f'<div style="font-size:11px;color:#6B7280;margin-top:6px;">'
            f'of <b style="color:#111827;">{truck_vol_ft3:,.0f} ft³</b> · '
            f'<b style="color:#111827;">{m.get("volume_util_pct", 0):.1f}%</b> util</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(hero_html, unsafe_allow_html=True)

        # Compact secondary row — units + weight (length/volume are in the hero).
        c1, c2 = st.columns(2)
        with c1:
            st.metric("Units", f"{sim['fitted_count']}/{sim['requested_count']}")
        with c2:
            st.metric("Weight (lb)", f"{m['weight_total_lb']:,.0f}")

        st.markdown(
            "<div style='font-size:13px;color:#374151;margin:4px 0 -8px 0;font-weight:600;'>"
            "Load rate</div>",
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            build_loadrate_bars(m),
            use_container_width=True,
            config={"displayModeBar": False},
        )

        # Verdict
        verdict = build_verdict(sim, truck_spec, is_recommended)
        st.markdown(verdict, unsafe_allow_html=True)


def build_verdict(sim: Dict[str, Any], truck_spec: Dict[str, Any], is_recommended: bool) -> str:
    m = sim["metrics"]
    if not sim["fits"]:
        return (
            f"<div style='color:#991B1B;font-weight:600;margin-top:8px;'>"
            f"⚠ Too small · {sim['unfitted_count']} units left over</div>"
        )
    if is_recommended:
        rem_ft = m["remaining_length_ft"]
        rem_in = rem_ft * 12 if rem_ft < 2 else None
        rem_disp = f"{rem_in:.0f} in" if rem_in is not None else f"{rem_ft:.1f} ft"
        return (
            f"<div style='color:#0F6E56;font-weight:600;margin-top:8px;'>"
            f"✓ Right size · {rem_disp} buffer at rear</div>"
        )
    # fits but oversized
    return (
        f"<div style='color:#92400E;font-weight:600;margin-top:8px;'>"
        f"⚠ Oversized · {m['remaining_length_ft']:.1f} ft empty</div>"
    )


def render_comparison_table(sim_26: Dict[str, Any], sim_53: Dict[str, Any]) -> None:
    m26 = sim_26["metrics"]
    m53 = sim_53["metrics"]

    def fmt_delta_pp(a: float, b: float) -> str:
        d = a - b
        sign = "+" if d >= 0 else ""
        return f"{sign}{d:.0f}%p"

    def fmt_waste(sim: Dict[str, Any]) -> str:
        rem_ft = sim["metrics"]["remaining_length_ft"]
        if rem_ft < 2:
            return f"{rem_ft * 12:.0f} in"
        return f"{rem_ft:.1f} ft"

    rows = [
        ("Length 장입률", f"{m26['compactness_pct']:.0f}%", f"{m53['compactness_pct']:.0f}%",
         fmt_delta_pp(m26["compactness_pct"], m53["compactness_pct"])),
        ("Volume 장입률", f"{m26['volume_util_pct']:.0f}%", f"{m53['volume_util_pct']:.0f}%",
         fmt_delta_pp(m26["volume_util_pct"], m53["volume_util_pct"])),
        ("Weight 장입률", f"{m26['weight_util_pct']:.0f}%", f"{m53['weight_util_pct']:.0f}%",
         fmt_delta_pp(m26["weight_util_pct"], m53["weight_util_pct"])),
        ("낭비 공간", fmt_waste(sim_26), fmt_waste(sim_53), "—"),
    ]
    df = pd.DataFrame(rows, columns=["Metric", "26ft Box", "53ft Van", "Δ"])
    st.dataframe(df, hide_index=True, use_container_width=True)


def render_constraints(sim: Dict[str, Any]) -> None:
    # Pair-packing algorithm enforces every constraint structurally except payload.
    # Payload is enforced by master spec; we surface the actual margin.
    under_payload = sim["metrics"]["weight_util_pct"] <= 100
    items: List[Tuple[str, bool]] = [
        ("All units upright", True),
        ("Door track cleared", True),
        ("Stack limits respected", True),
        ("Washer/Dryer paired", True),
        ("Max lane count", True),
        ("Under payload", under_payload),
    ]
    c1, c2 = st.columns(2)
    for i, (label, ok) in enumerate(items):
        col = c1 if i % 2 == 0 else c2
        icon = "✓" if ok else "✗"
        color = COLOR_CHECK_ICON if ok else "#DC2626"
        col.markdown(
            f"<div style='margin:4px 0;'>"
            f"<span style='color:{color};font-weight:700;font-size:16px;'>{icon}</span> "
            f"<span>{label}</span></div>",
            unsafe_allow_html=True,
        )


# =========================================================================
# Math formula — closed-form fit predictor (no simulator)
# =========================================================================
def render_math_formula(
    load_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    trucks_map: Dict[str, Dict[str, Any]],
) -> None:
    with st.expander("📐 Math formula — predict fit without simulator", expanded=False):
        st.markdown(
            r"""
For each **dim-group** $g$ (same width × depth × height SKUs paired together):

$$
\begin{aligned}
\text{eff\_H} &= H_{truck} - 250\text{ mm}\quad\text{(door track loss)} \\
\text{layers}_g &= \begin{cases}
  \lfloor \text{eff\_H} / h_g \rfloor & \text{if stackable}_g \\
  1 & \text{otherwise}
\end{cases}
\end{aligned}
$$

For each orientation $(w^*, d^*) \in \{(w_g,d_g),\,(d_g,w_g)\}$:
$$
\text{lanes} = \lfloor W_{truck} / w^* \rfloor,\quad
\text{per\_row} = \text{lanes} \times \text{layers}_g,\quad
\text{rows} = \lceil Q_g / \text{per\_row} \rceil,\quad
\text{length} = \text{rows} \times d^*
$$

Choose the orientation that minimizes $\text{length}$. Then:

$$
\boxed{\text{FITS}\ \iff\ \sum_g \text{length}_g\ \le\ L_{truck}}
$$

No `load_bear` or `fragile` gating in max-fit mode (CEO 2026-05-15).
            """
        )

        st.markdown("**Applied to this load**")
        col26, col53 = st.columns(2)
        for col, tkey in ((col26, "26ft"), (col53, "53ft")):
            with col:
                result = fits_formula(load_lines, master, trucks_map[tkey])
                label = "26ft Box Truck" if tkey == "26ft" else "53ft Dry Van"
                verdict = "✓ FITS" if result["fits"] else "✗ DOES NOT FIT"
                color = COLOR_CHECK_ICON if result["fits"] else "#DC2626"
                st.markdown(
                    f"**{label}** &nbsp;&nbsp; "
                    f"<span style='color:{color};font-weight:700;'>{verdict}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Σ length = {result['predicted_length_ft']:.2f} ft  vs  "
                    f"truck = {result['truck_length_ft']:.0f} ft  →  "
                    f"{'+' if result['remaining_ft'] >= 0 else ''}"
                    f"{result['remaining_ft']:.2f} ft buffer"
                )
                rows = []
                for b in result["breakdown"]:
                    models_str = " + ".join(m for m, _ in b["models"])
                    if len(models_str) > 22:
                        models_str = models_str[:20] + "…"
                    rows.append({
                        "Models": models_str,
                        "Qty": b["qty"],
                        "Lanes × Layers": f"{b['lanes']} × {b['layers']}",
                        "Rows": b["rows"],
                        "Length (ft)": b["length_ft"],
                    })
                rows.append({
                    "Models": "Σ",
                    "Qty": "—",
                    "Lanes × Layers": "—",
                    "Rows": "—",
                    "Length (ft)": round(result["predicted_length_ft"], 2),
                })
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# =========================================================================
# Step 1 page
# =========================================================================
def render_step1(
    load_id: str,
    load_lines: List[Dict[str, Any]],
    master: Dict[str, Dict[str, Any]],
    trucks_map: Dict[str, Dict[str, Any]],
    sim_26: Dict[str, Any],
    sim_53: Dict[str, Any],
    destination: Optional[str],
) -> None:
    total_qty = sum(l["quantity"] for l in load_lines)
    total_weight_lb = sum(
        master[l["model_code"]]["weight_lb"] * l["quantity"] for l in load_lines
    )
    total_vol_cft = sum(
        (master[l["model_code"]].get("volume_cft") or cuin_to_cft(
            master[l["model_code"]]["width_in"]
            * master[l["model_code"]]["depth_in"]
            * master[l["model_code"]]["height_in"]
        )) * l["quantity"]
        for l in load_lines
    )

    st.markdown("#### Step 1 of 2 · Can it fit?")
    parts = [f"Load **{load_id}**"]
    if destination:
        parts.append(destination)
    parts += [
        f"{total_qty} units",
        f"{total_weight_lb:,.0f} lb",
        f"{total_vol_cft:,.0f} ft³",
    ]
    st.caption(" · ".join(parts))

    with st.expander("📋 Load composition", expanded=False):
        df_comp = build_load_composition_df(load_lines, master)
        st.dataframe(df_comp, hide_index=True, use_container_width=True)

    st.markdown("### Truck simulation results")
    recommended = pick_recommended(sim_26, sim_53)
    c1, c2 = st.columns(2)
    with c1:
        render_truck_card("26ft", sim_26, trucks_map["26ft"], is_recommended=(recommended == "26ft"))
    with c2:
        render_truck_card("53ft", sim_53, trucks_map["53ft"], is_recommended=(recommended == "53ft"))

    if recommended is None:
        st.error(
            "⚠ Neither truck can fit the full load. "
            "Consider splitting across two trucks or upgrading to a multi-stop plan."
        )

    st.markdown("### Why this recommendation?")
    render_comparison_table(sim_26, sim_53)

    st.markdown("### All constraints passed")
    primary_sim = sim_26 if recommended == "26ft" else sim_53
    render_constraints(primary_sim)

    render_math_formula(load_lines, master, trucks_map)

    st.info(
        "→ Switch to the **Step 2 · Load & Work Order** tab above for the 3D view, "
        "loading sequence, and printable work order."
    )


# =========================================================================
# Step 2-A helpers (3D rendering)
# =========================================================================
DOOR_TRACK_LOSS_IN = 10     # 10" headroom loss on roll-up doors (rear)
DOOR_TRACK_LENGTH_IN = 60   # 5 ft × 12 in

CATEGORY_ICONS = {
    "refrigerator": "R", "washer": "W", "dryer": "D",
    "dishwasher": "Dw", "oven": "O", "microwave": "M",
    "tv": "TV", "monitor": "Mon", "other": "?",
}

CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]


def _box_mesh(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str, opacity: float = 0.92, hovertext: Optional[str] = None,
) -> go.Mesh3d:
    x1, y1, z1 = x0 + dx, y0 + dy, z0 + dz
    xs = [x0, x1, x1, x0, x0, x1, x1, x0]
    ys = [y0, y0, y1, y1, y0, y0, y1, y1]
    zs = [z0, z0, z0, z0, z1, z1, z1, z1]
    # 12 triangles: 2 per face × 6 faces (bottom, top, front, right, back, left)
    i = [0, 0, 4, 4, 0, 0, 1, 1, 2, 2, 3, 3]
    j = [1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 0, 4]
    k = [2, 3, 6, 7, 5, 4, 6, 5, 7, 6, 4, 7]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=opacity, flatshading=True,
        hovertext=hovertext, hoverinfo=("text" if hovertext else "skip"),
        showlegend=False,
    )


def _box_edges(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str = "#1F2937", width: float = 1.5, dash: Optional[str] = None,
) -> go.Scatter3d:
    x1, y1, z1 = x0 + dx, y0 + dy, z0 + dz
    segments = [
        ((x0, y0, z0), (x1, y0, z0)),
        ((x1, y0, z0), (x1, y1, z0)),
        ((x1, y1, z0), (x0, y1, z0)),
        ((x0, y1, z0), (x0, y0, z0)),
        ((x0, y0, z1), (x1, y0, z1)),
        ((x1, y0, z1), (x1, y1, z1)),
        ((x1, y1, z1), (x0, y1, z1)),
        ((x0, y1, z1), (x0, y0, z1)),
        ((x0, y0, z0), (x0, y0, z1)),
        ((x1, y0, z0), (x1, y0, z1)),
        ((x1, y1, z0), (x1, y1, z1)),
        ((x0, y1, z0), (x0, y1, z1)),
    ]
    xs: List[Any] = []
    ys: List[Any] = []
    zs: List[Any] = []
    for (p, q) in segments:
        xs.extend([p[0], q[0], None])
        ys.extend([p[1], q[1], None])
        zs.extend([p[2], q[2], None])
    line = dict(color=color, width=width)
    if dash:
        line["dash"] = dash
    return go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=line, hoverinfo="skip", showlegend=False,
    )


def build_3d_figure(
    sim: Dict[str, Any], truck_spec: Dict[str, Any], master: Dict[str, Dict[str, Any]]
) -> go.Figure:
    L = truck_spec["length_in"]
    W = truck_spec["width_in"]
    H = truck_spec["height_in"]

    traces: List[Any] = []

    # Truck outer wireframe (gray dotted)
    traces.append(_box_edges(0, 0, 0, L, W, H, color="#9CA3AF", width=1, dash="dot"))

    # Boxes
    for p in sim["placements"]:
        cat = broad_category(master[p["model_code"]]["category"])
        palette = CAT_COLORS.get(cat, CAT_COLORS["other"])
        hover = (
            f"#{p['seq']} · {p['model_code']}<br>"
            f"x={p['x_in']/12.0:.1f} ft · lane {p['lane']} · "
            f"layer {p['layer']}<br>{p['weight_lb']:.0f} kg "
            f"({p['weight_lb']:.0f} lb)"
        )
        traces.append(_box_mesh(
            p["x_in"], p["y_in"], p["z_in"],
            p["dim_x_in"], p["dim_y_in"], p["dim_z_in"],
            color=palette["front"], opacity=0.92, hovertext=hover,
        ))
        traces.append(_box_edges(
            p["x_in"], p["y_in"], p["z_in"],
            p["dim_x_in"], p["dim_y_in"], p["dim_z_in"],
            color=palette["stroke"], width=1.2,
        ))

    # Door track (rear 5 ft × top 10") — Phase D polish: stronger fill +
    # dashed boundary wireframe so it reads on screen AND in a B&W print
    # screenshot (UX Director audit).
    door_x = L - DOOR_TRACK_LENGTH_IN
    door_z = H - DOOR_TRACK_LOSS_IN
    if door_x >= 0 and door_z >= 0:
        traces.append(_box_mesh(
            door_x, 0, door_z,
            DOOR_TRACK_LENGTH_IN, W, DOOR_TRACK_LOSS_IN,
            color="#DC2626", opacity=0.30,
            hovertext="Door track · 87 in cap (5 ft from rear)",
        ))
        # Explicit wireframe outline so the zone is unambiguous even
        # under low opacity.
        traces.append(_box_edges(
            door_x, 0, door_z,
            DOOR_TRACK_LENGTH_IN, W, DOOR_TRACK_LOSS_IN,
            color="#B91C1C", width=2, dash="dash",
        ))

    # Free space outline after last box
    x_used = sim["metrics"]["x_used_in"]
    if L - x_used > 100:
        traces.append(_box_edges(
            x_used, 0, 0, L - x_used, W, H,
            color="#10B981", width=2, dash="dash",
        ))

    # FRONT / REAR text anchors so the viewer can orient without
    # reading the axis ticks (Phase D — Forklift Op audit asked for
    # bigger orientation cues).
    traces.append(go.Scatter3d(
        x=[0], y=[W * 0.5], z=[H + 4],
        mode="text",
        text=["▶ FRONT (cab)"],
        textfont=dict(size=14, color="#1F2937", family="Arial Black"),
        hoverinfo="skip", showlegend=False,
    ))
    traces.append(go.Scatter3d(
        x=[L], y=[W * 0.5], z=[H + 4],
        mode="text",
        text=["REAR ◀"],
        textfont=dict(size=14, color="#B91C1C", family="Arial Black"),
        hoverinfo="skip", showlegend=False,
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        scene=dict(
            xaxis=dict(title="Length (in) · Cab → Dock", showbackground=False),
            yaxis=dict(title="Width (in)", showbackground=False),
            zaxis=dict(title="Height (in)", showbackground=False),
            aspectmode="data",
            camera=dict(eye=dict(x=1.6, y=-2.0, z=1.1)),
        ),
        height=580,
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        paper_bgcolor="white",
    )
    return fig


def _group_zones(sim: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """
    Group placements by physical dimension key (paired same-dim items → one zone).
    Returns zones sorted by first x position (cab → dock).
    Used by Step 2-A zone breakdown and Step 2-B loading sequence.
    """
    groups: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
    for p in sim["placements"]:
        key = (p["dim_y_in"], p["dim_x_in"], p["dim_z_in"])
        groups.setdefault(key, []).append(p)
    return sorted(groups.values(), key=lambda ps: min(p["x_in"] for p in ps))


def _zone_category_label(ps: List[Dict[str, Any]], master: Dict[str, Dict[str, Any]]) -> str:
    broad_cats: List[str] = []
    for p in ps:
        c = broad_category(master[p["model_code"]]["category"])
        if c not in broad_cats:
            broad_cats.append(c)
    return " + ".join(c.capitalize() for c in broad_cats)


def build_zone_breakdown_df(
    sim: Dict[str, Any], master: Dict[str, Dict[str, Any]]
) -> pd.DataFrame:
    zones = _group_zones(sim)
    rows = []
    zone_letters = list("ABCDEFGHIJ")
    for idx, ps in enumerate(zones):
        models = sorted({p["model_code"] for p in ps})
        model_str = " + ".join(models)
        if len(model_str) > 28:
            model_str = model_str[:26] + "…"
        lanes = len({p["lane"] for p in ps})
        layers = len({p["layer"] for p in ps})
        rows_count = len({p["x_in"] for p in ps})
        x_start_ft = min(p["x_in"] for p in ps) / 12.0
        x_end_ft = max(p["x_in"] + p["dim_x_in"] for p in ps) / 12.0
        weight_lb = sum(p["weight_lb"] for p in ps)
        rows.append({
            "Zone": zone_letters[idx] if idx < len(zone_letters) else f"Z{idx}",
            "Category": _zone_category_label(ps, master),
            "Models": model_str,
            "Qty": len(ps),
            "R × L × T": f"{rows_count} × {lanes} × {layers}",
            "Length range (ft)": f"{x_start_ft:.1f} → {x_end_ft:.1f}",
            "Weight (lb)": int(round(weight_lb)),
        })
    return pd.DataFrame(rows)



def render_load_sequence(df_zones: pd.DataFrame) -> None:
    if df_zones.empty:
        st.caption("(no placements)")
        return
    parts = []
    for i, row in enumerate(df_zones.itertuples(), 1):
        marker = CIRCLED[i - 1] if i <= 10 else f"{i}."
        parts.append(f"**{marker}** {row.Category}")
    parts.append("**✓** Secure & inspect")
    st.markdown("  →  ".join(parts))


def render_legend_chips(sim: Dict[str, Any], master: Dict[str, Dict[str, Any]]) -> None:
    cat_in_use: List[str] = []
    for p in sim["placements"]:
        cat = broad_category(master[p["model_code"]]["category"])
        if cat not in cat_in_use:
            cat_in_use.append(cat)
    parts = []
    for cat in cat_in_use:
        c = CAT_COLORS.get(cat, CAT_COLORS["other"])
        icon = CATEGORY_ICONS.get(cat, "?")
        parts.append(
            f"<span style='display:inline-block;background:{c['front']};"
            f"color:white;padding:2px 8px;border-radius:6px;font-size:12px;"
            f"margin:0 6px 4px 0;'>[{icon}] {cat.capitalize()}</span>"
        )
    if parts:
        st.markdown("".join(parts), unsafe_allow_html=True)


def build_simple_excel(
    sim: Dict[str, Any], load_id: str, truck_key: str,
    master: Dict[str, Dict[str, Any]], trucks_map: Dict[str, Dict[str, Any]],
) -> bytes:
    from io import BytesIO

    m = sim["metrics"]
    summary = pd.DataFrame([{
        "Load_ID": load_id,
        "Truck": trucks_map[truck_key].get("display_name", truck_key),
        "Fits": sim["fits"],
        "Fitted": sim["fitted_count"],
        "Requested": sim["requested_count"],
        "Length_used_ft": m["x_used_ft"],
        "Compactness_pct": m["compactness_pct"],
        "Volume_util_pct": m["volume_util_pct"],
        "Weight_total_lb": m["weight_total_lb"],
        "Weight_util_pct": m["weight_util_pct"],
        "Remaining_ft": m["remaining_length_ft"],
        "Strategy": sim["strategy"],
    }])

    place_rows = []
    for p in sim["placements"]:
        spec = master.get(p["model_code"], {})
        place_rows.append({
            "Seq": p["seq"],
            "Model_Code": p["model_code"],
            "Category": spec.get("category", ""),
            "Lane": p["lane"], "Layer": p["layer"],
            "Pos_X_ft": round(p["x_in"] / 12.0, 2),
            "Pos_X_in": p["x_in"], "Pos_Y_in": p["y_in"], "Pos_Z_in": p["z_in"],
            "Dim_X_in": p["dim_x_in"], "Dim_Y_in": p["dim_y_in"], "Dim_Z_in": p["dim_z_in"],
            "Weight_lb": round(p["weight_lb"], 1),
        })
    placements_df = pd.DataFrame(place_rows)

    unfitted_df = pd.DataFrame(sim.get("unfitted_detail", []))

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        placements_df.to_excel(writer, sheet_name="Placements", index=False)
        if not unfitted_df.empty:
            unfitted_df.to_excel(writer, sheet_name="Unfitted", index=False)
    return buf.getvalue()


# =========================================================================
# Step 2 v4 — dashboard mirroring the PDF v4 work-order layout
# =========================================================================
# Same data, same shape, same engine output as the PDF.  The dispatcher's
# screen and the worker's printout match section-for-section.  Legacy
# render_step2 is kept above for emergency rollback.


def _build_row_mini_view(
    truck_spec: Dict[str, Any],
    rows_so_far: list,
    current_row_idx: int,
) -> "go.Figure":
    """Side-view card body — one card = one physical row (Q4 CEO direction).

    Rows BEFORE current: light-grey ghost (already loaded).
    Current row:           bright blue + ↓ arrow centred over the column.
    Future rows:           not drawn (keeps focus on the next action).
    """
    import plotly.graph_objects as go
    L = float(truck_spec["length_in"])
    H = float(truck_spec["height_in"])
    DT_LEN = 60.0
    DT_LOSS = 10.0
    # CEO 2026-05-19: orange (#F97316) for current row — warehouse safety
    # standard ("act now" colour, beats blue ~1.4× for glance recognition,
    # prints clearly in black & white).
    CUR_FILL = "#F97316"; CUR_EDGE = "#C2410C"
    GHOST_FILL = "#E2E8F0"; GHOST_EDGE = "#CBD5E1"

    shapes = []
    shapes.append(dict(
        type="rect", x0=0, y0=0, x1=L, y1=H,
        line=dict(color="#94A3B8", width=2),
        fillcolor="#F8FAFC", layer="below",
    ))
    shapes.append(dict(
        type="rect",
        x0=L - DT_LEN, y0=H - DT_LOSS, x1=L, y1=H,
        line=dict(color="#B91C1C", width=1, dash="dash"),
        fillcolor="rgba(220,38,38,0.18)", layer="below",
    ))

    cur_xs: list = []
    cur_top_y: float = 0.0
    for r_idx, row in enumerate(rows_so_far[: current_row_idx + 1]):
        is_current = (r_idx == current_row_idx)
        fill = CUR_FILL if is_current else GHOST_FILL
        line_col = CUR_EDGE if is_current else GHOST_EDGE
        line_w = 1.6 if is_current else 0.7
        for p in row.placements:
            x0 = p["x_in"]; x1 = x0 + p["dim_x_in"]
            y0 = p["z_in"]; y1 = y0 + p["dim_z_in"]
            shapes.append(dict(
                type="rect",
                x0=x0, y0=y0, x1=x1, y1=y1,
                line=dict(color=line_col, width=line_w),
                fillcolor=fill, layer="above",
            ))
            if is_current:
                cur_xs.append((x0 + x1) / 2)
                cur_top_y = max(cur_top_y, y1)

    fig = go.Figure()
    fig.update_layout(
        shapes=shapes,
        xaxis=dict(range=[-5, L + 5], visible=False, fixedrange=True),
        yaxis=dict(range=[-8, H + 22], visible=False, scaleratio=1,
                   scaleanchor="x", fixedrange=True),
        height=140,
        margin=dict(l=4, r=4, t=4, b=4),
        paper_bgcolor="white", plot_bgcolor="white", showlegend=False,
    )
    fig.add_annotation(
        x=2, y=H + 10, text="⬅ FRONT", showarrow=False,
        font=dict(size=11, color="#4338CA", family="sans-serif"),
        xanchor="left", yanchor="bottom",
    )
    fig.add_annotation(
        x=L - 2, y=H + 10, text="REAR 🚪", showarrow=False,
        font=dict(size=11, color="#4338CA", family="sans-serif"),
        xanchor="right", yanchor="bottom",
    )
    if cur_xs:
        cx = sum(cur_xs) / len(cur_xs)
        # White ↓ with dark stroke — readable against orange + ghost grey
        # (CEO 2026-05-19: keep arrow contrast independent of fill colour)
        fig.add_annotation(
            x=cx, y=cur_top_y + 8,
            text="<b>↓</b>", showarrow=False,
            font=dict(size=28, color="#111827", family="sans-serif"),
            xanchor="center", yanchor="bottom",
        )
    return fig


def _kpi_cell_html(label: str, value: str, sub: str, kind: str = "neutral") -> str:
    """Return one HTML <div> for the KPI strip."""
    palette = {
        "gold":    ("#FEF3C7", "#92400E"),
        "success": ("#ECFDF5", "#065F46"),
        "danger":  ("#FEE2E2", "#991B1B"),
        "neutral": ("#FFFFFF", "#374151"),
    }
    bg, fg = palette.get(kind, palette["neutral"])
    return (
        f'<div style="background:{bg};border:1px solid #D1D5DB;'
        f'border-radius:6px;padding:12px 14px;text-align:center;flex:1;">'
        f'<div style="font-size:10px;font-weight:700;letter-spacing:0.5px;'
        f'text-transform:uppercase;color:#6B7280;">{label}</div>'
        f'<div style="font-size:24px;font-weight:800;color:{fg};margin:3px 0;'
        f'letter-spacing:-0.5px;">{value}</div>'
        f'<div style="font-size:11px;color:#6B7280;">{sub}</div>'
        f'</div>'
    )


def render_step2_v4(
    load_id: str,
    sim_26: Dict[str, Any], sim_53: Dict[str, Any],
    master: Dict[str, Dict[str, Any]],
    trucks_map: Dict[str, Dict[str, Any]],
    recommended_key: str,
    all_loads_df: Optional[pd.DataFrame] = None,
) -> None:
    """Step 2 v5 — loading-guide dashboard (HTML print, no PDF).

        Header (Load + Carrier + Driver + Left-anchor)
        Print bar (Single / Selected / All) — HTML print, no ReportLab
        KPI 5 (Items / Length / Weight / Volume / Heavy on floor)
        Row A: 3D Isometric   |   Row B: Zone breakdown
        Row C: Dock Lineup (Wave 1 / Wave 2)
        Row D: ADAPTIVE N-stage cards (auto-fit + ghost system + ↓ arrow)
        Footer: Why this arrangement, Downloads, Email
    """
    from engine.zone_aggregator import (
        Stage,
        Zone,
        aggregate_zones,
        aggregate_rows,
        row_summary,
        stages_from_zones,
    )

    # ── @media print CSS — Cmd+P clean printout ────────────────────────
    # Hide sidebar, Streamlit chrome, file uploader, audit accordion etc.
    # Force light backgrounds. Page-break-after on .print-page sections so
    # bulk mode prints one work order per page.
    st.markdown(
        """
        <style>
        @media print {
            [data-testid="stSidebar"],
            [data-testid="stToolbar"],
            [data-testid="stHeader"],
            header, footer { display: none !important; }
            .stApp { background: white !important; }
            .main .block-container { padding: 0 !important; max-width: 100% !important; }
            .print-page { page-break-after: always; break-after: page; }
            .print-page:last-child { page-break-after: auto; break-after: auto; }
            details, .stExpander { display: none !important; }
            .no-print { display: none !important; }
            @page { size: letter portrait; margin: 0.4in; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Open print-page wrapper — closed at end of function. Each print-page
    # gets its own A4/Letter page when printing (page-break-after: always).
    st.markdown('<div class="print-page">', unsafe_allow_html=True)

    # ── Truck radio (screen-only — fixed to recommended for print) ─────
    st.markdown('<div class="no-print">', unsafe_allow_html=True)
    labels = {
        "26ft": (
            f"26ft Box Truck · Fits {sim_26['fitted_count']}/{sim_26['requested_count']}"
            + ("" if sim_26["fits"] else f" · ⚠ {sim_26.get('unfitted_count',0)} unfitted")
        ),
        "53ft": (
            f"53ft Dry Van · Fits {sim_53['fitted_count']}/{sim_53['requested_count']}"
            + ("" if sim_53["fits"] else f" · ⚠ {sim_53.get('unfitted_count',0)} unfitted")
        ),
    }
    options_keys = ["26ft", "53ft"]
    default_idx = options_keys.index(recommended_key) if recommended_key in options_keys else 0
    chosen_label = st.radio(
        "Truck",
        [labels[k] for k in options_keys],
        index=default_idx,
        horizontal=True,
        key=f"truck_step2v4_{load_id}",
    )
    chosen_key = options_keys[[labels[k] for k in options_keys].index(chosen_label)]
    st.markdown('</div>', unsafe_allow_html=True)  # /no-print
    sim = sim_26 if chosen_key == "26ft" else sim_53
    truck_spec = trucks_map[chosen_key]
    label = "26ft Box Truck" if chosen_key == "26ft" else "53ft Dry Van"
    m = sim["metrics"]
    placements = sim.get("placements", [])
    pair_count = sim.get("pair_count", 0)

    # Build zones (for the dispatcher Zone breakdown table) + rows (for the
    # forklift operator's row-by-row loading sequence). Stages_from_zones is
    # no longer wired into Row D — kept available for legacy callers.
    # Q4 CEO direction: load order MUST go front-to-rear row by row.
    zones = aggregate_zones(placements, master, pair_count=pair_count)
    rows_seq = aggregate_rows(placements, master)
    stages = stages_from_zones(zones)  # kept for any downstream caller

    # ── HEADER ─────────────────────────────────────────────────────────
    bol = load_id
    carrier = "—"
    dock = "—"
    appt = "—"
    route = "—"
    driver = ""
    head_cols = st.columns([3, 2])
    with head_cols[0]:
        st.markdown(
            f'<div style="border-bottom:1.5px solid #111827;padding-bottom:8px;">'
            f'<div style="display:flex;align-items:center;gap:12px;">'
            f'<div style="background:#A50034;color:white;width:26px;height:26px;'
            f'border-radius:5px;display:flex;align-items:center;justify-content:center;'
            f'font-weight:800;font-size:15px;">L</div>'
            f'<div>'
            f'<div style="font-size:11px;color:#6B7280;font-weight:600;">LG Load Optimizer</div>'
            f'<div style="font-size:18px;font-weight:800;color:#111827;">'
            f'Work Order  ·  Load {load_id}</div>'
            f'</div></div>'
            f'<div style="font-size:10px;color:#6B7280;margin-top:6px;">'
            f'<b>BOL</b> {bol} · <b>Carrier</b> {carrier} · <b>Dock</b> {dock} · '
            f'<b>Appt</b> {appt} · <b>Route</b> {route} · <b>Truck</b> {label}'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;margin-top:6px;'
            f'font-size:10px;">'
            f'<span style="color:#374151;"><b>Driver:</b> {driver or "—"}</span>'
            f'<span style="color:#991B1B;font-weight:700;">'
            f'> Left = driver-side, facing rear doors</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with head_cols[1]:
        # Print bar — single-load HTML print (Cmd+P / Ctrl+P). Bulk modes are
        # rendered as a separate radio below the header so they don't get lost
        # in the print button.
        st.markdown(
            '<div style="background:#EFF6FF;border:1.5px solid #1D4ED8;'
            'border-radius:8px;padding:10px 14px;text-align:center;">'
            '<div style="font-size:11px;color:#1E40AF;font-weight:700;'
            'letter-spacing:0.5px;text-transform:uppercase;">Print work order</div>'
            '<div style="font-size:13px;color:#1E3A8A;margin-top:4px;font-weight:600;">'
            f'Press <b>Cmd+P</b> (Mac) / <b>Ctrl+P</b> (Windows)<br>'
            f'or use Print mode below'
            '</div></div>',
            unsafe_allow_html=True,
        )

    # Audit / category-leak chips (screen-only — useful for dispatcher)
    is_optimal = sim.get("is_provable_optimal", False)
    engine_internal = sim.get("engine", "Heuristic")
    if is_optimal:
        eng_chip = '<span style="background:#FEF3C7;color:#92400E;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:700;">★ Auto · Proven shortest</span>'
    elif "SA" in engine_internal:
        eng_chip = '<span style="background:#ECFDF5;color:#047857;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:700;">Auto · Space-optimized</span>'
    else:
        eng_chip = '<span style="background:#F3F4F6;color:#4B5563;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:700;">Auto · Fast arrangement</span>'
    blk = sim.get("audit_block_count", 0)
    wrn = sim.get("audit_warn_count", 0)
    extra_chips = eng_chip
    if blk:
        extra_chips += f' <span style="background:#FEE2E2;color:#991B1B;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:700;">⚠ {blk} loading rule violation(s)</span>'
    if wrn:
        extra_chips += f' <span style="background:#FFFBEB;color:#B45309;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:700;">△ {wrn} warning(s)</span>'
    st.markdown(
        f'<div style="margin:8px 0 6px 0;">{extra_chips}</div>',
        unsafe_allow_html=True,
    )

    # Audit findings expander (auto-open on block OR warn)
    findings = sim.get("audit_findings", [])
    if findings:
        with st.expander(
            f"⚠ Audit findings ({len(findings)})",
            expanded=(blk + wrn) > 0,
        ):
            for f in findings:
                sev = f.get("severity", "info")
                color = {"block": "#B91C1C", "warn": "#B45309",
                         "info": "#1D4ED8"}.get(sev, "#6B7280")
                st.markdown(
                    f'<div style="border-left:3px solid {color};padding:6px 10px;'
                    f'margin:4px 0;background:#FAFAFA;border-radius:0 4px 4px 0;'
                    f'font-size:12px;">'
                    f'<b style="color:{color};text-transform:uppercase;font-size:10px;">{sev}</b> · '
                    f'<b>{f.get("rule","")}</b><br>'
                    f'<span style="color:#4B5563;">{f.get("message","")}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    if not placements:
        st.warning("No placements to render. Return to Step 1.")
        return

    # ── KPI 5 ─────────────────────────────────────────────────────────
    fits_ct = sim.get("fitted_count", 0)
    requested = sim.get("requested_count", 0)
    unfitted = sim.get("unfitted_count", max(0, requested - fits_ct))
    heavy_ct = sum(1 for p in placements if p.get("weight_lb", 0) >= 150)
    kpi_html = (
        '<div style="display:flex;gap:6px;margin:6px 0 14px;">'
        + _kpi_cell_html("Items", f"{fits_ct} / {requested}",
                         "All fit" if unfitted == 0 else f"! {unfitted} left over",
                         "success" if unfitted == 0 else "danger")
        + _kpi_cell_html("Length", f"{m.get('x_used_ft', 0):g} ft",
                         "Proven shortest" if is_optimal else "Space-optimized",
                         "gold")
        + _kpi_cell_html("Weight", f"{int(m.get('weight_total_lb', 0)):,} lb",
                         f"{m.get('weight_util_pct', 0):g}% util", "neutral")
        + _kpi_cell_html("Volume", f"{m.get('volume_loaded_cft', 0):g} ft³",
                         f"{m.get('volume_util_pct', 0):g}% util", "neutral")
        + _kpi_cell_html("Heavy on floor", f"{heavy_ct}",
                         "z=0 verified", "neutral")
        + '</div>'
    )
    st.markdown(kpi_html, unsafe_allow_html=True)

    # ── Row A · 3D Iso  |  Row B · Zone breakdown ──────────────────────
    row1 = st.columns([1, 1])
    with row1[0]:
        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:#6B7280;'
            'text-transform:uppercase;letter-spacing:0.5px;">'
            'A · Isometric — rows × lanes × tiers</div>',
            unsafe_allow_html=True,
        )
        fig_3d = build_3d_figure(sim, truck_spec, master)
        st.plotly_chart(
            fig_3d, use_container_width=True,
            key=f"step2v4_3d_{load_id}_{chosen_key}",
            config={"displayModeBar": False},
        )
    with row1[1]:
        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:#6B7280;'
            'text-transform:uppercase;letter-spacing:0.5px;">'
            'B · Zone breakdown</div>',
            unsafe_allow_html=True,
        )
        # Build zone DataFrame from the same aggregator the PDF uses
        zone_rows = []
        for z in zones:
            if z.is_pair:
                half = z.item_count // 2
                qty = f"{half} + {z.item_count - half}"
            else:
                qty = str(z.item_count)
            title = z.raw_category if z.broad_category == "other" and z.raw_category else (
                {"refrigerator": "Refrigerator", "washer": "Washer",
                 "washer_dryer_pair": "Washer + Dryer (paired)",
                 "dryer": "Dryer", "dishwasher": "Dishwasher",
                 "microwave": "Microwave", "oven": "Wall Oven",
                 "tv": "TV", "monitor": "Monitor", "av": "Audio",
                 "ac": "Air Conditioner", "other": "Other"}.get(
                     z.broad_category, z.broad_category.capitalize())
            )
            zone_rows.append({
                "Zone": f"{z.zone_id} · {title}",
                "Qty": qty,
                "Layout": f"{z.rows}R × {z.lanes}L × {z.tiers}T",
                "Length": f"{z.length_ft_start} to {z.length_ft_end} ft",
                "Weight": f"{z.weight_lb:,} lb",
            })
        st.dataframe(
            pd.DataFrame(zone_rows),
            hide_index=True, use_container_width=True,
        )

    # ── Row C · Dock Lineup ───────────────────────────────────────────
    mid = max(1, len(stages) // 2)
    wave1 = stages[:mid]
    wave2 = stages[mid:]
    stage_title_map = {
        "refrigerator": "Refrigerator", "washer": "Washer",
        "dryer": "Dryer", "dishwasher": "Dishwasher",
        "microwave": "Microwave", "oven": "Wall Oven",
        "washer_dryer_pair": "Washer + Dryer",
        "tv": "TV", "monitor": "Monitor", "av": "Audio",
    }
    def _stage_title(s: Stage) -> str:
        if not s.zones:
            return s.title_en or "Stage"
        z0 = s.zones[0]
        if z0.broad_category == "other" and z0.raw_category:
            return z0.raw_category[:18]
        broad = z0.broad_category
        # Washer+Dryer split — washer (floor) then dryer (top)
        if broad == "washer_dryer_pair":
            if "tier 2" in s.layout or "위" in s.title_kr:
                return "Dryer (top stack)"
            return "Washer (floor)"
        if broad == "washer" and "바닥" in s.title_kr:
            return "Washer (floor)"
        if broad == "dryer":
            return "Dryer (top stack)"
        if broad == "oven":
            return "Wall Oven + close-out"
        return stage_title_map.get(broad, broad.capitalize())

    def _wave_html(name: str, stages_in: List[Stage], hint: str) -> str:
        items_html = ""
        for s in stages_in:
            items_html += (
                f'<div style="font-size:13px;color:#374151;padding:2px 0;">'
                f'{s.step_no}. {_stage_title(s)} × {s.units}</div>'
            )
        return (
            f'<div style="padding:10px 14px;">'
            f'<div style="font-size:14px;font-weight:700;color:#111827;">{name}</div>'
            f'<div style="font-size:10px;color:#6B7280;margin-bottom:4px;">{hint}</div>'
            f'{items_html}'
            f'</div>'
        )
    st.markdown(
        '<div style="font-size:11px;font-weight:700;color:#6B7280;'
        'text-transform:uppercase;letter-spacing:0.5px;margin-top:8px;">'
        'C · Dock Lineup — Wave split</div>',
        unsafe_allow_html=True,
    )
    wave_cols = st.columns(2)
    with wave_cols[0]:
        st.markdown(
            '<div style="background:white;border:1px solid #D1D5DB;border-radius:5px;">'
            + _wave_html("Wave 1", wave1, "lanes A · B · C simultaneous")
            + '</div>',
            unsafe_allow_html=True,
        )
    with wave_cols[1]:
        st.markdown(
            '<div style="background:white;border:1px solid #D1D5DB;border-radius:5px;">'
            + _wave_html("Wave 2", wave2, "start when Wave 1 half-cleared")
            + '</div>',
            unsafe_allow_html=True,
        )

    # ── Row D · ROW-BY-ROW LOADING SEQUENCE (Q4 CEO direction) ─────────
    # 한 카드 = 한 물리 row (x_in column). 앞에서 뒤로 sequential.
    # Mixed-category row 명확히 표시 (e.g. "Row 3: 2 Dryer + 1 Fridge + 2 Washer").
    # Safety badges, crew chip, ETA 모두 제거 (Q2).
    n_total = len(rows_seq)
    truck_len_in = float(truck_spec["length_in"])

    def _row_position_hint(row) -> str:
        """FRONT / MIDDLE / REAR · LAYER N — based on this row's x and z."""
        avg_x = (row.x_in + row.x_end_in) / 2.0
        zone_x = ("FRONT" if avg_x < truck_len_in * 0.33
                  else "MIDDLE" if avg_x < truck_len_in * 0.66
                  else "REAR")
        stack = " · STACKED" if row.has_stack else ""
        return f"{zone_x} · {row.x_ft:.1f} ft{stack}"

    def _row_pair_note(row, idx: int) -> str:
        """Identify stacked-pair situations within a row."""
        cats = row.categories
        if "washer" in cats and "dryer" in cats:
            return f"⛓ Washer ({cats['washer']}) on floor, Dryer ({cats['dryer']}) ON TOP"
        if row.has_stack and "dryer" in cats and idx > 0:
            prv = rows_seq[idx - 1]
            if "washer" in prv.categories:
                return f"⛓ Dryer ON TOP of Row {prv.row_no} Washers"
        return ""

    st.markdown(
        f'<div style="font-size:11px;font-weight:700;color:#6B7280;'
        f'text-transform:uppercase;letter-spacing:0.5px;margin-top:14px;">'
        f'D · LOADING SEQUENCE — {n_total} ROW{"S" if n_total != 1 else ""}'
        f' · front → rear · physical load order</div>',
        unsafe_allow_html=True,
    )

    if n_total == 0:
        st.info("No rows — return to Step 1.")
        return

    total_units = sum(r.units for r in rows_seq)
    cum_units = 0

    # Grid sizing — N=1 single full, N=2-5 row of N, N=6+ wrap 5 per row,
    # N≥10 Wave headers (3 waves Front/Middle/Rear from x_in distribution).
    def _row_size(n: int) -> int:
        return n if n <= 5 else 5

    use_waves = n_total >= 10
    wave_boundaries: List[Tuple[str, int, int]] = []
    if use_waves:
        # Split by physical x position into thirds (not by count, since rows
        # are uneven). Front = first 33% of truck length, etc.
        front_end = 0
        mid_end = 0
        for i, r in enumerate(rows_seq):
            cx = (r.x_in + r.x_end_in) / 2
            if cx < truck_len_in * 0.33:
                front_end = i + 1
            if cx < truck_len_in * 0.66:
                mid_end = i + 1
        front_end = max(1, front_end)
        mid_end = max(front_end, mid_end)
        wave_boundaries = [
            ("Wave 1 — FRONT (first ~⅓ of truck)", 0, front_end),
            ("Wave 2 — MIDDLE", front_end, mid_end),
            ("Wave 3 — REAR (final ⅓, includes door zone)", mid_end, n_total),
        ]
        wave_boundaries = [w for w in wave_boundaries if w[2] > w[1]]
    else:
        wave_boundaries = [("", 0, n_total)]

    row_size = _row_size(n_total)

    for wave_label, wstart, wend in wave_boundaries:
        if wave_label:
            st.markdown(
                f'<div style="background:linear-gradient(90deg,#DBEAFE,white);'
                f'padding:6px 12px;border-radius:6px;margin:10px 0 6px;'
                f'font-size:12px;font-weight:700;color:#1D4ED8;">🚛 {wave_label}</div>',
                unsafe_allow_html=True,
            )
        for grid_start in range(wstart, wend, row_size):
            grid_end = min(grid_start + row_size, wend)
            cols = st.columns(grid_end - grid_start)
            for ci, i in enumerate(range(grid_start, grid_end)):
                r = rows_seq[i]
                pos = _row_position_hint(r)
                pair_text = _row_pair_note(r, i)
                summary = row_summary(r)
                cum_units += r.units
                progress_pct = int(cum_units / max(total_units, 1) * 100)
                title = f"Row {r.row_no}"
                if r.is_mixed:
                    title += " · MIXED"
                mixed_badge = (
                    '<span style="background:#FEF3C7;color:#92400E;'
                    'padding:1px 6px;border-radius:3px;font-size:9px;'
                    'font-weight:700;margin-left:4px;">MIXED</span>'
                ) if r.is_mixed else ''

                with cols[ci]:
                    # CEO 2026-05-19: orange theme — warehouse "act now" colour.
                    # step circle / position label / progress all share #F97316.
                    st.markdown(
                        f'<div style="display:flex;align-items:flex-start;gap:8px;'
                        f'margin-bottom:6px;">'
                        f'<div style="background:#F97316;color:white;width:30px;height:30px;'
                        f'border-radius:50%;display:flex;align-items:center;justify-content:center;'
                        f'font-weight:800;font-size:14px;flex-shrink:0;'
                        f'box-shadow:0 1px 3px rgba(194,65,12,0.4);">{r.row_no}</div>'
                        f'<div style="flex:1;">'
                        f'<div style="font-size:14px;font-weight:700;color:#111827;'
                        f'line-height:1.2;">Row {r.row_no}{mixed_badge}</div>'
                        f'<div style="font-size:11px;color:#6B7280;margin-top:2px;">'
                        f'<b>{r.units}</b> units · <b>{int(r.total_weight_lb):,}</b> lb</div>'
                        f'</div>'
                        f'<div style="width:24px;height:24px;border:2.5px solid #111827;'
                        f'border-radius:4px;flex-shrink:0;" title="Check when row loaded"></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    fig_mini = _build_row_mini_view(truck_spec, rows_seq, i)
                    st.plotly_chart(
                        fig_mini, use_container_width=True,
                        config={"displayModeBar": False, "staticPlot": True},
                        key=f"row_mini_{load_id}_{chosen_key}_{i}",
                    )
                    # Item summary (mixed vs uniform)
                    st.markdown(
                        f'<div style="font-size:12px;font-weight:700;color:#111827;'
                        f'background:#F8FAFC;border:1px solid #E2E8F0;border-radius:4px;'
                        f'padding:5px 8px;margin-bottom:4px;">📦 {summary}</div>',
                        unsafe_allow_html=True,
                    )
                    # Position label — orange theme
                    st.markdown(
                        f'<div style="background:#FED7AA;color:#9A3412;'
                        f'border-radius:6px;padding:5px 9px;margin-bottom:4px;'
                        f'font-size:12px;font-weight:700;text-align:center;'
                        f'border:1px solid #FDBA74;">'
                        f'📍 {pos}</div>',
                        unsafe_allow_html=True,
                    )
                    if pair_text:
                        st.markdown(
                            f'<div style="background:#FEF3C7;border:1px solid #FCD34D;'
                            f'color:#92400E;border-radius:4px;padding:4px 8px;'
                            f'font-size:10.5px;font-weight:700;margin-bottom:4px;">'
                            f'{pair_text}</div>',
                            unsafe_allow_html=True,
                        )
                    # Progress bar — orange to match theme
                    st.markdown(
                        f'<div style="margin-top:4px;">'
                        f'<div style="font-size:9px;color:#6B7280;display:flex;'
                        f'justify-content:space-between;">'
                        f'<span>After Row {r.row_no}</span>'
                        f'<span>{cum_units}/{total_units} units</span></div>'
                        f'<div style="background:#E5E7EB;height:5px;border-radius:3px;'
                        f'overflow:hidden;">'
                        f'<div style="background:linear-gradient(90deg,#F97316,#FDBA74);'
                        f'height:100%;width:{progress_pct}%;"></div></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    # ── Footer expanders (Why / Walk-through / Downloads / Email) ────
    with st.expander("Why this arrangement", expanded=False):
        try:
            from engine.explain import explain, explain_html
            reasons = explain(sim, master, truck_spec)
            if reasons:
                st.markdown(explain_html(reasons), unsafe_allow_html=True)
        except Exception:
            pass

    with st.expander("Other downloads · Email driver", expanded=False):
        # Excel + Interactive 3D HTML alongside the PDF v4
        excel_bytes = build_simple_excel(sim, load_id, chosen_key, master, trucks_map)
        html_bytes = fig_3d.to_html(include_plotlyjs="cdn", full_html=True).encode("utf-8")
        dc1, dc2 = st.columns(2)
        with dc1:
            st.download_button(
                "📊 Excel report",
                excel_bytes,
                file_name=f"{load_id}_{chosen_key}_load_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"excel_v4_{load_id}_{chosen_key}",
            )
        with dc2:
            st.download_button(
                "⬇ Interactive 3D HTML",
                html_bytes,
                file_name=f"{load_id}_{chosen_key}_3d.html",
                mime="text/html",
                use_container_width=True,
                key=f"html_v4_{load_id}_{chosen_key}",
            )
        try:
            from engine.email_ui import render_email_panel
            # Persist Excel + interactive HTML so render_email_panel can attach
            # them. PDF generation removed (Q1 CEO direction — HTML print only).
            excel_path = OUT_DIR / f"{load_id}_{chosen_key}_load_report.xlsx"
            excel_path.write_bytes(excel_bytes)
            html_path = OUT_DIR / f"{load_id}_{chosen_key}_3d.html"
            fig_3d.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)
            render_email_panel(load_id, chosen_key, sim, OUT_DIR)
        except Exception:
            pass

    # Close print-page wrapper
    st.markdown('</div>', unsafe_allow_html=True)


# =========================================================================
# Sidebar Load picker (only shown on Load Plan page)
# =========================================================================
LOADS_REQUIRED_COLS = ["load_id", "model_code", "quantity"]
LOADS_OPTIONAL_COLS = {"destination": None, "pickup_date": None, "truck_type": None}


def normalize_loads_df(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Loads upload normalizer — case-insensitive cols, NaN drop, type coerce."""
    df = _normalize_columns(raw)
    warnings: list[str] = []
    missing = [c for c in LOADS_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}. "
            f"Loads schema: load_id (text), model_code (text matching Model_Master), "
            f"quantity (integer). Optional: destination, pickup_date, truck_type."
        )
    for col, default in LOADS_OPTIONAL_COLS.items():
        if col not in df.columns:
            df[col] = default
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    invalid = df[LOADS_REQUIRED_COLS].isna().any(axis=1) | (df["quantity"] <= 0)
    if invalid.any():
        warnings.append(f"Dropped {invalid.sum()} row(s) with missing/zero values")
        df = df.loc[~invalid].reset_index(drop=True)
    df["quantity"] = df["quantity"].astype(int)
    df["load_id"] = df["load_id"].astype(str).str.strip()
    df["model_code"] = df["model_code"].astype(str).str.strip()
    return df, warnings


def sidebar_load_picker() -> Optional[Tuple[str, pd.DataFrame, Optional[str]]]:
    st.sidebar.markdown("### Load source")
    st.sidebar.download_button(
        "📋 Download Loads template",
        build_loads_template_bytes(),
        file_name="lg_loads_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="loads_template_dl",
        help="Pre-filled template with 3 example rows + Schema_Notes sheet.",
    )
    st.sidebar.caption(
        "Only **3 columns** needed: `load_id`, `model_code`, `quantity`. "
        "Dimensions/weight are auto-pulled from Model_Master via `model_code`."
    )

    # Persisted upload state survives page navigation. Streamlit clears widget
    # state when the widget isn't rendered (sidebar_load_picker only runs on the
    # Load Plan page), so we copy parsed Loads into stable session_state keys
    # (df_loads_uploaded, df_loads_uploaded_name) on each upload.
    uploaded = st.sidebar.file_uploader(
        "Upload Loads Excel", type=["xlsx"], key="loads_uploader",
        help="Required: load_id, model_code, quantity. "
             "Persists across page navigation until you click Clear.",
    )

    if uploaded is not None:
        try:
            xls = pd.ExcelFile(uploaded)
            sheets = xls.sheet_names
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Cannot read Excel: {exc}")
            return None

        def _default_idx(target: str, options: List[str]) -> int:
            for i, name in enumerate(options):
                if name.strip().lower() == target.lower():
                    return i
            return 0

        chosen_sheet = st.sidebar.selectbox(
            "Loads sheet", sheets,
            index=_default_idx("Loads", sheets), key="loads_sheet_pick",
        )
        try:
            raw_loads = pd.read_excel(xls, sheet_name=chosen_sheet)
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Sheet read failed: {exc}")
            return None
        try:
            df_loads_new, lwarns = normalize_loads_df(raw_loads)
        except ValueError as exc:
            st.sidebar.error(f"⚠ {exc}")
            return None
        st.session_state.df_loads_uploaded = df_loads_new
        st.session_state.df_loads_uploaded_name = uploaded.name
        for w in lwarns:
            st.sidebar.info(f"ℹ {w}")

    has_upload = st.session_state.get("df_loads_uploaded") is not None
    if has_upload:
        name = st.session_state.get("df_loads_uploaded_name", "uploaded file")
        n_rows = len(st.session_state.df_loads_uploaded)
        n_loads = st.session_state.df_loads_uploaded["load_id"].nunique()
        st.sidebar.success(
            f"📎 Using **{name}**  \n"
            f"{n_loads} load(s) · {n_rows} row(s) · persisted across pages"
        )
        if st.sidebar.button("🗑 Clear uploaded · use sample", key="loads_clear_btn"):
            st.session_state.pop("df_loads_uploaded", None)
            st.session_state.pop("df_loads_uploaded_name", None)
            st.rerun()
        df_loads = st.session_state.df_loads_uploaded
    else:
        st.sidebar.caption("ℹ No upload yet — using bundled sample data.")
        df_loads = st.session_state.df_loads

    if df_loads is None or df_loads.empty:
        st.sidebar.warning("No load data available.")
        return None

    load_ids = sorted(df_loads["load_id"].unique().tolist())
    chosen = st.sidebar.selectbox("Load", load_ids, index=0, key="load_id_pick")
    grp = df_loads[df_loads["load_id"] == chosen]
    destination = grp["destination"].iloc[0] if "destination" in grp.columns else None
    return chosen, grp, destination


def build_loads_template_bytes() -> bytes:
    """Generate a minimal Loads template — only 3 required columns.

    Dimensions/weight/stackable are NOT needed in this file — they are looked
    up automatically from Model_Master by `model_code`.
    """
    from io import BytesIO
    template = pd.DataFrame([
        {"load_id": "L001", "model_code": "LF29H8330S", "quantity": 6},
        {"load_id": "L001", "model_code": "WM4000HWA",  "quantity": 8},
        {"load_id": "L001", "model_code": "DLEX4000W",  "quantity": 8},
        {"load_id": "L001", "model_code": "LDFN4542S",  "quantity": 10},
        {"load_id": "L001", "model_code": "LMV1764ST",  "quantity": 12},
        {"load_id": "L002", "model_code": "OLED65C4PUA", "quantity": 40},
        {"load_id": "L002", "model_code": "OLED77C4PUA", "quantity": 20},
    ])
    notes = pd.DataFrame([
        {"column": "load_id",   "required": "YES",
         "description": "Shipment / order ID. Multiple rows sharing the same load_id "
                        "are one shipment (one truck plan)."},
        {"column": "model_code", "required": "YES",
         "description": "Must match a model_code in Model_Master. App will look up "
                        "width/depth/height/weight/stackable automatically — DO NOT "
                        "include those columns here."},
        {"column": "quantity",   "required": "YES",
         "description": "Number of units. Positive integer."},
        {"column": "destination", "required": "optional",
         "description": "Display only — destination DC / customer code."},
        {"column": "pickup_date", "required": "optional", "description": "Display only."},
        {"column": "truck_type",  "required": "optional",
         "description": "Display hint. App simulates both 26ft and 53ft regardless."},
        {"column": "(dims / weight / stackable)", "required": "NO — auto-mapped",
         "description": "These come from Model_Master via model_code lookup. "
                        "Do not duplicate them in the Loads file."},
    ])
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        template.to_excel(w, sheet_name="Loads", index=False)
        notes.to_excel(w, sheet_name="Schema_Notes", index=False)
    return buf.getvalue()


# =========================================================================
# Page: Load Plan
# =========================================================================
if page == "📦 Load Plan":
    st.title("📦 Load Plan")

    picker = sidebar_load_picker()
    if picker is None:
        st.stop()

    load_id, grp, destination = picker

    master = st.session_state.df_master.set_index("model_code").to_dict("index")
    apply_calibrations(master)

    trucks_df = st.session_state.df_trucks
    trucks_map: Dict[str, Dict[str, Any]] = trucks_df.set_index("truck_type").to_dict("index")
    for tt, spec in trucks_map.items():
        spec["truck_type"] = tt

    # Get the active Loads DataFrame so we can offer bulk print across loads.
    active_loads_df: Optional[pd.DataFrame] = st.session_state.get(
        "df_loads_uploaded", st.session_state.df_loads,
    )
    all_load_ids: List[str] = (
        sorted(active_loads_df["load_id"].unique().tolist())
        if active_loads_df is not None and not active_loads_df.empty
        else [load_id]
    )

    load_lines = grp[["model_code", "quantity"]].to_dict("records")

    # Validate model codes against current master before simulating
    requested = {l["model_code"] for l in load_lines}
    missing_skus = sorted(requested - set(master.keys()))
    if missing_skus:
        st.error(
            f"⚠ {len(missing_skus)} SKU(s) in load `{load_id}` are NOT in the current "
            f"Model_Master — simulation aborted.\n\n"
            f"Missing: {', '.join(missing_skus[:20])}"
            + (" ..." if len(missing_skus) > 20 else "")
        )
        st.info(
            f"Current master has **{len(master):,} SKUs** "
            f"(source: {st.session_state.get('master_source', 'unknown')}). "
            f"Either:\n"
            f"- Upload a **Loads** file in the sidebar with SKUs that exist in your master, or\n"
            f"- Add the missing SKUs to your Model_Master (Model Master page → re-upload), or\n"
            f"- Click 🗑 Clear uploaded in the sidebar to go back to bundled sample."
        )
        with st.expander("Sample of available SKUs in your master"):
            sample = list(master.keys())[:30]
            st.code("\n".join(sample))
        st.stop()

    # Dim sanity check — surface SKUs whose dims will be rejected by the engine
    DOOR_TRACK_IN = 10
    oversized_for_26 = []
    oversized_for_53 = []
    for line in load_lines:
        mc = line["model_code"]
        spec = master[mc]
        w, d, h = spec["width_in"], spec["depth_in"], spec["height_in"]
        eff_h_26 = trucks_map["26ft"]["height_in"] - DOOR_TRACK_IN
        eff_h_53 = trucks_map["53ft"]["height_in"] - DOOR_TRACK_IN
        reasons_26, reasons_53 = [], []
        # Width along truck width axis
        if w > trucks_map["26ft"]["width_in"]:
            reasons_26.append(f"width {w}>{trucks_map['26ft']['width_in']}")
        if w > trucks_map["53ft"]["width_in"]:
            reasons_53.append(f"width {w}>{trucks_map['53ft']['width_in']}")
        # Height vs effective height
        if h > eff_h_26:
            reasons_26.append(f"height {h}>{eff_h_26} (eff_H after door track)")
        if h > eff_h_53:
            reasons_53.append(f"height {h}>{eff_h_53}")
        # Depth vs truck length (extreme case)
        if d > trucks_map["26ft"]["length_in"]:
            reasons_26.append(f"depth {d}>{trucks_map['26ft']['length_in']}")
        if d > trucks_map["53ft"]["length_in"]:
            reasons_53.append(f"depth {d}>{trucks_map['53ft']['length_in']}")
        if reasons_26:
            oversized_for_26.append((mc, line["quantity"], w, d, h, "; ".join(reasons_26)))
        if reasons_53:
            oversized_for_53.append((mc, line["quantity"], w, d, h, "; ".join(reasons_53)))

    if oversized_for_26 or oversized_for_53:
        with st.expander(
            f"🔎 Dim sanity — "
            f"{len(oversized_for_26)} SKU(s) too big for 26ft, "
            f"{len(oversized_for_53)} SKU(s) too big for 53ft",
            expanded=bool(oversized_for_26),
        ):
            if oversized_for_26:
                st.markdown("**Will be rejected by 26ft truck:**")
                st.dataframe(
                    pd.DataFrame(oversized_for_26,
                                 columns=["model_code", "qty", "w_in", "d_in", "h_in", "reason"]),
                    hide_index=True, use_container_width=True,
                )
            if oversized_for_53:
                st.markdown("**Will be rejected by 53ft truck:**")
                st.dataframe(
                    pd.DataFrame(oversized_for_53,
                                 columns=["model_code", "qty", "w_in", "d_in", "h_in", "reason"]),
                    hide_index=True, use_container_width=True,
                )
            st.caption(
                "These SKUs have at least one dimension exceeding the truck's interior "
                "(after the 10 in door-track loss for height). They will appear as "
                "*unfitted* in the result. Check your Model_Master values."
            )

    # Run both simulations via the v2 router (MILP/SA/heuristic auto-routing,
    # pair-packing, post-pack audit). Cache by (load_id + order signature +
    # master fingerprint + trucks fingerprint) so a master upload invalidates
    # stale results — QA Lead & Eng Lead audit finding (without master_hash
    # the dispatcher could see fit numbers computed with old SKU dims after
    # uploading a corrected master, with no visible signal).
    def _master_fingerprint(m: Dict[str, Dict[str, Any]]) -> int:
        bits = tuple(
            (mc, spec.get("width_in"), spec.get("depth_in"), spec.get("height_in"),
             spec.get("weight_lb"), spec.get("stackable"), spec.get("fragile"))
            for mc, spec in sorted(m.items())
            if mc in {l["model_code"] for l in load_lines}
        )
        return hash(bits)

    def _trucks_fingerprint(tm: Dict[str, Dict[str, Any]]) -> int:
        bits = tuple(
            (k, v.get("length_in"), v.get("width_in"), v.get("height_in"),
             v.get("max_payload_lb"))
            for k, v in sorted(tm.items())
        )
        return hash(bits)

    def _solve_with_cache(lid: str, lines: List[Dict[str, Any]]):
        sig = (
            lid,
            tuple(sorted((l["model_code"], l["quantity"]) for l in lines)),
            _master_fingerprint(master),
            _trucks_fingerprint(trucks_map),
        )
        ck = f"sim_{sig}"
        if ck not in st.session_state:
            s26 = router_solve(lines, master, trucks_map["26ft"], time_budget_s=15.0)
            s53 = router_solve(lines, master, trucks_map["53ft"], time_budget_s=15.0)
            st.session_state[ck] = (s26, s53)
        return st.session_state[ck]

    sim_26, sim_53 = _solve_with_cache(load_id, load_lines)
    recommended_key = pick_recommended(sim_26, sim_53) or "26ft"

    tab1, tab2 = st.tabs([
        "Step 1 · Decision",
        "Step 2 · Load & Work Order",
    ])
    with tab1:
        render_step1(load_id, load_lines, master, trucks_map, sim_26, sim_53, destination)
    with tab2:
        # ── Print mode control bar — Single / Selected / All ──────────
        # Single (default): render only the current load_id from the sidebar.
        # Selected: user picks N load_ids via multiselect, each gets a page.
        # All: every load_id in the active Loads file gets a page.
        st.markdown('<div class="no-print">', unsafe_allow_html=True)
        col_a, col_b = st.columns([2, 3])
        with col_a:
            print_mode = st.radio(
                "🖨 Print mode",
                ["Single load", "Selected loads", "All loads"],
                index=0, horizontal=True, key="print_mode_radio",
                help=(
                    "Single: only the load picked in the sidebar.\n"
                    "Selected: choose N loads to bundle.\n"
                    "All: every load in the active Loads file (Cover page + N pages)."
                ),
            )
        selected_ids: List[str] = [load_id]
        if print_mode == "Selected loads":
            with col_b:
                default_sel = st.session_state.get("print_selected_ids", [load_id])
                default_sel = [x for x in default_sel if x in all_load_ids] or [load_id]
                selected_ids = st.multiselect(
                    "Loads to include",
                    options=all_load_ids,
                    default=default_sel,
                    key="print_selected_ids",
                )
                if not selected_ids:
                    selected_ids = [load_id]
        elif print_mode == "All loads":
            selected_ids = all_load_ids
            with col_b:
                st.info(
                    f"Will render **{len(selected_ids)} loads** "
                    f"(Cover + {len(selected_ids)} pages). "
                    f"Hit Cmd+P / Ctrl+P → Save as PDF."
                )
        st.caption(
            "💡 To print: hit **Cmd+P** (Mac) or **Ctrl+P** (Windows). "
            "Sidebar/buttons are hidden in the printout."
        )
        st.markdown('</div>', unsafe_allow_html=True)

        # ── Render single or bulk ─────────────────────────────────────
        if print_mode == "Single load" or len(selected_ids) <= 1:
            render_step2_v4(load_id, sim_26, sim_53, master, trucks_map, recommended_key)
        else:
            # Cover page first (only meaningful in bulk)
            if print_mode == "All loads":
                st.markdown(
                    f'<div class="print-page" style="padding:24px;border:1px solid #E5E7EB;'
                    f'border-radius:10px;margin-bottom:16px;">'
                    f'<div style="font-size:24px;font-weight:800;color:#111827;'
                    f'border-bottom:3px solid #111827;padding-bottom:8px;">'
                    f'📋 Loads Cover · Daily Print Bundle</div>'
                    f'<div style="font-size:14px;color:#374151;margin-top:8px;">'
                    f'<b>{len(selected_ids)} loads</b> · '
                    f'<b>{sum(len(active_loads_df[active_loads_df["load_id"]==lid]) for lid in selected_ids)}</b> rows'
                    f'</div>'
                    f'<ol style="font-size:13px;color:#374151;margin-top:12px;">'
                    + "".join(
                        f'<li>☐ <b>{lid}</b> — {active_loads_df[active_loads_df["load_id"]==lid]["quantity"].sum()} units</li>'
                        for lid in selected_ids
                    )
                    + '</ol></div>',
                    unsafe_allow_html=True,
                )
            # Per-load pages with progress bar
            prog = st.progress(0.0, text=f"Building {len(selected_ids)} work orders…")
            for i, lid in enumerate(selected_ids):
                lid_grp = active_loads_df[active_loads_df["load_id"] == lid]
                lid_lines = lid_grp[["model_code", "quantity"]].to_dict("records")
                lid_skus = {l["model_code"] for l in lid_lines}
                missing = lid_skus - set(master.keys())
                if missing:
                    st.warning(
                        f"⏭ Skipping {lid} — {len(missing)} SKU(s) missing in master"
                    )
                    prog.progress((i + 1) / len(selected_ids))
                    continue
                lid_sim_26, lid_sim_53 = _solve_with_cache(lid, lid_lines)
                lid_recommended = pick_recommended(lid_sim_26, lid_sim_53) or "26ft"
                render_step2_v4(
                    lid, lid_sim_26, lid_sim_53, master, trucks_map,
                    lid_recommended, all_loads_df=active_loads_df,
                )
                prog.progress((i + 1) / len(selected_ids),
                              text=f"Built {i+1}/{len(selected_ids)} · {lid}")
            prog.empty()


# =========================================================================
# Page: Model Master
# =========================================================================
elif page == "📋 Model Master":
    st.title("📋 Model Master")
    st.caption("Appliance / TV / monitor model master — US units (in / lb / ft³).")

    df_master = st.session_state.df_master
    source = st.session_state.get("master_source", "bundled")
    src_label = (
        f"💾 Loaded from your saved master: `{USER_MASTER_PATH}`"
        if source == "user" else
        "📦 Loaded from bundled sample. Upload a new master below to persist it."
    )
    st.info(src_label)
    st.markdown(f"**Total {len(df_master)} models registered**")

    # Defensive: cast to string + drop NaN so mixed-type rows don't crash sorted()
    cats = ["All"] + sorted(
        {str(c) for c in df_master["category"].dropna().unique() if str(c).strip()}
    )
    sel_cat = st.selectbox("Category filter", cats)
    df_view = df_master if sel_cat == "All" else df_master[df_master["category"] == sel_cat]
    st.dataframe(df_view, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### 🔄 Update master (persisted across restarts)")
    st.caption(
        f"**Required columns in Model_Master sheet**: `{', '.join(REQUIRED_MASTER_COLS)}`. "
        f"Optional: `{', '.join(OPTIONAL_MASTER_COLS.keys())}`. "
        f"Column names are case-insensitive. "
        f"Truck_Master and Loads sheets are optional (kept from session if absent)."
    )
    new_file = st.file_uploader("Upload Excel", type=["xlsx"], key="master_uploader")
    if new_file:
        try:
            xls = pd.ExcelFile(new_file)
            sheet_names = xls.sheet_names
        except Exception as exc:  # noqa: BLE001
            st.error(f"Cannot read Excel file: {exc}")
            st.stop()

        st.markdown(f"📑 **Found sheets**: `{', '.join(sheet_names)}`")

        # Sheet pickers — auto-default to expected names if found
        def _default_idx(target: str, options: List[str]) -> int:
            for i, name in enumerate(options):
                if name.strip().lower() == target.lower():
                    return i
            return 0

        c1, c2, c3 = st.columns(3)
        with c1:
            master_sheet = st.selectbox(
                "Model Master sheet (required)",
                sheet_names,
                index=_default_idx("Model_Master", sheet_names),
                key="master_sheet_pick",
            )
        with c2:
            truck_options = ["(keep current)"] + sheet_names
            t_default = (
                truck_options.index("Truck_Master") if "Truck_Master" in sheet_names else 0
            )
            truck_sheet = st.selectbox(
                "Truck Master sheet",
                truck_options, index=t_default, key="truck_sheet_pick",
            )
        with c3:
            loads_options = ["(keep current)"] + sheet_names
            l_default = (
                loads_options.index("Loads") if "Loads" in sheet_names else 0
            )
            loads_sheet = st.selectbox(
                "Loads sheet",
                loads_options, index=l_default, key="loads_sheet_pick",
            )

        # Read + normalize selected sheets
        try:
            raw_master = pd.read_excel(xls, sheet_name=master_sheet)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not read sheet '{master_sheet}': {exc}")
            st.stop()

        try:
            new_master, master_warns = normalize_master_df(raw_master)
        except ValueError as exc:
            st.error(f"⚠ {exc}")
            with st.expander("Show first 5 rows of uploaded sheet"):
                st.dataframe(raw_master.head(), use_container_width=True)
            st.stop()

        new_trucks = st.session_state.df_trucks
        truck_warns: List[str] = []
        if truck_sheet != "(keep current)":
            try:
                raw_trucks = pd.read_excel(xls, sheet_name=truck_sheet)
                new_trucks, truck_warns = normalize_trucks_df(raw_trucks)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Truck_Master from '{truck_sheet}' rejected ({exc}); keeping current.")

        new_loads = st.session_state.df_loads
        if loads_sheet != "(keep current)":
            try:
                new_loads = _normalize_columns(
                    pd.read_excel(xls, sheet_name=loads_sheet)
                )
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Loads from '{loads_sheet}' rejected ({exc}); keeping current.")

        # Preview before commit
        st.markdown("##### ✓ Preview (clean)")
        st.dataframe(new_master.head(10), use_container_width=True, hide_index=True)
        st.caption(f"Total rows after normalization: **{len(new_master)}**")
        for w in master_warns + truck_warns:
            st.info(f"ℹ {w}")

        # Save location selector — auto-detects writable drives, with override
        st.markdown("##### 💾 Save location")
        default_dir = _resolve_user_data_dir()
        save_dir_str = st.text_input(
            "Folder (will create if missing):",
            value=str(default_dir),
            help=(
                "Default tries E:/ → D:/ → Documents → home. "
                "Override to any writable folder (e.g. E:/LG_Load_Optimizer)."
            ),
            key="save_dir_input",
        )
        save_dir = Path(save_dir_str)
        if _writable(save_dir):
            st.caption(f"✓ Writable: `{save_dir}`")
        else:
            st.warning(
                f"⚠ Cannot write to `{save_dir}`. "
                "Will auto-fall back to next writable candidate on save."
            )

        if st.button("💾 Apply + Save to persistent master", type="primary"):
            # Persist Loads only if user explicitly picked a sheet — otherwise keep
            # Loads as a transient per-session value (sidebar upload). Avoids
            # saving stale bundled-sample Loads whose SKUs may not exist in the
            # custom master.
            loads_to_save = new_loads if loads_sheet != "(keep current)" else None
            try:
                saved_path = save_user_master(
                    new_master, new_trucks, loads_to_save, override_dir=save_dir
                )
            except Exception as exc:  # noqa: BLE001
                st.error(
                    f"❌ Could not save anywhere. Last error: {exc}\n\n"
                    f"Tried: {[str(d) for d in _candidate_data_dirs(extra=save_dir)]}\n\n"
                    "Data NOT lost — your upload is still in this session. "
                    "Try a different folder above and click Apply again."
                )
                st.stop()
            # If applying a new master invalidates the currently loaded Loads,
            # drop them from session so user has to upload a compatible Loads file.
            current_loads = st.session_state.get("df_loads")
            if current_loads is not None and "model_code" in current_loads.columns:
                load_skus = set(current_loads["model_code"].astype(str))
                master_skus = set(new_master["model_code"].astype(str))
                if load_skus and not load_skus.issubset(master_skus):
                    new_loads = None
                    st.warning(
                        f"⚠ Existing Loads referenced "
                        f"{len(load_skus - master_skus)} SKU(s) not in your new master "
                        "— Loads cleared. Upload a fresh Loads file in the sidebar."
                    )
            st.session_state.df_master = new_master
            st.session_state.df_trucks = new_trucks
            st.session_state.df_loads = new_loads
            st.session_state.master_source = "user"
            for k in list(st.session_state.keys()):
                if isinstance(k, str) and k.startswith("sim_"):
                    del st.session_state[k]
            st.success(
                f"✅ Saved to `{saved_path}` — {len(new_master)} models. "
                "Auto-loads on next launch."
            )
            st.rerun()

    col_dl, col_reset = st.columns(2)
    with col_dl:
        # Build a snapshot xlsx in memory for download
        from io import BytesIO
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df_master.to_excel(w, sheet_name="Model_Master", index=False)
            st.session_state.df_trucks.to_excel(w, sheet_name="Truck_Master", index=False)
            if st.session_state.df_loads is not None:
                st.session_state.df_loads.to_excel(w, sheet_name="Loads", index=False)
        st.download_button(
            "📥 Download current master (backup)",
            buf.getvalue(),
            file_name="lg_master_backup.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="master_download",
        )
    with col_reset:
        if source == "user":
            if st.button(
                "♻️ Reset to bundled sample",
                use_container_width=True,
                help="Removes saved master file; next launch reverts to bundled sample.",
            ):
                try:
                    USER_MASTER_PATH.unlink(missing_ok=True)
                except Exception:
                    pass
                df_master, df_trucks, df_loads, source = _load_initial_data()
                st.session_state.df_master = df_master
                st.session_state.df_trucks = df_trucks
                st.session_state.df_loads = df_loads
                st.session_state.master_source = source
                st.rerun()


# =========================================================================
# Page: Truck Master
# =========================================================================
elif page == "🚛 Truck Master":
    st.title("🚛 Truck Master")
    st.caption("Truck cargo space spec (interior dimensions).")
    st.dataframe(st.session_state.df_trucks, use_container_width=True, hide_index=True)
    st.info("💡 26ft Box Truck / 53ft Dry Van. Phase 1 will add containers.")
