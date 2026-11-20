from __future__ import annotations

from app.services.i18n import DEFAULT_LOCALE, t


def test_renders_every_locale() -> None:
    for locale in ("en", "ru", "zh"):
        assert "Demo" in t(locale, "start.welcome", bot="Demo")


def test_missing_language_falls_back_to_default() -> None:
    assert t("de", "start.welcome", bot="Demo") == t(
        DEFAULT_LOCALE, "start.welcome", bot="Demo"
    )


def test_unknown_key_returns_key() -> None:
    assert t("en", "missing.key") == "missing.key"


def test_every_catalog_has_the_same_keys() -> None:
    import json
    from pathlib import Path

    from app.services.i18n import ENABLED_LOCALES, LOCALES

    root = Path(__file__).resolve().parent.parent / "app" / "i18n"
    catalogs = {locale: json.loads((root / f"{locale}.json").read_text(encoding="utf-8")) for locale in LOCALES}
    reference = set(catalogs[DEFAULT_LOCALE])
    for locale in ENABLED_LOCALES:
        assert set(catalogs[locale]) == reference, f"{locale} keys drifted"


def test_admin_button_is_translated_everywhere() -> None:
    for locale in ("en", "ru", "zh", "tt"):
        text = t(locale, "button.admin")
        assert text != "button.admin" and text
