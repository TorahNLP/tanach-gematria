# Plan — Search by verse reference (+ both-sides translation)

**Status:** revised after three independent reviews, 2026-08-03. Ready to build.
**Branch for docs:** `docs` (this file). Code lands on `main`.

> **Review outcome: the first draft was NOT safe to implement.** Three reviewers
> (technical feasibility, product/UX, correctness) independently returned
> "revise first". Two of my own claims were measurably wrong and are corrected
> below; one shipping correctness bug and one licence-condition bug were found
> that the draft would have introduced. Read "What the reviews changed" before
> building.

---

## What this is

A third search mode: instead of typing Hebrew or a number, name a verse — or a
part of one — and search on *its* value. The inverse of Tab 1's existing flow.

| Mode | Entry | Path |
|---|---|---|
| Hebrew text (exists) | type `שלום` | text → value → matches |
| Gematria value (exists) | type `376` | value → matches |
| **Verse reference (new)** | pick `Genesis 1:1` | reference → text → value → matches |

**It is not a Tab 2 replacement.** Tab 2 is a *browser*: pick a boundary type,
get a 23,206-row table, scroll or filter by book, click a row. This is a
*lookup*: name the reference directly. They overlap in outcome, not in use —
Tab 2 answers "show me all verses and their values", this answers "what equals
Bereishis 1:1?". Tab 2 stays as it is.

---

## Where it goes

**Tab 1's existing mode radio** (`app.py` ~5122):

```python
mode = st.radio("Search by", ["Hebrew text", "Gematria value"], ...)
```

becomes three options with `"Verse reference"` added. One implementation serves
**both app view and the site** — no duplicated code, and it lands in the tab
where results already render. Tab 2 is untouched.

---

## UI: cascading selects, not a single list

"Kendo dropdown" is a commercial JS component library — unavailable here, and
the Artifact CSP would block it regardless. The equivalent native pattern:

1. **Book** — `st.selectbox`, 39 options.
2. **Chapter** — `st.selectbox`, filtered to that book.
3. **Verse** — `st.selectbox`, filtered to that chapter.
4. **Unit** — what part of the verse to search on (below).

Plus a **free-text reference box** (`Genesis 1:1`, `בראשית א:א`) as a faster
path for anyone who knows the reference. Parse to `(book, chapter, verse)` and
drive the same selects. Sefaria-style refs are what the corpus is keyed on.

### The unit selector

**Measured 2026-08-03 against the built `tanach.db`** (an earlier draft of this
plan claimed whole-verse searches would "match almost nothing" — that was
**wrong**, and the correction matters):

| Query boundary | Units | Share a Standard value with ≥1 other unit |
|---|---|---|
| Verse | 23,206 | **97.2%** |
| FirstHalf | 23,206 | 99.6% |
| TiphchaPhrase | 81,629 | 99.9% |
| ZakefPhrase | 107,086 | ~100% |
| Word | 305,495 | 100% |

For a **Verse** query specifically: **median 17 other matching units**, mean
32.4, 10th percentile 3, and only **2.8% (641 verses) return nothing**.

The error was reasoning that verse values are near-unique *among verses* —
true, and why Tab 3's echo detection is interesting — without checking against
the full population. Matching runs against all **599,617** stored units of
every boundary type, so a verse total readily lands on some word, phrase or
half elsewhere in Tanach.

**Consequences for the design:**

- The zero-match case is a genuine but **minor** edge (2.8%), not the headline
  risk. Handle it gracefully; do not distort the UI around it.
- The unit selector is a **feature, not a mitigation** — offering
  **Whole verse · First half · Second half · Tipcha phrase · Zakef phrase ·
  Single word**, plus a **word range** (reuses the existing `WordSpan`
  pseudo-boundary and `_w0`/`_w1` offsets).
- Still show the selected unit's own values across all 35 methods — useful in
  its own right, and it covers the 2.8% case for free.

---

## Translation — the real gap

The bundled translation is **Koren** (JPS 1917 fills Joshua 21:36–37). There is
only one edition; "both translations" means **both sides of the comparison** —
the query verse's English and the matched verse's English.

**Current state, verified 2026-08-03:**

- `render_verse_detail` (~4310) renders a "Show English translation" checkbox
  for the **matched** unit. Tab 2 already gets this, since it calls the same
  function at 5496 and 5569 — so the earlier claim that Tab 2 lacks
  translations was wrong; it has the matched side.
- `build_print_html` takes a **single** `english=` parameter (~3030), fed only
  from the matched unit.
- Tab 2 *does* already thread a full `query_info` (`raw`/`cons`/`wcons`/`label`,
  ~5563), so the **calculation** is two-sided there. Only the **English** is
  one-sided.

