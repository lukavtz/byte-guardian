# Card enrichment with official sources

Status: implemented (2026-11-21)

Context: All 59 threats in `content/*.json` already carry authoritative `source` lines with official HTTPS URLs, validated by `tests/content/test_content.py::SOURCE_HOSTS`, but `app/ui/render.py::_card_html()` deliberately strips URLs via `_no_links()` and only prints organisation names in the footer. The LLM web-answer path already builds real source buttons but cards never reuse them. Decision: surface each card's validated official `source` URLs as tappable citation links and deepen card copy against those same official pages, bumping `content_version` so the idempotent seed updates rows. Consequences: every card becomes independently verifiable by the student; raw URLs stay out of the rich footer; no new hosts are trusted without a source-validation test; content changes flow through the existing seed/migration path. Alternatives: leave names only (rejected — the user complaint), render arbitrary LLM-generated source links (rejected — invented URLs risk), or add a web-search call per card (rejected — cards are offline and already sourced).

## Implementation notes

- `CardView` (`app/content/types.py`) carries the enrichment fields: `citations`, `mistakes`, `quick`, `updated`.
- `app/ui/render.py` shows at most two `https` citations in the footer (org name is the label, URL stays in the attribute; `_citations()` caps and filters the scheme), renders `mistakes` inside the collapsible `<details>` block, and prints `updated` in the footer.
- Coverage in `tests/ui/test_ui_render.py`: citation cap by org, non-https citations skipped, mistakes collapsed inside details, no mistakes block when empty, updated date in footer.
