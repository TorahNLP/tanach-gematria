# Tanakh Gematria Engine — Session Handoff

**Project:** `C:\Users\joshu.AKIVA\Desktop\tanakh-gematria`
**Live URL (site):** https://huggingface.co/spaces/TorahNLP/tanach-gematria
**Live URL (app / PWA install):** https://torahnlp-tanach-gematria.hf.space/?view=app
**Last pushed commit:** `e48a126` (Update handoff: PWA app view, colel exemptions, styling, gotchas)
**Handoff date:** 2026-07-19

> ⚠️ **Three commits are committed locally but NOT pushed** — production is still
> running `e48a126` and does not yet contain any of the 2026-07-19 (later session)
> work below. All of it is verified locally; none of it is verified live.
>
> | Commit | Contents |
> |--------|----------|
> | `3f1e329` | Word-span detail fix, Hebrew loader icon, streamlit pin |
> | `8345087` | Method picker before search, Ksiv-only app view, Track shown only when it varies |
> | `9b78ccf` | Search on type-and-click (no Enter needed) |
>
> On push: HF rebuilds in ~2–3 min. **This is the first rebuild against a pinned
> streamlit** — verify live before trusting it, and check the loader icon and app
> view specifically, since both touch Streamlit-internal DOM.

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

Plus one **pseudo-boundary**, `WordSpan` — not stored in the DB; see below.

---

## Two Faces, One Codebase (added 2026-07-19)

The same deployment serves two experiences, switched by URL parameter:

### Site view (default)
Five tabs: 📖 Guide & Sources · 1 Phrase & Name Matcher · 2 Scriptural Structural
Explorer · 3 Textual Echoes & Anomalies · 4 Macro Statistical Dashboard. Sidebar
with Sefaria-refs input and corpus panel. All widget tooltips.

### App view (`?view=app`) — the installable PWA
Deliberately minimal, phone-first:
- **No tabs.** A single page titled **"Tanach Gematria Search"** (old Tab 1).
- **Guide & Sources is a separate page** (`?view=app&page=guide`) reached by a
  button next to the title, with a "← Back to Gematria Search" button.
- **Tabs 2/3/4 do not exist** in app view — not hidden by CSS, *never created*:
  `tab2 = tab3 = tab4 = None` and their `with` blocks are guarded by
  `if tabN is not None:` with a two-space-indented `with` (avoids re-indenting
  ~300-line bodies). CSS hiding was tried first and failed — deployed Streamlit's
  DOM differs from local; don't reintroduce it.
- **Method dropdown**: all 34 methods, ordered `APP_CIPHER_ORDER` = classical
  (Talmud-attested) first: Standard, Katan, Gadol, Siduri, Atbash, Albam, Atbach,
  AchasBeta — then the rest. AchasBeta is classical (Shabbat 104a); AyakBachar was
  swapped out (its grid is later/kabbalistic).
- **No sidebar**, plus best-effort CSS hiding the collapsed-sidebar chevron.
- **No widget tooltips** — `_tip(text)` returns `None` when
  `st.query_params.get("view") == "app"`; Streamlit hover tooltips clip on phones.
- **Ksiv only** (added later 2026-07-19) — no reading-track selector at all; see
  "Reading tracks" below.

### PWA plumbing
- `static/manifest.json` — `start_url: "/?view=app"`, standalone, indigo theme.
- `static/icon-*.png` — gimel (ג) in David Bold on indigo; 192/512/maskable/180.
- `.streamlit/config.toml` — `server.enableStaticServing = true` plus
  `[theme]`/`[theme.dark]` (indigo primaryColor; needs streamlit ≥ 1.46).
- `_inject_pwa_head()` patches Streamlit's packaged `static/index.html` at import
  time. The Docker build step `RUN python app.py builddb` bakes the patch in.
  **Each snippet is wrapped in `<!--gem-*-start/end-->` delimiters and replaced on
  every run.** An earlier version keyed on a content marker and therefore only
  ever picked up *brand-new* snippets, never edits to an existing one — an
  already-patched venv kept serving stale loader CSS. Un-delimited blocks from
  older releases are stripped first, so there is exactly one of each.