**So the work is:**

1. Add `query_english` (+ `query_english_is_full_verse`) to
   `build_print_html`, rendered under the "Your Word" / "Selected Unit"
   section — mirroring how `query_breakdown`/`query_val` already pair with the
   match side.
2. Add an optional `query_english` to `render_verse_detail` so the panel can
   show both on screen, under one checkbox governing both.
3. Feed it from the new verse mode, **and from Tab 2**, where the query is
   already a verse and the field has simply never existed. This is a genuine
   fix to shipped code, not only new-feature scaffolding.

**Licence note:** Koren is CC-BY-NC and attribution is a licence condition. Two
translations in one document still need exactly one attribution block —
`ENGLISH_ATTRIBUTION` already renders in the export; do not duplicate it per
section.

---

## Edit points

| File / area | Change |
|---|---|
| `app.py` ~5122 | Add `"Verse reference"` to the Tab 1 mode radio |
| `app.py` Tab 1 body | New branch: cascading selects + ref parser + unit selector |
| `app.py` `render_verse_detail` ~4042 | Accept `query_english`, render both sides under one checkbox |
| `app.py` `build_print_html` ~3030 | Accept `query_english`, `query_english_is_full_verse`; render in the query section |
| `app.py` Tab 2 ~5563 | Populate `query_english` for the selected unit (fixes existing one-sidedness) |
| `app.py` `verse_english` ~3853 | Already exists; reuse, no change expected |
| Guide | Document the new mode; note that whole-verse searches often have no match |
| README | Only if the translation contract changes |

**New helper wanted:** `parse_verse_ref(text) -> (book, chapter, verse) | None`,
handling English and Hebrew book names plus `1:1` / `א:א`. Self-testable, so
put assertions in `run_selftest`.

---

## Risks and constraints

- **No DB change.** Every boundary this needs is already stored, and the
  translation is deliberately *not* in `tanach.db` (so a translation refresh
  never forces a cipher rebuild). **No `builddb` required** — unlike the 35th
  method.
- **Dataframes are canvas-rendered.** Verify result *contents* at the data
  layer against `tanach.db`; the browser only proves surrounding UI.
- **Streamlit reruns on every widget change.** Three cascading selects mean up
  to three reruns before a search. ⚠️ **Do NOT put them in an `st.form`** — an
  earlier draft of this plan suggested that, and it is self-defeating: widgets
  inside a form do not report their values until submit, so the Chapter select
  could never filter on the Book just chosen. Cascading selects must sit
  outside the form. The existing `st.form` at `app.py:4764` wraps only the
  Hebrew text input inside the `mode == "Hebrew text"` branch, so there is no
  conflict. Keep each lookup cheap instead (the book→chapter→verse index is
  small and `@st.cache_data`-able).
- **App view is Ksiv-only.** The verse mode must not surface track selection in
  app view; follow the existing `app_view` gating.
- **Joshua 21:36–37 are disputed** and flagged by `disputed_verse_note()`. A
  verse *picker* makes them directly selectable for the first time — make sure
  the note still renders on that path.

---

## What the reviews changed

### Blocking correctness issues found (must be built in, not bolted on)

**B1 — `nikud_partial` has NO query side. This is the serious one.**
Every gate is a corpus-side SQL predicate (`nikud_partial_clause` at
`app.py:699-714`; `where.append("nikud_partial = 0")` at 2365, 2397, 2421,
2469). `has_unpointed_word` (1747) is called only at build time (2061).
Nothing checks the *query*.

**1,107 Verse rows, 1,273 Word rows and 672 FirstHalf rows are flagged.** If a
user picks Deuteronomy 7:9 as the query under `HaNekudot`, the query value is
computed with no gate — a knowably-short number, or for the `Im*` pair a number
*identical to Standard*. The app then searches that phantom value and returns
corpus units whose own totals are complete. That is a screenful of fabricated
matches, violating Joshua's explicit rule ("not flagged-but-shown, not shown as
0"). Worse: `build_print_html` clears `query_breakdown` only when
`ksiv_unpointed` is true, and that flag is *match*-derived — so the export would
render a per-mark table rebuilding the withheld total.

*Required:* compute `has_unpointed_word` on the query unit and **suppress the
four vowel-mark methods from the verse-mode method picker entirely** when it is
set. Add `query_nikud_unreliable` to `build_print_html` (it has no such
parameter today).

**B2 — the CC-BY-NC attribution would print twice.** `ENGLISH_ATTRIBUTION`
renders at exactly one site, `app.py:3144`, *inside* the `if english:` block in
sec2. Copying that block for the query side duplicates the licence notice.
*Required:* hoist the attribution to one document-level site before adding a
second translation block.

