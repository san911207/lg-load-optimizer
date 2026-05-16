"""
Minimal i18n shim — English (default) + Korean.
================================================

Streamlit does not ship with a translation framework, so the app uses a
plain dictionary lookup with a fallback to English. Only the most
user-visible labels are translated — error messages, dataframe column
names, and developer-facing strings stay in English so screenshots are
easier to grep.

Usage::

    from engine.i18n import t, set_locale, available_locales
    st.markdown(t("step1.title"))
    set_locale("ko")  # switch globally for the rest of the run
"""
from __future__ import annotations

from typing import Dict, List

LOCALES: Dict[str, Dict[str, str]] = {
    "en": {
        # ── Top bar / navigation ──
        "brand.name": "LG Load Optimizer",
        "lang.toggle": "Language",

        # ── Step 1 ──
        "step1.title": "Step 1 of 2 · Decision",
        "step1.subtitle": "Pick the truck",
        "step1.load_summary": "Load summary",
        "step1.items": "Items",
        "step1.categories": "Categories",
        "step1.volume": "Volume",
        "step1.weight": "Weight",
        "step1.recommended": "Recommended",
        "step1.alternative": "Alternative",
        "step1.linear_length": "Linear length",
        "step1.length_used": "Length used",
        "step1.volume_util": "Volume util",
        "step1.weight_util": "Weight util",
        "step1.fits_msg": "All {n} items fit",
        "step1.oversize_msg": "{n} ft wasted · oversized for this load",
        "step1.cost": "Cost",
        "step1.save_msg": "Save ${amt}/day",
        "step1.see_3d": "See 3D view · Step 2 →",

        # ── Step 2 ──
        "step2.title": "Step 2 of 2 · Load & Work Order",
        "step2.engine.optimal": "★ Provably optimal",
        "step2.engine.refined": "Refined",
        "step2.engine.heuristic": "Heuristic",
        "step2.audit.block": "BLOCK",
        "step2.audit.warn": "warning",
        "step2.audit.findings": "Audit findings",
        "step2.pair_count": "pair(s)",
        "step2.why": "💡 Why this arrangement",
        "step2.export_pdf": "🖨 Print PDF work order",
        "step2.export_excel": "📊 Excel report",
        "step2.export_html": "⬇ Interactive 3D HTML",

        # ── Reasons (used by explain.py via t-keys) ──
        "reason.heavy_bottom": "Heavy items on bottom",
        "reason.tall_front": "Tall columns to front",
        "reason.pairs": "Washer + Dryer pairs grouped",
        "reason.optimal": "Mathematically optimal",
        "reason.refined": "Refined by simulated annealing",
        "reason.heuristic": "Heuristic packing",
        "reason.audit_block": "Audit BLOCKs detected",
        "reason.audit_warn": "Audit warnings",
        "reason.all_pass": "All rules pass",
    },
    "ko": {
        # ── 상단바 ──
        "brand.name": "LG 적재 옵티마이저",
        "lang.toggle": "언어",

        # ── Step 1 ──
        "step1.title": "1단계 · 의사결정",
        "step1.subtitle": "트럭 선택",
        "step1.load_summary": "적재 요약",
        "step1.items": "물품 수",
        "step1.categories": "카테고리",
        "step1.volume": "부피",
        "step1.weight": "무게",
        "step1.recommended": "추천",
        "step1.alternative": "대안",
        "step1.linear_length": "리니어 길이",
        "step1.length_used": "사용 길이",
        "step1.volume_util": "부피 활용",
        "step1.weight_util": "무게 활용",
        "step1.fits_msg": "모든 {n}개 적재 가능",
        "step1.oversize_msg": "{n}ft 낭비 · 과대 트럭",
        "step1.cost": "비용",
        "step1.save_msg": "$ {amt}/일 절약",
        "step1.see_3d": "3D 뷰 보기 · 2단계 →",

        # ── Step 2 ──
        "step2.title": "2단계 · 적재 작업 지시서",
        "step2.engine.optimal": "★ 수학적 최적해",
        "step2.engine.refined": "정제됨",
        "step2.engine.heuristic": "휴리스틱",
        "step2.audit.block": "차단",
        "step2.audit.warn": "경고",
        "step2.audit.findings": "감사 결과",
        "step2.pair_count": "쌍",
        "step2.why": "💡 이 배치를 선택한 이유",
        "step2.export_pdf": "🖨 PDF 작업 지시서 인쇄",
        "step2.export_excel": "📊 엑셀 리포트",
        "step2.export_html": "⬇ 인터랙티브 3D HTML",

        # ── 이유 ──
        "reason.heavy_bottom": "무거운 물품 바닥 적재",
        "reason.tall_front": "긴 물품 앞쪽 배치",
        "reason.pairs": "세탁기 + 건조기 짝지음",
        "reason.optimal": "수학적으로 최적",
        "reason.refined": "Simulated Annealing 정제 완료",
        "reason.heuristic": "휴리스틱 적재",
        "reason.audit_block": "감사 차단 항목 감지",
        "reason.audit_warn": "감사 경고",
        "reason.all_pass": "모든 룰 통과",
    },
}

DEFAULT_LOCALE = "en"
_current_locale = {"value": DEFAULT_LOCALE}


def available_locales() -> List[str]:
    return list(LOCALES.keys())


def set_locale(code: str) -> None:
    """Switch the global locale. Unknown codes silently fall back to English."""
    if code in LOCALES:
        _current_locale["value"] = code
    else:
        _current_locale["value"] = DEFAULT_LOCALE


def current_locale() -> str:
    return _current_locale["value"]


def t(key: str, **kwargs) -> str:
    """
    Look up ``key`` in the current locale; fall back to English; finally
    return the key itself so missing translations are obvious in QA.

    ``kwargs`` are passed to ``str.format`` so placeholders like
    ``"{n} units"`` substitute cleanly.
    """
    loc = _current_locale["value"]
    val = LOCALES.get(loc, {}).get(key)
    if val is None:
        val = LOCALES["en"].get(key, key)
    try:
        return val.format(**kwargs) if kwargs else val
    except (KeyError, IndexError):
        return val