- **Install must happen from the direct `.hf.space` URL** — the huggingface.co
  Spaces page iframes the app, so the manifest never reaches the top-level page
  and `?view=app` added there does NOT propagate into the iframe. This caused a
  false "app view is broken" alarm once; check the URL first.

---

## Word-Span Detail (fixed, `3f1e329`)

**The bug:** selecting a row in "All word-span matches" highlighted and scored the
*entire verse*. The call passed the literal boundary `"Verse"` and no
`matched_text`, so `sub_unit` was false and the span's word range was discarded
before the renderer ever saw it. Not an imprecision — the span never arrived.

**The fix:** `span_search` emits half-open word offsets `_w0`/`_w1` (underscore
columns are internal, dropped before display, so selection indices still address
`span_df`). A `WordSpan` pseudo-boundary carries them into `render_verse_detail`.

Three things that are easy to get wrong here:

1. **Highlight by index, not by consonant match.** `mark_word_span()` walks tokens
   and marks by position. A span's consonant string can recur in the same verse,
   and the existing `Word` branch marks only the first occurrence. The token walk
   must drop exactly what `tokenize_words()` drops — letter-less tokens and
   *standalone* paragraph markers (a marker fused onto a word is still one word) —
   or the highlight slides off by one. `word_span_token_count()` exists purely to
   pin that correspondence in tests.
2. **Word-boundary spacing must survive.** `w_cons` for a span is
   `" ".join(words[i0:i1])`, not a bare consonant join — Kaful / Mityashev /
   Meshulash compute differently without the spaces.
3. **Variant tracks need their own source text.** See below.

**Alignment contract:** DB `Word` units are built from `tokenize_words()` (see
`verse_forks`), so span offsets index into `tokenize_words(src_text)`. Anything
that changes tokenization changes span highlighting.

---

## Reading Tracks / Textual Variants (revised, `8345087`)

**Principle:** the reading tracks agree across the overwhelming majority of the
corpus. A `Track` column reading "Ksiv" on every row is noise — it poses a variant
question where none exists. **Mark a variant only where one actually exists.**

- `drop_uniform_track(df, app_view)` keeps the `Track` column only when the rows on
  screen actually contain a `Kri` or `TextVariant` reading. Applied to the phrase
  results, value-search results, span table, and the Tab 2 listing (applied
  *before* label mapping there, since the check reads raw track names).
- **`Aggregate` is not a reading tradition** — it's a storage tag for Perek/Parsha
  totals, auto-added to `effective_tracks` when those boundaries are selected. It
  never counts as a variant.
- **App view is Ksiv-only**: `tracks = ["Ksiv"]`, no selector rendered, Track column
  never shown.

**The TextVariant trap (cost a wrong first implementation):** `doublet_from` /
`doublet_to` are defined on **bare consonants** (e.g. `אחר` → `ואחר`) and usually
have **no counterpart in the cantillated text**, so substituting into `v.text`
silently does nothing — this is the fallback `verse_forks` already anticipates.
The fork instead substitutes **per word** over the consonant list. `render_verse_detail`
now mirrors that. Consequence, surfaced in the UI rather than left silent: on those
verses the cantillated line shows the Ksiv spelling while the values follow the
variant. Only 7 verses / 106 word units in the corpus, all reachable.

---

## Parsha → Sefer (resolved 2026-07-19)

`parsha` was never populated: it held the **book name on every row** —
571,521/571,521, 39 values for 39 books (`app.py` assigned `parsha=row["book"]`
at load). So the `Parsha` boundary had always been computing **whole-book
totals** while labelled "Torah portion".

Resolved by renaming rather than removing, since a book total is a legitimate
unit: **boundary `Parsha` → `Sefer`, labelled "Book (ספר)"**, aggregating on
`f.book` directly. The vestigial `parsha` column is no longer selected by any
query, and Tab 2 no longer lists or filters on it. `shape_result_columns` still
drops a `Parsha` column defensively for frames built elsewhere.