**B3 — placement must not re-break `sec1_warn`.** Assembly order is
`{sec1}{sec1_warn}{sec1b}{sec2}{sec3}` (`app.py:3361`). Inserting a query-English
block between `sec1` and `sec1_warn` re-separates the nikud warning from the
query it describes — the exact bug fixed once already (HANDOFF: "Print/Export"
section).

**B4 — `disputed_verse_note` has no query-side render site at all.** Its only
call is `app.py:4127-4130`, keyed on the *matched* unit, and
`build_print_html` never references it. A verse picker makes Joshua 21:36–37
directly selectable for the first time. *Required:* a query-side note on screen
**and** a new export parameter — not merely "make sure it still renders".

**B5 — the translation checkbox must not gate warnings.** Do-not-gate list:
`match_nikud_unreliable` caption (4245-4248), `ksiv_unpointed` caption
(4262-4263), disputed note (4127-4130), cross-verse sof-pasuq caption
(4120-4121). This is the same class as the bug found by `/code-review high` in
`90b6c04`.

**B6 — Kri track must be an explicit query choice.** 1,101 verses have both a
Ksiv and a Kri row with different values (Deut 7:9: 3670 vs 3680). Default-Ksiv
would silently search 3670 while the panel displays the Kri in brackets via
`merge_ksiv_kri_display`. Site needs a track selector here; app view stays
Ksiv-only.

**B7 — sub-unit queries still need `locate_vocalized`.** The whole-verse case
does not (we know the reference, so `verse_index[...].text` gives cantillated
text directly), but Word / phrase / span units carry bare consonants in the DB,
exactly Tab 2's problem (solved there at 5531-5556). That logic is currently
duplicated, not shared — **factor it into one helper** as part of this work.

### My own claims that were wrong

- **"Whole verses match almost nothing" — false.** Measured: **2.8% zero**,
  **median 17** matches, p90 65. Both reviewers reproduced this independently.
  The unit selector is a feature, not a mitigation; do not distort the UI
  around a 3% edge.
- **The `st.form` suggestion was unimplementable.** Widgets inside a form do not
  report until submit, so cascading selects can never filter each other there.
  Selects go outside; only the unit picker + submit go inside.
- **Line anchors were stale.** Mode radio is **4748** (not ~5122);
  `build_print_html` **3028**; `render_verse_detail` **4042** with **7** call
  sites (not ~8).
- **"0 verses lack a SecondHalf" was my own SQL bug** (double-quoted string
  literal read as an identifier, silently returning empty). Correct figure:
  **1,731 Ksiv verses have no atnach and therefore no SecondHalf** — the unit
  selector must be built from what each verse actually has.

### Design changes adopted

- **Free-text reference is the PRIMARY input**, cascading selects the fallback
  under "Browse for a reference". This audience knows references cold and types
  `בראשית א:א` faster than three dropdowns. `parse_verse_ref` is therefore
  required, not optional.
- **App view: text box + unit selector only.** No cascading selects on a phone.
- **Unit selector: Whole verse (default) · First half · Second half · Word**,
  with phrase units and word range behind "More units".
- **Single word and word range collapse into one control**: render the verse's
  words as small toggle buttons in verse order; click one for a word, two for a
  span (reusing `WordSpan` / `_w0`/`_w1`).
- **Tab 2's "Filter (book contains)" should use the same `parse_verse_ref`**,
  turning it into a real reference filter. One parser, two call sites — this
  removes the Tab 1 / Tab 2 confusion rather than arguing it away.
- ⚠️ **Word-range offsets must index `_tokenize_raw_words`** (4093), while
  `span_w_cons` uses `tokenize_words` (4094). Two different tokenizers; mixing
  them slides the highlight.

### Build order (revised)

1. **Both-sides translation, standalone.** It fixes shipped one-sidedness in
   Tab 2's export, depends on no verse-mode decision, and carries B2/B3/B5.
2. **`parse_verse_ref`** + free-text box, wired into Tab 1's new mode *and*
   Tab 2's filter. Self-testable — assertions go in `run_selftest`.
3. **Whole-verse query** with the word-button selector, including B1, B4, B6.
4. **Phrase units, word range, cascading-select fallback** (site-only) last.

### Still open for Joshua

1. **Kri track (B6):** when a picked verse has a Kri reading, should the site
   default to Ksiv with a selector, or ask explicitly?
2. **Method scope:** default the verse mode to Standard only, or all 35? Median
   is 17 matches on Standard but ~595 across all methods — the latter is a
   browse, not a lookup.
3. **Tab 2 filter change:** in scope for this work, or a separate change?
