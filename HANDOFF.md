# Tanakh Gematria Engine — Session Handoff

**Project:** `C:\Users\joshu.AKIVA\Desktop\tanakh-gematria`
**Live URL (site):** https://huggingface.co/spaces/TorahNLP/tanach-gematria
**Live URL (app / PWA install):** https://torahnlp-tanach-gematria.hf.space/?view=app
**Last pushed commit:** `78f0da5` (App view: title reads 'Tanach Gematria Search')
**Handoff date:** 2026-07-19

---

## What This Is

A single-file Streamlit app (`app.py`) serving a gematria analysis engine over the
full Tanakh (23,206 cantillated Masoretic verses, corpus from Sefaria). The corpus
loads into an in-memory SQLite database on startup via `@st.cache_resource`, with a
pre-built `tanach.db` baked into the Docker image for fast cold starts. Deployed on
HuggingFace Spaces (`sdk: docker`); push to `main` auto-deploys (~2–3 min rebuild).

**34 ciphers** in families: Direct-value, Substitution (temurah), Name-expansion
(Milui/Neelam/Emtzaiyot ± Maleh), Positional, Vowel-mark (nikud), Combined,
Sequential/Kolel.

**Boundary types:** `Word / ZakefPhrase / TiphchaPhrase / FirstHalf / SecondHalf /
Verse / Petucha / Setuma / Perek / Parsha`

---

## Two Faces, One Codebase (added 2026-07-19)

The same deployment serves two experiences, switched by URL parameter:

### Site view (default, unchanged)
Five tabs: 📖 Guide & Sources · 1 Phrase & Name Matcher · 2 Scriptural Structural
Explorer · 3 Textual Echoes & Anomalies · 4 Macro Statistical Dashboard. Sidebar
with Sefaria-refs input and corpus panel. All widget tooltips.

### App view (`?view=app`) — the installable PWA
Deliberately minimal, phone-first:
- **No tabs.** A single page titled **"Tanach Gematria Search"** (old Tab 1).
- **Guide & Sources is a separate page** (`?view=app&page=guide`) reached by a
  button next to the title, with a "← Back to Gematria Search" button. Guide copy
  is app-specific (describes only the search; points to the full site for the rest).
- **Tabs 2/3/4 do not exist** in app view — not hidden by CSS, *never created*:
  `tab2 = tab3 = tab4 = None` and their `with` blocks are guarded by
  `if tabN is not None:` with a two-space-indented `with` (avoids re-indenting
  ~300-line bodies). CSS hiding was tried first and failed — deployed Streamlit's
  DOM differs from local; don't reintroduce it.
- **Method dropdown**: all 34 methods, but ordered `APP_CIPHER_ORDER` = classical
  (Talmud-attested) first: Standard, Katan, Gadol, Siduri, Atbash, Albam, Atbach,
  AchasBeta — then the rest. (An "Advanced methods" toggle existed briefly and was
  removed as clutter.) Note: AchasBeta is in the classical set (Shabbat 104a);
  AyakBachar was swapped out (its grid is later/kabbalistic).
- **No sidebar** (both sidebar blocks skipped; extra Sefaria refs forced to ""),
  plus best-effort CSS hiding the collapsed-sidebar chevron.
- **No widget tooltips** — `_tip(text)` helper returns `None` when
  `st.query_params.get("view") == "app"`; Streamlit hover tooltips clip on phones.
  All `help=` args in app-view-reachable widgets are wrapped in `_tip(...)`.

### PWA plumbing
- `static/manifest.json` — `start_url: "/?view=app"`, standalone, indigo theme.
- `static/icon-*.png` — gimel (ג) in David Bold on indigo; 192/512/maskable/180.
  Regeneration script exists in session scratchpad (PIL, Windows fonts).
- `.streamlit/config.toml` — `server.enableStaticServing = true` (assets at
  `/app/static/...`) plus `[theme]`/`[theme.dark]` (indigo primaryColor; auto
  light/dark preserved; needs streamlit ≥ 1.46).
- `_inject_pwa_head()` in app.py patches Streamlit's packaged `static/index.html`
  with manifest link + iOS meta tags at import time. Idempotent; the Docker build
  step `RUN python app.py builddb` bakes the patch into the image.
- **Install must happen from the direct `.hf.space` URL** — the huggingface.co
  Spaces page iframes the app, so the manifest never reaches the top-level page
  and `?view=app` added there does NOT propagate into the iframe. This caused a
  false "app view is broken" alarm once; check the URL first.

---

## Colel Semantics (added 2026-07-19)

- Toggle label is now **כולל (±1)** (Tab 1 both modes + Tab 3).
- `COLEL_EXEMPT` frozenset — the ±1 tolerance is NOT applied to:
  - `KololEhad` / `KololOtiyot` — kolel is built into the method (stacking = double-count)
  - `KatanMispari` — digital root, 9 possible values; ±1 spans a third of the space
  - `HaMerubahKlali` — squared total is non-additive; (S+1)² ≠ S²+1
  - `HaNekudot` — all mark values even (dot=10, line=6), ±1 can never match within-method