**This changed stored `boundary_type` values, so the DB must be rebuilt** —
Docker does it every build; locally `rm tanach.db && python app.py builddb`.
Verified after rebuild: 39 `Sefer` rows, 0 `Parsha` rows.

**Real parshiyot were never added, and probably shouldn't be as a boundary:**
weekly sedrot exist only for the Torah (54 portions over 5 books) with no
equivalent partition anywhere in Nevi'im or Ketuvim — haftarot are selections,
not a partition. In *Masoretic* usage "parashah" means an open/closed section,
which the corpus already exposes as the `Petucha` and `Setuma` boundaries. So a
real Parsha boundary would be Torah-only and largely redundant with what exists.

---

## TextVariant fork: half-verse splits (FIXED)

The doublet fork used to do three inconsistent things: substitute on the
**concatenated consonant string** (which can match across a word boundary or
inside the wrong word), replace in **every** matching word when building its word
list, and split halves at the Ksiv first-half **character** offset. The
substitution changes the string's length, so the split landed mid-word —
Genesis 18:5 FirstHalf ended `...עלעבדכ` and its SecondHalf opened on the
orphaned `ם`. 11 rows were affected; their half-verse cipher values were computed
on mis-split text.

Now `apply_doublet_to_words` substitutes **once, at word level**, and the fork's
consonants, halves and word list are all derived from that single result, so they
are consistent by construction. Halves split at a **word index** carried on the
fork (`fh_word_count`), which cannot drift. `render_verse_detail` calls the same
helper so display and DB cannot diverge again.

Verified: 0 display/consonant mismatches across all 571,521 rows; TextVariant
halves concatenate to their verse 7/7 and are all spaced; and the Ksiv derivation
is provably unchanged — old and new half word-spacing agree on all 23,206 verses.

---

## ⚠️ OPEN BUG: `sub_id` is not unique

**142,635 duplicate `sub_id` values** out of 571,521 rows. `_base_id` builds the
prefix from the first letter of each word in the book name, so every
single-word book starting with the same letter collides: `E_5_3_Ksiv_FH` is
shared by **Exodus, Ezekiel, Ecclesiastes, Esther and Ezra**. `J_4_9_*` rows
collide six ways.

Consequences:
- The `SubID` column shown in site results does not identify a row.
- **Any analysis keyed on `sub_id` silently merges unrelated books.** This
  produced a false regression signal once: a before/after snapshot keyed on
  `sub_id` appeared to show a changed cipher value when it had simply kept a
  different book's row on each build.

Not fixed. The fix is to make `_base_id` disambiguate (full book name, or a
book index), which changes every `sub_id` and needs a DB rebuild. Until then,
**key on `(book, chapter, verse, boundary_type, variant_track)`**, never on
`sub_id`.

---

## Spaced Result Text (`_display_form`)

`text_display` holds the **word-spaced** form so a match is legible in the result
table without opening the verse panel; `consonants` remains the unspaced string
every cipher and lookup runs on. Result queries select `text_display AS Text`.

`_display_form` enforces `display.replace(" ", "") == consonants` at insert time
rather than trusting it — that assertion is what caught the fork bug above.
Word units pass no `word_cons` (a single word needs no spacing) and fall back
harmlessly. 216,139 of 571,521 rows carry spacing; the rest are single words.

Note: `matched_text` passed to `render_verse_detail` is now spaced, which is safe
because every consumer runs it through `strip_to_consonants` first.

**Changing this requires a DB rebuild** — `rm tanach.db && python app.py builddb`.

---

## Result Table Shaping (`shape_result_columns`)

Row order and count are never touched, so dataframe selection indices still
address the source frame — every caller relies on that.

- **Parsha** — always dropped (see above).
- **Value** — dropped for a single-method table, where the heading states it and
  every row matches. **Kept when colel is on**, since ±1 makes each row's value
  meaningful again.
