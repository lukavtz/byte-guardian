# Asset layout

Status: accepted

Context: `assets/` mixes the topical directories documented in `assets/README.md` (`brand/`, `art/`, `readme/`, `botfather/`) with two loose, tracked, unreferenced duplicates — `assets/chat.png` (1.7 MB) and `assets/lang.png` (1.5 MB) — while the accepted JPEGs live in `assets/art/`. Grep finds no code or doc reference to the PNGs. Decision: delete the loose PNGs and keep only the topical directories as the single layout; the README/menu banner stays in `assets/brand/banner.jpg` untouched. Consequences: smaller repo, one obvious home per asset, no stale duplicate that could be served by mistake; an asset that is not in a topical directory is now an error to fix, not a tolerated exception. Alternatives: keep duplicates for convenience (rejected — reintroduces stale-image risk and violates the project's topical-directory convention), or move them into `art/` (rejected — they are unreferenced old renders, not canonical art).