- Enforced in `search_value`, `count_value`, `search_value_all_methods`, and
  `_xm_count_matrix`. Documented in the Guide's Colel expander and toggle help.
- Verified: Standard+colel matches v−1/v/v+1; exempt methods stay exact even with
  the toggle on, including in the UNION-all search.

---

## Styling (added 2026-07-19)

- `[theme]`/`[theme.dark]`: indigo accent (#4F46E5 light / #A5B4FC dark), auto
  light/dark follows the viewer.
- Markdown content (verses, breakdowns, prose) renders in **Noto Serif Hebrew**
  (Google Fonts @import injected in `run_app`), matching the print view; UI chrome
  stays sans. Dataframes are canvas-rendered — CSS fonts don't reach them.

---

## Earlier Fixes Still Relevant

- **MAQAF nikud false-positive fixed** (`bd9de9a`): nikud detection now tests
  membership in `NIKUD_VALS` instead of the codepoint range that included U+05BE.
- Print / Save PDF + Download HTML per match (iOS: Download HTML is the only path;
  `window.print()` is blocked in the components iframe).
- AyakBachar hundreds-tier finals, boundary checks, trailing-paragraph flush, etc.
  (12-bug Opus audit, `56632f0`).

---

## Known Issues / Gotchas

| Item | Status |
|------|--------|
| HF platform 500s | 2026-07-19 saw a platform-wide HF edge outage (all popular Spaces 500ing; container logs clean). Symptoms: blank first load, "Failed to fetch dynamically imported module", works after refresh. Nothing app-side to fix. |
| `requirements.txt` unpinned streamlit | Docker installs latest at each build; local .venv has 1.58.0. This divergence broke CSS-based tab hiding once. **Recommended: pin streamlit.** (Not yet done.) |
| Local `tanach.db` staleness | The disk cache is schema-versioned by nothing — a stale one throws `no such column` (seen 2026-07-19, rebuilt since). If local Tab 3 errors, delete `tanach.db` and run `python app.py builddb`. Docker always builds fresh. |
| Transliteration search | Not built. |
| On-screen Hebrew keyboard | Still commented out (`_KBD_KEY`). |
| Auto-nikud for typed input | Deferred; design saved in Claude memory (corpus lookup first, tiny ONNX nakdan fallback). |
| `use_container_width` deprecation warnings | Streamlit will remove it after 2025-12-31 per runtime logs — sweep to `width=` eventually. |

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Entire app (corpus load, ciphers, PWA head patch, both views) |
| `.streamlit/config.toml` | Static serving + indigo theme |
| `static/manifest.json`, `static/icon-*.png` | PWA assets (served at `/app/static/`) |
| `fetch_corpus.py` | One-time corpus builder |
| `tanach_corpus.jsonl` | Corpus data (committed) |
| `tanach.db` | SQLite cache (generated; gitignored) |
| `Dockerfile` | HF Spaces build (`sdk: docker`); builddb step bakes DB + head patch |
| `BUILD.md` | Build/deploy notes |

---

## Deployment

```bash
cd "C:\Users\joshu.AKIVA\Desktop\tanakh-gematria"
git add <files> && git commit -m "..." && git push   # → HF rebuild ~2–3 min
```

Remote `space` → https://huggingface.co/spaces/TorahNLP/tanach-gematria, branch `main`.
HF API status: `GET https://huggingface.co/api/spaces/TorahNLP/tanach-gematria`
(runtime.stage). Run logs (SSE, needs HF token — git credential helper has one):
`/api/spaces/TorahNLP/tanach-gematria/logs/run`.

## Verification Pattern Used This Session

Local browser checks via Playwright driving installed Edge
(`channel="msedge"`, no browser download): boot streamlit on :8599, assert on
DOM (tab count, tooltip icons, sidebar presence). A scratchpad venv held
playwright; recreate with `pip install playwright` if needed. Remember: local
pass ≠ deployed pass while streamlit is unpinned — verify live after deploy.

## Session Log (2026-07-19, newest first)

- `78f0da5` App title → "Tanach Gematria Search"
- `7626242` App view: no sidebar
- `e5ca3ee` App view: suppress widget tooltips (`_tip`)
- `2627fa0` Colel per-method exemptions + Hebrew label
- `aaf8326` App view: search-only page, Guide as separate page, toggle removed
- `c8475bc` App view tabs made structural (no CSS hiding)
- `6e85fe0` App-view Guide trim
- `9cc560e` App view opens on matcher, unnumbered labels
- `58f59f7` Indigo theme + serif Hebrew content
- `f7dec04` AchasBeta in classical set, AyakBachar out
- `80a81e5` PWA app mode (manifest, icons, head patch, static serving)
- `bd9de9a` MAQAF nikud detection fix (2026-07-18)