- **App view only** — Book/Chapter/Verse collapse to one `Amos 3:5` reference
  column, and SubID is dropped, to save phone width. Handles both column
  spellings (`Chapter`/`Verse` and span_search's `Ch`/`Vs`).

Also app-view only: **no "Cleaned consonants" readout**, and the computed-values
table sits at the bottom, just above cross-method coincidences, rather than
directly under the results heading.
`TODO(site)`: decide whether the full site still wants the cleaned-consonants
readout — currently kept there, dropped only in app view.

**App view is Ksiv-only, and the guide now says so.** The "Variant tracks"
section is not rendered in app view (guarded with a two-space-indented `with`,
the same trick the tab guards use, so its ~40-line body keeps its indentation);
an info box states the scope instead. Default text units in app view are
**Verse + Word** only.

**Planned (not built): replace reading tracks with a variants toggle.** Instead
of a track multiselect, search the relatively few places that actually have a
Kri or textual variant and flag them in an extra column when applicable — the
way colel is surfaced. This would let the app stop being Ksiv-only without the
current noise.

**Guide wording:** the phrase "all 34 methods" was removed throughout — the 34
are the methods implemented here, not a complete catalogue of the tradition, and
the methods expander now says so.

---

## Loading Icon (added, `3f1e329`)

Streamlit's "running man" status icon is replaced by Hebrew letters (22 base + 5
finals) reshuffling every 140 ms, rejecting any letter shown in the last 4 ticks —
pure random draws the same glyph twice often enough to read as a frozen spinner.

- Lives in `_LOADER_HEAD_SNIPPET`, injected into `<head>`, **not** a component:
  components render in a sandboxed iframe that cannot reach the parent DOM (the
  same restriction that blocks `window.print()`).
- Targets `[data-testid="stStatusWidget"]`, a Streamlit **internal** id with no
  stability promise. **This is why streamlit is now pinned.**
- Failure mode is cosmetic: no widget found, no letters, app unaffected.

---

## Search Submission (fixed, `9b78ccf`)

The Search button used to stay disabled until you pressed Enter. `st.text_input`
only sends its value to the server on **Enter or blur**, so mid-typing `raw` was
still `""` server-side, `cons` with it, and the button carried `disabled=not cons`.

Removing `disabled=` alone would have been worse: with a bare button the first
click merely blurs the input to commit the text and is itself swallowed — two
clicks. The input and its button now sit in an **`st.form`**, whose submit button
collects current widget values in one round trip. Enter still submits (built-in
form behaviour). Empty submits are now possible, so they warn rather than no-op.

**The colel toggle stays outside the form on purpose** — inside it, toggling would
not take effect until the next submit, where today it re-runs the search
immediately. Same reasoning applies to anything else added near that input.

---

## Method Picker (moved, `8345087`)

The "Show matches for method(s)" multiselect rendered only inside the
committed-search branch, so methods couldn't be chosen until after a search. It now
sits with the track/unit filters and is available on first load. Gematria-value
mode has no picker — it searches every method by design.

---

## Colel Semantics

- Toggle label is **כולל (±1)** (Tab 1 both modes + Tab 3).
- `COLEL_EXEMPT` frozenset — ±1 is NOT applied to:
  - `KololEhad` / `KololOtiyot` — kolel is built in (stacking = double-count)
  - `KatanMispari` — digital root, 9 values; ±1 spans a third of the space
  - `HaMerubahKlali` — squared total is non-additive; (S+1)² ≠ S²+1
  - `HaNekudot` — all mark values even (dot=10, line=6); ±1 can never match
- Enforced in `search_value`, `count_value`, `search_value_all_methods`, and
  `_xm_count_matrix`. Documented in the Guide's Colel expander and toggle help.

---

## Styling

