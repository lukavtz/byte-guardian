"""Schema validation for the shipped threat catalogue (content/*.json)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"
CATEGORIES = {"fire", "terror", "tech", "nature", "medical", "chem", "rad", "cyber"}
LIST_FIELDS = ("signs", "before", "during", "after", "forbidden")
TEXT_FIELDS = ("title", "summary", *LIST_FIELDS, "contacts", "source")
MAX_SUMMARY = 300
MAX_ITEM = 160
MIN_ITEMS, MAX_ITEMS = 2, 5
# Only official Russian sources: a card must be explainable by a page the
# student can open without an account. Subdomains are part of the domain
# (www.cbr.ru, static-0.minzdrav.gov.ru, 23.mchs.gov.ru).
SOURCE_HOSTS = frozenset(
    {
        "mchs.gov.ru",
        "minzdrav.gov.ru",
        "takzdorovo.ru",
        "rospotrebnadzor.ru",
        "nac.gov.ru",
        "digital.gov.ru",
        "cbr.ru",
        "gosuslugi.ru",
        "government.ru",
        "rosatom.ru",
        "meteonfo.ru",
        "gibdd.ru",
        "fsb.ru",
        "rostekhnadzor.gov.ru",
        "мвд.рф",
        "xn--b1aew.xn--p1ai",
    }
)
HTTPS_URL = re.compile(r"https://[^\s<>\"']+")
LINK_CHECK_ENV = "BYTEBOT_LINK_CHECK"


def _cards() -> list[tuple[Path, dict]]:
    paths = [CONTENT / f"{slug}.json" for slug in sorted(CATEGORIES)]
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def _threats() -> list[tuple[str, dict]]:
    return [(p.stem, t) for p, card in _cards() for t in card["threats"]]


def test_eight_categories_present() -> None:
    slugs = {p.stem for p in CONTENT.glob("*.json") if p.stem != "aliases"}
    assert slugs == CATEGORIES


def test_every_file_parses_and_matches_category() -> None:
    assert _cards(), "content/*.json is empty"
    for path, card in _cards():
        assert isinstance(card.get("version"), int), f"{path.name}: version is not int"
        assert card["version"] >= 1, f"{path.name}: version must be >= 1"
        category = card["category"]
        assert category["slug"] == path.stem, f"{path.name}: category.slug mismatch"
        assert category["title"]["ru"].strip(), f"{path.name}: empty category title"
        assert isinstance(category["sort"], int), f"{path.name}: sort is not int"
        assert category["emoji"].strip(), f"{path.name}: empty emoji"
        assert card["threats"], f"{path.name}: no threats"


def test_threat_slugs_unique_and_prefixed() -> None:
    seen: set[str] = set()
    for file_slug, threat in _threats():
        slug = threat["slug"]
        assert slug.startswith(file_slug + "-"), f"{slug}: not prefixed with {file_slug}-"
        assert slug not in seen, f"{slug}: duplicate slug"
        seen.add(slug)


def test_severity_is_1_to_3() -> None:
    for slug, threat in _threats():
        assert threat["severity"] in (1, 2, 3), f"{slug}: bad severity {threat['severity']!r}"


def test_ru_texts_are_complete() -> None:
    for slug, threat in _threats():
        texts = threat["texts"]["ru"]
        assert set(texts) >= set(TEXT_FIELDS), f"{slug}: missing keys {set(TEXT_FIELDS) - set(texts)}"
        assert texts["title"].strip(), f"{slug}: empty title"
        assert texts["source"].strip(), f"{slug}: empty source"
        assert 0 < len(texts["summary"]) <= MAX_SUMMARY, f"{slug}: summary length {len(texts['summary'])}"


def _source_urls() -> list[tuple[str, str]]:
    """Every https URL in every localized source line, with its threat key."""
    found: list[tuple[str, str]] = []
    for slug, threat in _threats():
        for locale, texts in threat["texts"].items():
            for url in HTTPS_URL.findall(texts["source"]):
                found.append((f"{slug}/{locale}", url.rstrip(".,;:")))
    return found


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _allowed(host: str) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in SOURCE_HOSTS)


def test_every_source_cites_an_allowlisted_https_page() -> None:
    for slug, threat in _threats():
        for locale, texts in threat["texts"].items():
            urls = HTTPS_URL.findall(texts["source"])
            assert urls, f"{slug}/{locale}: source has no https URL"
            for url in urls:
                host = _host_of(url.rstrip(".,;:"))
                assert _allowed(host), f"{slug}/{locale}: host {host!r} is not allowlisted ({url})"


@pytest.mark.skipif(
    os.environ.get(LINK_CHECK_ENV) != "1",
    reason=f"set {LINK_CHECK_ENV}=1 to check live source links",
)
def test_source_links_answer_200() -> None:
    """Opt-in network check: HEAD every unique source URL, expect 200."""
    failures: list[str] = []
    for url in sorted({url for _key, url in _source_urls()}):
        request = Request(
            url, method="HEAD", headers={"User-Agent": "ByteBot-link-check/1.0"}
        )
        try:
            with urlopen(request, timeout=20) as response:
                status = response.status
        except HTTPError as exc:
            status = exc.code
        except OSError as exc:
            status = str(exc)
        if status != 200:
            failures.append(f"{status} {url}")
    assert not failures, "source links failed:\n" + "\n".join(failures)


def test_ru_lists_are_bounded() -> None:
    for slug, threat in _threats():
        texts = threat["texts"]["ru"]
        for field in LIST_FIELDS:
            items = texts[field]
            assert isinstance(items, list), f"{slug}.{field}: not a list"
            assert MIN_ITEMS <= len(items) <= MAX_ITEMS, f"{slug}.{field}: {len(items)} items"
            for item in items:
                assert isinstance(item, str) and item.strip(), f"{slug}.{field}: blank item"
                assert len(item) <= MAX_ITEM, f"{slug}.{field}: item length {len(item)}"
        assert texts["contacts"], f"{slug}: empty contacts"
        assert all(isinstance(c, str) and c.strip() for c in texts["contacts"]), f"{slug}: blank contact"


def test_quizzes_are_complete() -> None:
    for slug, threat in _threats():
        quiz = threat["quiz"]["ru"]
        assert len(quiz) == 3, f"{slug}: {len(quiz)} questions"
        assert [q["id"] for q in quiz] == ["q1", "q2", "q3"], f"{slug}: bad question ids"
        for question in quiz:
            qid = f"{slug}/{question['id']}"
            assert question["prompt"].strip(), f"{qid}: empty prompt"
            options = question["options"]
            assert len(options) == 4, f"{qid}: {len(options)} options"
            assert len(set(options)) == 4, f"{qid}: duplicate options"
            assert all(isinstance(o, str) and o.strip() for o in options), f"{qid}: blank option"
            answer = question["answer"]
            assert isinstance(answer, int) and not isinstance(answer, bool), f"{qid}: answer not int"
            assert 0 <= answer <= 3, f"{qid}: answer {answer} out of range"
            assert question["explanation"].strip(), f"{qid}: empty explanation"


def test_aliases_reference_known_threats() -> None:
    aliases = json.loads((CONTENT / "aliases.json").read_text(encoding="utf-8"))
    known = {threat["slug"] for _, threat in _threats()}
    assert set(aliases) >= {"ru", "en"}, "aliases need ru and en sections"

    covered: set[str] = set()
    for locale, mapping in aliases.items():
        assert mapping, f"aliases.{locale}: empty"
        for alias, slugs in mapping.items():
            assert alias.strip() == alias and alias, f"aliases.{locale}: bad alias {alias!r}"
            assert isinstance(slugs, list) and slugs, f"aliases.{locale}.{alias}: empty list"
            assert len(set(slugs)) == len(slugs), f"aliases.{locale}.{alias}: duplicate slug"
            for slug in slugs:
                assert slug in known, f"aliases.{locale}.{alias}: unknown slug {slug}"
            if locale == "ru":
                covered.update(slugs)

    for slug in known:
        count = sum(slug in slugs for mapping in aliases.values() for slugs in mapping.values())
        assert count >= 3, f"{slug}: only {count} aliases"
        assert slug in covered, f"{slug}: no ru aliases"


def test_aliases_include_inflections() -> None:
    ru = json.loads((CONTENT / "aliases.json").read_text(encoding="utf-8"))["ru"]
    for alias in ("пожара", "пожаром"):
        assert alias in ru, f"missing inflected alias {alias}"
        assert ru[alias], f"{alias}: empty slug list"


@pytest.mark.parametrize("alias", ["пожар", "fire"])
def test_aliases_hit_expected_category(alias: str) -> None:
    aliases = json.loads((CONTENT / "aliases.json").read_text(encoding="utf-8"))
    slugs = aliases["ru"].get(alias, aliases["en"].get(alias))
    assert slugs, f"{alias}: not found"
    assert all(slug.startswith("fire-") for slug in slugs), f"{alias}: non-fire slugs {slugs}"


# "Quick" must carry one decisive, immediately doable action — not only a
# phone number. A call-only quick is one made entirely of numbers, call
# verbs, and separators in any of the four shipped locales.
CALL_WORDS = frozenset(
    {
        "позвоните", "звоните", "вызовите", "звонить",
        "call", "dial", "phone",
        "拨打", "拨",
        "шалтыратыгыз", "чакырыгыз", "телефонына", "номер",
    }
)
MAX_CALL_ONLY_SHARE = 0.30
MIN_QUICK_WORDS = 6
URL_IN_QUICK = re.compile(r"https?://|www\.", re.IGNORECASE)


def _quick_entries() -> list[tuple[str, str]]:
    """Every localized quick, labelled slug/locale, with its text."""
    return [
        (f"{slug}/{locale}", text["quick"])
        for slug, threat in _threats()
        for locale, text in threat["texts"].items()
        if "quick" in text
    ]


def _is_call_only(quick: str) -> bool:
    tokens = re.findall(r"[^\W\d_]+|\d+", quick, flags=re.UNICODE)
    return not [t for t in tokens if not (t.isdigit() or t.casefold() in CALL_WORDS)]


def test_quick_carries_an_action_not_only_a_number() -> None:
    entries = _quick_entries()
    assert entries, "no quick entries found"
    share = sum(_is_call_only(quick) for _, quick in entries) / len(entries)
    assert share < MAX_CALL_ONLY_SHARE, f"{share:.1%} of quicks are call-only (limit <30%)"


def test_quick_is_long_enough_and_has_no_url() -> None:
    for label, quick in _quick_entries():
        length = len(quick.replace(" ", "")) if label.endswith("/zh") else len(quick.split())
        assert length >= MIN_QUICK_WORDS, f"{label}: quick has {length} units ({quick!r})"
        assert not URL_IN_QUICK.search(quick), f"{label}: quick contains a URL ({quick!r})"
