"""i18n shim — quick checks."""
from __future__ import annotations

from engine.i18n import (
    available_locales,
    current_locale,
    set_locale,
    t,
)


def test_default_locale_is_english():
    set_locale("en")  # reset to known state
    assert current_locale() == "en"
    assert t("step1.title").startswith("Step 1")


def test_korean_translation():
    set_locale("ko")
    assert current_locale() == "ko"
    assert "단계" in t("step1.title")  # contains "단계"
    set_locale("en")


def test_unknown_locale_falls_back_to_english():
    set_locale("zh")  # not supported yet
    assert current_locale() == "en"
    assert t("step1.title").startswith("Step 1")


def test_missing_key_returns_key():
    set_locale("en")
    assert t("does.not.exist") == "does.not.exist"


def test_placeholder_substitution():
    set_locale("en")
    assert t("step1.fits_msg", n=42) == "All 42 items fit"
    set_locale("ko")
    assert t("step1.fits_msg", n=42) == "모든 42개 적재 가능"
    set_locale("en")


def test_available_locales_list():
    assert "en" in available_locales()
    assert "ko" in available_locales()