- `[theme]`/`[theme.dark]`: indigo accent (#4F46E5 light / #A5B4FC dark), auto
  light/dark follows the viewer.
- Markdown content renders in **Noto Serif Hebrew** (Google Fonts @import in
  `run_app`), matching the print view; UI chrome stays sans. Dataframes are
  canvas-rendered — CSS fonts don't reach them.

---

## Earlier Fixes Still Relevant

- **MAQAF nikud false-positive fixed** (`bd9de9a`): nikud detection tests
  membership in `NIKUD_VALS`, not the codepoint range that included U+05BE.
- Print / Save PDF + Download HTML per match (iOS: Download HTML is the only path;
  `window.print()` is blocked in the components iframe).
- AyakBachar hundreds-tier finals, boundary checks, trailing-paragraph flush, etc.
  (12-bug Opus audit, `56632f0`).

---

## Known Issues / Gotchas

| Item | Status |
|------|--------|
| **Unpushed work** | `3f1e329`, `8345087`, `9b78ccf` are local only. Production runs `e48a126`. |
| **Dataframes are canvas-rendered** | **DOM assertions cannot see any table contents** — cell text never reaches the accessibility tree. A Playwright check for a column's presence will pass whether the column is right or hidden always. Test table *contents* at the data layer; use the browser only for surrounding UI. This nearly produced a false pass on the Track-column work. |
| `streamlit` pinned to `1.58.0` | Pinned deliberately (`3f1e329`): the loader icon and app-view layout target internal test ids (`stStatusWidget`, `stSidebarCollapsedControl`). Upgrade only with a live re-verify of both. |
| Local `.venv` missing `plotly` | Tab 4 throws locally (`ModuleNotFoundError: plotly`). It IS in `requirements.txt`, so Docker is fine. `pip install plotly` to fix locally. |
| `use_container_width` deprecation | Streamlit warns it is removed after 2025-12-31 — sweep to `width=`. The pin buys time, not immunity. |
| HF platform 500s | 2026-07-19 saw a platform-wide HF edge outage (all popular Spaces 500ing; container logs clean). Symptoms: blank first load, "Failed to fetch dynamically imported module", works after refresh. Nothing app-side. |
| Local `tanach.db` staleness | Schema-versioned by nothing — a stale one throws `no such column`. If local Tab 3 errors, delete `tanach.db` and run `python app.py builddb`. Docker always builds fresh. |
| Transliteration search | Not built. |
| On-screen Hebrew keyboard | Still commented out (`_KBD_KEY`). |
| Auto-nikud for typed input | Deferred; design in Claude memory (corpus lookup first, tiny ONNX nakdan fallback). |
| Word-span detail never UI-clicked | Verified hard at the data layer (410 rows, both tracks), but selecting a row means clicking canvas coordinates, so no browser test covers it. Worth one manual click after deploy. |

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Entire app (corpus load, ciphers, PWA + loader head patch, both views) |
| `.streamlit/config.toml` | Static serving + indigo theme |
| `static/manifest.json`, `static/icon-*.png` | PWA assets (served at `/app/static/`) |
| `fetch_corpus.py` | One-time corpus builder |
| `tanach_corpus.jsonl` | Corpus data (committed) |
| `tanach.db` | SQLite cache (generated; gitignored) |
| `Dockerfile` | HF Spaces build; builddb step bakes DB + head patch |
| `requirements.txt` | **streamlit pinned** — see gotchas |
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

---

## Verification Pattern

Local browser checks via Playwright driving installed Edge (`channel="msedge"`, no
browser download): boot streamlit on a spare port, assert on DOM. Recreate with
`pip install playwright` if needed.

**What the browser can and cannot tell you:**
- ✅ Widget presence/absence, labels, tab counts, button enabled state, whether a
  search returned results, the loader letters (a real DOM node).
- ❌ **Anything inside a dataframe** — canvas-rendered, invisible to the DOM. Test
  those at the data layer against `tanach.db` + the corpus jsonl.

**Driving the search in a test:** `fill()` sets the DOM value but the form needs a
real submit. Type with `type()`, then click the submit button — no Enter required
(that is now the product behaviour under test).

**Always re-verify live after deploy.** Local pass ≠ deployed pass; the pin reduces
that gap but the app-view DOM differences that broke CSS tab hiding were only ever
visible in production.

---

## Session Log (2026-07-19, newest first)

*Local, unpushed:*
- `9b78ccf` Search on type-and-click (st.form), no Enter needed
- `8345087` Method picker before search; Ksiv-only app view; Track only when it varies
- `3f1e329` Word-span detail scores the span not the verse; Hebrew loader icon; streamlit pinned

*Pushed:*
- `e48a126` Handoff update (PWA app view, colel exemptions, styling, gotchas)
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
