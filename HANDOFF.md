# Tanakh Gematria Engine — Session Handoff

**Project:** `C:\Users\joshu.AKIVA\Desktop\tanakh-gematria`
**Live URL (site):** https://huggingface.co/spaces/TorahNLP/tanach-gematria
**Live URL (app / PWA install):** https://torahnlp-tanach-gematria.hf.space/?view=app
**Last code commit:** `1088128` (Atbach in two girsaos; 27-letter becomes the default)
**Last data commit:** `ffa9cfb` (name-index review lists — no app code)
**Last DB-affecting commit:** `1088128` — **rebuild `tanach.db` if you are older than this**
**Handoff date:** 2026-08-12

> ✅ **Everything in this document is pushed and verified live** on all four
> targets unless explicitly marked otherwise.
>
> ⚠️ **EXCEPTION as of 2026-08-12:** the Shabbat 104a citation upgrades for
> Atbash and Albam, and the removal of Yalkut Shimoni, are **committed to
> `main` but NOT yet deployed**. Selftests pass. See "Citations: verify against
> the primary text".
>
> **This file lives on the `docs` branch and is never deployed** — see "Docs are
> off the deploy path" below. Editing it costs nothing; it used to cost a
> production restart.
>
> ⚠️ **Read the concurrency section first.** The Space has gone down TWICE with
> `RUNTIME_ERROR` exit 139 from a sqlite connection used across Streamlit
> sessions — most recently on 2026-08-03, from verse mode. It is easy to
> reintroduce and the fix pattern is documented there.
>
> ⚠️ **The 2026-08-05 session changed stored nikud values twice** (`75c768f`,
> `5da07eb`). The dagesh no longer scores, and the chatafim are now sheva+base
> in both nikud methods. **Rebuild `tanach.db`.** Read "Nikud: the sourced
> basis" before touching any vowel-mark code.
>
> **Open, with plans written:**
> - **Nikud tool** — `PLAN_nikud_tool.md`, this branch. Name lists generated
>   and agent-reviewed; `name_review/REVIEW_ME.csv` on `main` is waiting for
>   Joshua. Nothing built yet.
> - **Verse-reference search** — `PLAN_verse_search.md`. Shipped, but items 4
>   (word-range spans) and the cascading-select polish remain.
> - Variants-toggle redesign, `TODO(site)` on cleaned-consonants — both still
>   deferred.
> - **231 gates** — rule recovered and validated; display handling for ~22
>   further methods still to build. See "Open: 231 gates".
> - **`Agdat` (+2 shift)** — no source found, its one citation proven
>   fabricated. Decision parked until 231 ships. See "Open: the +2 shift".
> - **Source research** — `RESEARCH_LOG.md` on this branch has the full
>   per-claim verification record and the two traps that yield wrong results.

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
Verse / Petucha / Setuma / Perek / Sefer`

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
- **`Aggregate` is not a reading tradition** — it's a storage tag for Perek/Sefer
  totals, auto-added to `effective_tracks` when those boundaries are selected. It
  never counts as a variant.
- **App view is Ksiv-only**: `tracks = ["Ksiv"]`, no selector rendered, Track column
  never shown.

**The TextVariant trap (cost a wrong first implementation):** `doublet_from` /
`doublet_to` are defined on **bare consonants** (e.g. `אחר` → `ואחר`) and usually
have **no counterpart in the cantillated text**, so substituting into `v.text`
silently does nothing — this is the fallback `verse_forks` already anticipates.
The fork instead substitutes at **word level, once**, via `apply_doublet_to_words`;
`render_verse_detail` calls the same helper so the two cannot drift (see the fork
section above — they had drifted). Consequence, surfaced in the UI rather than
left silent: on those verses the cantillated line shows the Ksiv spelling while
the values follow the variant. Only 7 verses / 106 word units in the corpus, all reachable.

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

## Docs are off the deploy path (`88391af`)

HuggingFace rebuilds and restarts the Space on **any** push to the tracked
branch, including commits that only touch markdown. Four doc-only pushes on
2026-07-19 each took production down for ~3 minutes for no functional reason,
and one of those windows was reported as "the site's not loading" — it was just
me pushing a `.md`.

`HANDOFF.md`, `BUILD.md` and `CLAUDE_CODE_TASKS.md` now live on the **`docs`
branch** of the same remote. The Space builds `main`, so pushing `docs` deploys
nothing. **Verified before removing them from `main`:** after pushing the docs
branch the Space stayed `RUNNING` across eight checks over two minutes and never
entered `BUILDING`.

```bash
git worktree add ../tanakh-docs docs   # one-time, if missing
cd ../tanakh-docs                      # edit docs here
git add -A && git commit -m "..." && git push space docs   # no rebuild
```

`README.md` stays on `main` — it carries the Space's YAML config and is required
there. It points at this branch.

**Corollary worth remembering:** a 500 in the first minutes after *any* push to
`main` is the restart window, not a symptom. Don't debug it; wait.

---

## Notability: match count, not share of population (`8a6520a`)

The cross-method panel used to filter at "rate < 5%", where rate was a cell's
matches divided by the units in scope. That measured the wrong thing.

Methods differ enormously in spread over this corpus — **`KatanMispari` produces
9 distinct values, `ImMiluiNekudot` produces 3,467** — so average units-per-value
runs from ~36,000 down to ~95. A single share-of-population cutoff therefore
tracked how many values a method happens to produce, far more than rarity. It
blanked the entire `KatanMispari` column while passing essentially every
high-spread cell. And it inverted rarity outright: it called שלום's `Milui` value
"notable" when that value is **44× more common** than typical for Milui.

Now:
- **Filter = absolute match count** (default 25, with "No limit"). This is the
  question the panel actually serves — a cell with 30,000 matches is unusable
  however interesting, 20 can be read through — and it is stable whether the
  population is 39 units or 519,350.
- **Colour = lift**: matches ÷ expected, where `expected = population / distinct
  values for that method` (`cached_method_spread`). Each column is judged against
  its own spread. Warm = rarer than typical for that method, cool = as common or
  commoner. The old colouring used the same broken share measure as the filter.

Verified: `Standard` (lift 2.53) reads cool, `Milui` (0.69) and `ImMiluiNekudot`
(0.70) warm; at limit 25, 471 of 1,156 cells survive.

---

## Spinner placement (`88391af`)

`@st.cache_data(show_spinner="…")` renders its spinner **outside** the expander,
where it overlapped the panel below. Both heavy scans now run inside an explicit
`with st.spinner(...)` in the panel body, with `show_spinner=False` on the
decorator. Verified by geometry: spinner `2086..2113` inside expander
`1574..2130`, DOM-contained.

---

## ⚠️ Never share a sqlite connection across sessions (`31b3f21`)

**The Space died with `RUNTIME_ERROR`, exit code 139 (SIGSEGV)**, raising
`sqlite3.InterfaceError: bad parameter or other API misuse` from `search_value`.

`_build_connection` returned a single `sqlite3` connection to an in-memory DB,
cached with `@st.cache_resource` — so it was shared by every Streamlit session
*and* every script-runner thread. sqlite3 connections are not safe for
concurrent use: overlapping queries raise `InterfaceError` and can segfault the
process. **Two people searching at once, or one person in two tabs, is enough.**
It stayed hidden for so long only because the app rarely had concurrent users;
parallel browser checks during verification made it reproducible.

`ThreadLocalConnection` now gives each thread its own connection, all attached to
one `cache=shared` in-memory database, so the corpus is still built and held once
rather than per session. **The `keeper` connection must stay alive** — a
`mode=memory` database is destroyed when its last connection closes. `raw_conn()`
unwraps the proxy for pandas, which dispatches on
`isinstance(con, sqlite3.Connection)` and otherwise warns and takes an untested
path on every query.

Reproduction, if you ever need it: 8 threads querying the old shared connection
raises `DatabaseError`; the new one runs 8 threads × 12 rounds across five query
functions with zero errors under `-W error::UserWarning`.

**Rule: anything cached with `@st.cache_resource` is shared across sessions and
threads. Do not put a bare DB connection, cursor, or other stateful client in
it.**

### It happened again — exit 139 on 2026-08-03 (`343f311`, reverted in `2b7b710`)

The verse-reference search mode took production down the same way, and the
lesson is narrower than the rule above: **`ThreadLocalConnection` protects you
only where it is actually used per thread. Calling `raw_conn(conn).execute(...)`
directly in the SCRIPT BODY does not.**

The new mode ran two ad-hoc queries at Tab-1 script level — "which boundary
types does this verse have", "which sub-units" — so they re-executed on **every
rerun of every session**, unwrapped and uncached. Local testing never caught it
because a single user never overlaps; the Space segfaulted (exit 139) within
minutes of real traffic. This is the same failure the section above documents,
reintroduced by copying the *syntax* of the one existing `raw_conn` call site
(inside a nested function that fires on demand) without noticing **where** it
was safe to run.

**Do this instead:** put the query in a `@st.cache_data` function taking the
connection as `_conn` (Streamlit then skips hashing it) with `corpus_key` for
identity — exactly how `search_phrase`, `span_search` and `boundary_population`
already do it. A verse's available units are a pure function of the reference,
so they cache perfectly.

**Two diagnostic notes for next time:**
- `runtime.stage` said `RUNNING` while the app was already dying. **Check
  `runtime.errorMessage` from the API**, not just the stage — it carries the
  exit code. Failed page loads were misread as the documented cold-start
  pattern for several minutes because the stage looked healthy.
- Without an HF token in the session you cannot fetch `/logs/run`, but
  `errorMessage` on the plain `/api/spaces/...` endpoint needs no auth and
  carried the exit code and the tail of stdout.

---

## Performance: expander bodies run while collapsed (`fe6fb2f`)

Searching took ~18s, and picking a nikud method up to ~63s. The cause is a
Streamlit behaviour worth remembering: **an expander's body executes even while
the expander is collapsed.** Every search — and every widget interaction, since
each triggers a full rerun — ran a full-corpus word-span scan *and* a 34×34
cross-method count matrix, both inside panels that are shut by default. Nothing
was cached, so it all repeated on every click.

Both panels are now opt-in behind a checkbox, and `span_search`,
`_xm_count_matrix` and `boundary_population` are wrapped in `@st.cache_data`.
The connection is passed as `_conn` so Streamlit skips hashing it, with
`corpus_key` standing in for identity so a custom Sefaria corpus cannot reuse the
bundled corpus's entries; sequence arguments are tuples so keys stay stable.

| | Before | After |
|---|---|---|
| Search settles | 18.6s | **0.7s** |
| Add HaNekudot | 18.6s | **2.3s** |
| Add Im HaNekudot | 63.0s | **2.2s** |

Cold vs warm compute is 4.4s against 0.7s. **Measure cache effects with a word
the cache has not seen** — `st.cache_data` is process-wide, so a previous test on
the same server will make a "cold" run look instant. That produced a wrong
measurement once.

---

## Verse-reference search (`39a5742`, and the fixes after it)

A third mode on Tab 1's radio: `Hebrew text` / `Gematria value` /
**`Verse reference`**. Type a reference, pick which unit of the verse to search,
and it commits like any other query. One implementation serves the site and app
view.

- **`parse_verse_ref`** accepts English, yeshivish and Hebrew book names,
  arabic or Hebrew-letter numerals: `Genesis 1:1`, `Bereishis 1 1`,
  `בראשית א:א`, `2 Kings 2:1`, `מלכים ב ב:א`, `Kings II 2:1`. Self-tested,
  including that **all 39 canonical book names round-trip** — without that
  assertion a book can silently become unreachable by typing.
- **Tab 2's filter uses the same parser.** It was a book-substring match only,
  so reaching one verse meant typing the book and scrolling 23,206 rows.
- **`BOOK_DISPLAY_NAMES` / `book_label()`** show "Kings I" rather than
  "I Kings". ⚠️ **Display only** — `book` is a stored DB value used by every
  query, sub_id and join. Never route a DB call through it.
- Free text is the primary input; the cascading Book/Chapter/Verse selects are
  a collapsed fallback, shown in **both** views.

⚠️ **The selects must NOT go inside an `st.form`** — widgets in a form do not
report until submit, so the chapter list could never narrow to the book just
chosen.

⚠️ **The "Use this reference" button writes a SEPARATE key** (`t1_vs_seed`),
not `t1_vs_ref`. Streamlit raises `StreamlitAPIException` if you assign to a
session-state key after its widget exists, and the text input owns that key.
The seed is popped at the top of the branch and used as the widget's value on
the next rerun — the same shape as the keyboard's `_KBD_BUF`.

### Query-side `nikud_partial` gate

⚠️ Every other `nikud_partial` check in the file is a **corpus-side SQL
predicate**; nothing checked the query. Picking one of the **1,107 flagged
verses** under a vowel-mark method would have computed a knowably-short value —
or, for the `Im*` pair, one identical to Standard — and then searched it,
returning real-looking matches for a value that should not exist.

The four vowel-mark methods are now **removed from the picker** when the
committed unit is flagged. **Read the flag from the SELECTED UNIT's own row,
never the parent verse's**: 922 clean halves and 15,856 clean words sit inside
verses flagged at verse level.

### Self-matching (`a6d0f72`)

A unit always equals its own value, so a verse search returned the searched
verse among its matches — all 23,206 verses, every method. Tab 2 had it too.
`drop_self_match` removes a row only when it is the **same unit AND the same
method**. Deliberately kept: the same unit under a *different* method (Genesis
11:9 has Standard == Atbash), a different boundary of the same verse, and typed
Hebrew searches, which pass no unit at all.

## Two-sided translation and the cross-method export

- `render_verse_detail` takes `query_ref`; `build_print_html` takes
  `query_english`. An export of "these two units share a value" used to carry
  only the matched unit's English.
- ⚠️ **The CC-BY-NC attribution is rendered ONCE at document level.** It used to
  sit inside the single `if english:` block; a second translation block would
  have printed the licence notice twice.
- ⚠️ **`query_method`** — a cross-method drill-down has TWO methods. Without it
  the export scored the query with `drill_b` too, printing an Atbash total of
  2344 for the name above an Atbash verse total of 1036 and calling them equal.
  The drill-down pickers now say "Method A — your search term" and
  "Method B — corpus results", which is the same confusion at root.
- Colel is mentioned in a print-out **only when it actually applied** — not when
  off, and not when on-but-exempt for the method.

## ⚠️ Nikud: the sourced basis for every value (`75c768f`, `2b5e316`, `e333ea6`, `5da07eb`)

**Read this before changing any vowel-mark value.** Two stored-data changes
landed on 2026-08-05, and the reasoning behind every number is now traceable.
Do not re-derive it — establishing it took a long adversarial research round in
which the assisting AI fabricated **nine** Hebrew quotations or arithmetic
claims. Only what is quoted below was verified against the actual text.

### The dagesh scores 0

`דגש ורפה` are **not nekudot**: Etz Chaim שער ה׳ says
`"אינם לא טעמים ולא נקודות ולא תגין"`, and Pardes Rimonim **שער כ״ח** — the
Remak's entire gate on the nekudot — never mentions דגש in any chapter. The
canonical count is nine, and the ninth is **shuruk**, not dagesh.

⚠️ **Shuruk and dagesh are the SAME codepoint (U+05BC)**, so this could not be
a table entry. `is_shuruk()` splits them by position: U+05BC on a vav carrying
no vowel of its own is a shuruk (scores 10, `"נקודה בתוך הו'"`); anywhere else
it is a dagesh (scores 0). Verified on the corpus — תֹהוּ/וּבֵין/רוּחַ classify as
shuruk, בְּרֵאשִׁית/בָּרָא/כִּי as dagesh. Affected **38.7% of pointed words**.

A proposal to score shuruk as **16** (vav 6 + dot 10) was **rejected**: the vav
is a consonant, and counting it would double-count. Its own proposer retracted
it.

### The chatafim are sheva + base, in BOTH methods

The Remak names them himself in שער כ״ח פרק א׳:
`"וג' מורכבות, שבא קמץ, שבא פתח, שבא סגול, וקוראים אותו חטף קמץ חטף פתח חטף סגול"`

- geometric: 26 / 36 / 50 (was 6 / 16 / 30 — scored as the bare base vowel, as
  if the sheva on the page were absent)
- milui: 791 / 533 / 402 (was 488 / 230 / 99)

⚠️ **The milui half was initially left alone and that was wrong.** Research
established that no classical text computes milui on a chataf at all, and the
first conclusion was "add no number no source supports". Joshua's objection
settled it: *if we count the sheva's dots in one method, we should name the
sheva in the other*. What makes that right is that **no source prints ANY of
these sums** — nobody writes `פתח = 488` — so "unsourced" does not distinguish
the cases. Both methods are one consistent rule applied to sourced spellings and
shapes; exempting the chataf in one was the inconsistency.

Rejected: `"חטף פתח" = 585`, which spells the grammatical descriptor חטף.

### Where each value actually comes from

| link in the chain | status |
|---|---|
| component counts (how many dots/lines per vowel) | **SOURCED** — Pardes Rimonim שער כ״ח פרק א׳ describes every shape; each is quoted at its line in `NIKUD_VALS` |
| dot = yud = 10, line = vav = 6 | **SOURCED** — Tikunei Zohar תיקון ע׳, `נקודה איהי י', וקוא איהו ו'` (identity only) |
| the multiplication and addition | **INFERENCE** — no classical text prints "kamatz = 16". Chabadpedia calls it `השיטה הנפוצה` and notes there are **two** methods |

⚠️ **The Guide must not credit the Arizal or the Remak with the arithmetic.**
It used to. שער כ״ח contains zero occurrences of גימטריא and no "16" anywhere —
it is sefirotic symbolism, not calculation.

### Milui spellings follow the Remak, not Gikatilla

The *method* is Gikatilla's (Ginnat Egoz), but that text could not be obtained
and the one quote offered for its orthography was fabricated. שער כ״ח is
readable and countable, so it is the verifiable baseline. Frequencies in that
gate, recorded at each line in `NEKUDA_NAME_VALS`:

`שבא` 13x · `חירק` 16x (**חיריק 0x**) · `צירי` 26x (צרי 3x) · `סגול` 23x ·
`פתח` 16x · `קמץ` 43x · `חולם` 19x · `שורק` 24x (שרק 12x)

Two values moved: **chirik 328 → 318**, **tsere 300 → 310**.

⚠️ `קובוץ` is the ONE spelling not grounded in his usage — שער כ״ח never writes
it (`קבוץ` once; he calls the mark `קבוץ שפתים`, treating it as
`"שורק של ג' נקודות"`). Modern spelling kept for recognisability; flagged in-code.

Full detail, including what was retracted, is in Claude's memory note
`tanakh-gematria-nikud-sourcing`.

## Vowel-mark (nikud) methods (`c389659`, `0008775`)

**Bug fixed: the detail panel contradicted the search that produced the row.**
Result rows carry only bare consonants (`consonants` / `text_display`), and
`matched_text` was passed straight through as the cantillated source — so every
vowel-mark cipher scored 0 in the panel. A Word match *found via* `HaNekudot=50`
displayed `HaNekudot=0`.

`locate_vocalized()` recovers the pointed text from the parent verse by matching
the shortest consecutive run of words whose consonants equal the matched string.
Verified: 240 sub-unit rows across all four nikud ciphers now agree with the DB
exactly. If a unit cannot be located the panel says so rather than silently
scoring without nikud.

**Breakdowns now exist for these methods** — per mark, not per letter.
`nikud_breakdown()` lists each mark on ◌ with its Hebrew name and value:
geometric (dot=10, line=6) for HaNekudot/ImHaNekudot, the gematria of the mark's
*name* (Gikatilla) for the Milui pair, with the `Im*` variants listing letters
first, matching how `compute_all_ciphers` builds the total:

```
MiluiNekudot for יְהוֹשֻׁעַ:  שבא(303) + חולם(84) + קובוץ(204) + פתח(488) = 1079
```

600 breakdowns checked corpus-wide; every one sums to its cipher value.

**Unpointed input blanks the four methods to "—"** in both the computed-values
box and the cross-method matrix rows. Without nikud, HaNekudot/MiluiNekudot come
out 0 and — more misleading — `ImHaNekudot`/`ImMiluiNekudot` come out *exactly
Standard*, since they are `Standard + 0`. That reads like a real second opinion
and isn't one.

**The vowel-mark *columns* stay live**, deliberately. They remain valid for
unpointed input: they ask whether a corpus unit's vowel-mark total equals one of
your word's other values, which needs no nikud on the input side. Only the rows
are dead. Note too that **every HaNekudot total is even** (dot=10, line=6;
verified: 0 odd values in 571,521 rows), so an odd value can never match that
column — the same property that puts HaNekudot in `COLEL_EXEMPT`.

Implementation detail: the heatmap's `gmap` is computed **before** blanking, so
it never sees `NaN`. Reordering those lines breaks the gradient.

---

## Print/Export: Your Word calculation + nikud accuracy warnings (`ea0b5e2`, `90b6c04`)

**The print/download HTML used to show only how the *matched* corpus text
arrives at its value, never how the user's own searched word does** — even
though showing both is the point of a "these are equal" result.
`build_print_html` gained `query_breakdown`/`query_val` and a new **"Calculation
— Your Word"** section, built by the same `_breakdown_table()` helper now
shared with the existing (renamed) **"Calculation — Matched Text"** section —
unqualified when there's no query word (Gematria-value-mode prints have none).

**Found and fixed alongside it:** Section 1's "Value" was silently sourced from
the *matched* unit's value, not the query's own — identical for an exact match,
but wrong under colel where they can differ by 1. Now shows `query_val`. Also:
the Gematria-value-mode call site was passing `query_info=st.session_state.get
("t1_committed")` — leftover state from an unrelated earlier Hebrew-text search
(or absent) — now passes `None` rather than print a stale/wrong word.

**App-view verse-detail box simplified.** Dropped from app view only (site
unchanged, per instruction): "Matched consonants"/"Consonants", the all-methods
values table, and the "#### Results for `{cons}`" heading. All three are still
*computed* (the print-out needs them) — only the *display* is gated. This also
resolved a real mislabelling for HaNekudot/MiluiNekudot: "Matched consonants"
implied the consonants mattered when only the vowel marks did.

**Download HTML deliberately kept in app view**, though removing it was
requested — `window.print()` is blocked in the Print button's components
iframe on iOS (see BUILD.md), so Download HTML is the *only* working export
path there, in the view built for phones. Flagged rather than silently
overridden.

**Also fixed:** three Tab 3 echo-pattern functions (`internal_balance_matches`,
`proximity_echo_matches`, `whole_unit_echo_matches`) were missed by the earlier
`raw_conn()` sweep — multi-line call sites the original substring replace
didn't match, still hitting pandas' "untested DBAPI2 object" warning on every
call. Verified clean under `-W error::UserWarning`.

### Code review found two more real bugs here (`90b6c04`)

`/code-review high` (opus finders + opus verifiers, 8 angles → 7 candidates →
1 refuted after a 12,692-comparison fuzz test) on the commit above found:

1. **The "Could not locate this unit's pointed text… vowel-mark methods are
   computed without nikud here" caption was gated behind `if not app_view:`**
   alongside the (correctly-hidden) values table. That caption is the *only*
   signal a displayed value is unreliable, not optional chrome — app-view
   users could land on an unlocatable nikud-cipher match and see an unexplained
   (often zero) value with **no warning at all**, while site users still got
   it. **Fixed:** renders in every view now, driven by `match_nikud_unreliable`
   (computed once, see below).
2. **The same gap existed in the exported document, and was worse there** —
   `build_print_html`'s `nikud_warn` only ever checked whether the *query*
   lacked nikud, never whether the *match*'s pointed text was locatable. The
   export could show "Your Word" and "Matched Text" totals that flatly
   disagree (e.g. 50 vs 0) with **zero explanation anywhere in the document**
   — even on the full site, where the screen caption did warn. **Fixed:**
   `match_nikud_unreliable` threaded into `build_print_html`, rendered as its
   own caveat under Matched Text.

Fixing #2 exposed a **placement bug**: the query-side "input has no nikud"
warning was rendering inside what is now the *Matched Text* section — a
leftover from before this session added "Your Word." Moved to sit with **Search
Query** instead (`sec1_warn`), and deliberately **not** nested inside "Your
Word" — a query with no nikud at all has no breakdown rows, so "Your Word"
never renders, and the warning would have gone dark for its most direct case.

Minor finding also applied: `locate_vocalized(src_text, cons)` was called twice
with identical arguments a few lines apart; cached in `_located`, reused for
both the fallback and the caveat condition.

**Two findings investigated and *not* acted on** — reported for the record:
- `query_val` (a full 34-cipher `compute_all_ciphers` pass, kept for 1 value)
  and `cipher_breakdown`'s own total are two independent computations for the
  same number. A 12,692-comparison fuzz test across every breakdown-bearing
  cipher found **zero disagreement**, and `cipher_breakdown` *is* the
  decomposition `compute_all_ciphers` sums — they can't currently diverge.
  Known duplication, left alone; threading the value through touches multiple
  call sites for a non-live risk.
- The inline `if app_view` gating pattern and the `query_info=None` point-fix
  were both checked for structural fragility and refuted: ~20+ existing
  `app_view` checks already establish this as house style, and all 7
  `render_verse_detail` call sites were traced — none but the fixed one are
  vulnerable to the stale-`query_info` bug class.

---

## `sub_id` uniqueness (FIXED, `8f7b636`)

`sub_id` used to collide **142,635 times** in 571,521 rows. `_base_id` took the
first letter of each word in the book name, capped at 4 chars, collapsing the 39
books into 18 tags — `J` covered Jeremiah, Job, Joel, Jonah, Joshua and Judges,
so `J_4_9_Ksiv_W5` named six different rows. The displayed SubID did not identify
a row, and anything keyed on it silently merged unrelated books; that produced a
false regression signal during the TextVariant work.

`book_slug()` now strips non-alphanumerics from the full book name:
`Judges_1_1_Ksiv_FH`, `IChronicles_5_3_Ksiv_FH`. Aggregate ids use the same slug
and the leftover `PARSHA_` prefix became `SEFER_`. Verified: 571,521 distinct ids
for 571,521 rows, zero duplicates. Cost ~3.5 MB. **Changed stored data — needs a
DB rebuild.**

The rule it replaces still holds as a habit: prefer
`(book, chapter, verse, boundary_type, variant_track)` as a key. `sub_id` is now
safe, but the tuple is self-describing.

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

## ⚠️ Ksiv/Kri was double-counted — every value in 1,104 verses was wrong (`44ac11c`)

**The single most important fix in this session's work.** Sefaria encodes a
Ksiv/Kri divergence inline as `ksiv [kri]`. Nothing parsed that notation, so
`tokenize_words` saw **two** words and every cipher counted **both** readings.
Deuteronomy 7:9 scored `מצותו` (542) *and* `מצותיו` (552): Verse Standard 4222,
where Ksiv is 3670 and Kri 3680.

Not confined to spans — the doubled word sat in the Word units, so it
propagated into every containing unit and every search, pattern and statistic.
**Scope: 1,104 verses, 492 of 929 chapters, 37 of 39 books, ~397,000 of phantom
value.**

The app already had a Kri fork engine (`VerseInput.kri_text` → a `Kri` variant
track), fully built and documented — the corpus simply never populated it (the
Kri track had **zero** rows). `split_ksiv_kri()` in the loader feeds machinery
that already existed. Kri track is now ~30,121 rows.

**Four corpus shapes the parser handles** (verified against all 23,206 verses):

1. one bare word + one bracket — the common case;
2. consecutive brackets sharing a run of bare words — Job 38:1
   `מנ הסערה [מִ֥ן ׀] [הַסְּעָרָ֗ה]`, matched positionally;
3. **maqaf-joined tokens — this broke the first attempt.** `אֶת־יעיש [יְע֥וּשׁ]`
   is ONE whitespace token holding TWO words, so dropping the token also dropped
   `את`; `לך־[לְכָה־]` fuses the bracket into the token. Splitting on whitespace
   failed **231 verses**. Must scan maqaf-aware units;
4. one-word ksiv → multi-word kri (Isaiah 3:15 `מלכם`→`מה־לכם`, Psalms 55:16
   `ישימות`→`ישי מות`). Word counts legitimately differ — **not** a parse error.

Also stripped in the same pass: Lamentations 5:22 and Ecclesiastes 12:14 carry a
liturgical repetition note in `<br><small>[...]</small>` (the previous verse
repeated so a book does not end sombrely). It was being scored as scripture and
fused words across the tag boundary (`מאד`+`השיבנו`). Removed **before** kri
parsing so its bracket is never mistaken for a kri.

---

## Unpointed Ksiv: vowel-mark methods are undefined, not zero (`58a5f0f`, `b8ce7aa`, `7f037b6`)

Sefaria prints the **Ksiv side as bare consonants** — 1,082 of the 1,102
divergences. So the four vowel-mark ciphers score it from marks that are not in
the data: Deut 7:9's Ksiv `מצותו` gives `HaNekudot` **0**. **All 1,101 Ksiv/Kri
verse pairs score lower than their Kri twin on HaNekudot.**

This is the source text's nature, not a defect here — in the Masoretic
manuscripts the vowels of the Kri sit on the consonants of the Ksiv, so the
written form has no vocalisation of its own. **Inventing vowels would be
fabricating text.**

**Joshua's rule — do not weaken this:** any word, verse, half-verse, phrase or
span containing such a word is **excluded from all four vowel-mark methods,
period.** Not flagged-but-shown, not shown as 0. His reasoning: the total is
knowably *short* (every other word contributed, that one could not), and unlike
a missing value it looks entirely ordinary in a results list.

Implemented as a **`nikud_partial` column set at build time** — not thirteen
query-site derivations that would drift. ⚠️ **This is a stored-data column: a DB
built before it crashes with `no such column`.** `_build_connection` now checks
the prebuilt schema and rebuilds rather than serving it (see the Streamlit Cloud
entry below).

Judged on each unit's **own** cantillated text, never the parent verse's — a
pointed word beside a bare one is sound, and verse-level flagging would condemn
~16,000 valid Word units to protect ~1,300. Applied in `search_value`,
`count_value`, `boundary_population` (takes `cipher`, so the denominator matches
the numerator), `search_value_all_methods`, all three pattern scans (**both**
sides of each join), `span_search`, the detail panel and the print-out.

**Spans use a different mechanism deliberately:** rows are **not** deleted,
because `_w0`/`_w1` index `tokenize_words()` and removing one shifts every later
index and mis-places the highlight. The word stays; any *window covering it* is
rejected via a prefix sum over the flag.

**Two traps worth remembering:**

- `ImHaNekudot`/`ImMiluiNekudot` add letters to marks, so on an unpointed word
  they do **not** fall to 0 — they collapse to the plain letter total (identical
  to Standard), which reads as an ordinary number and is *more* misleading.
- `vals.get(method, 0)` in `build_print_html` silently reinstated the suppressed
  number. It now carries `None` and renders an em dash. A per-mark **breakdown
  table** does the same thing — summing it rebuilds the withheld total — so
  `build_print_html` clears `breakdown_rows`/`query_breakdown` itself rather
  than trusting its caller.

Fixed alongside: Perek/Sefer/paragraph-block aggregates passed no `cantillated`
to `insert()`, so **every chapter and book had a vowel total of 0** — absent,
not partial. They now join their members' cantillated text.

**Display:** the Kri is shown inline in brackets on the single cantillated line
(`הוצא [הַיְצֵ֣א]`), the notation the source uses — not as a second verse.
`merge_ksiv_kri_display()` is the inverse of `split_ksiv_kri` and its output
never reaches `cons`/`w_cons`. Brackets are inserted **after** highlighting,
since the highlight is placed by word offset and inserting first would shift it.

---

## English translation (`307f9a0`, `d271319`, `7f78f31`)

`tanach_english.jsonl`, keyed identically to `tanach_corpus.jsonl`, refetched
with `python fetch_english.py`. **Display only — it takes no part in any
calculation and is deliberately not in `tanach.db`**, so a translation refresh
never forces a cipher rebuild. Loaded as a plain dict, cached once per container.

**Which text, and why it changed twice:**

1. **JPS 1917** (Public Domain) first — rejected as too archaic (53% of verses
   carry thee/thou/unto/hath).
2. **JPS 1985** — readable, but embeds critical apparatus mid-verse and renders
   Gen 1:1 as "When God began to create".
3. **Koren Jerusalem Bible** (current). Same CC-BY-NC tier as JPS 1985, so no
   licence cost. Chosen for: "IN THE BEGINNING God created" (traditional
   reading), transliterated Hebrew names (Yisra'el, Miżrayim), and **no
   editorial apparatus fused into the verse**.

Only four English versions on Sefaria cover the whole Tanach: Koren, JPS 1985,
JPS Gender-Sensitive (renders the Name as "the ETERNAL" — ruled out), and JPS
1917. Metsudah/ArtScroll-adjacent editions are Chumash-only or absent.

**CC-BY-NC obliges attribution**, so `ENGLISH_ATTRIBUTION` is rendered in the
**export** (a printed document leaves the site). On screen the short form
`ENGLISH_ATTRIBUTION_SHORT` is used — the Guide and tooltip carry the full
notice. It also **blocks commercial relicensing** while bundled; swapping back
to JPS 1917 is a one-line `VERSION` change plus refetch.

**Joshua 21:36–37** — Koren omits them (see below), so they are filled from the
public-domain JPS 1917 via `FALLBACK_VERSION`/`FALLBACK_REFS` in
`fetch_english.py`, which a refetch reproduces automatically. JPS 1917 brackets
them; the brackets are stripped, since square brackets already mean Kri here.

### Hebrew Wiktionary — `wiktionary_nikud.json` (`6292dc3`)

`build_wiktionary_nikud.py` extracts 18,519 vocalized entries from the
he.wiktionary dump, plus 3,792 recovered by splitting its multi-word phrases.

**SETTLED 2026-08-07 (Joshua): no attribution, and CC-BY-SA does not reach the
app.** The earlier entry left this open; it is now decided, and the reasoning
should survive rather than be re-litigated:

- What was extracted is **facts, not authorship**. That `אהבה` is vocalized
  `אַהֲבָה` is how Hebrew works — nobody at Wiktionary invented it, they
  recorded it. Facts are not copyrightable and a mechanical extraction of them
  does not inherit the source page's licence (the *Feist* principle: effort in
  compiling confers no copyright over what was compiled).
- **No prose was taken.** No definitions, etymologies or usage notes — only the
  pointed headword and its bare spelling.
- **The selection was not taken either.** A dictionary's *arrangement* can
  attract protection even where individual entries do not, but our keys come
  from the corpus and the name lists; Wiktionary only fills gaps. Their word
  list was never copied as a work.

This is unlike the Koren translation, which **is** expressive authorship and
whose CC-BY-NC attribution is a genuine licence condition — see above. Do not
generalise from one to the other.

Rebuilding needs only `python build_wiktionary_nikud.py`; the dump is fetched
once and cached in the system temp dir.

---

## Joshua 21:36–37 are disputed — kept and flagged (`7f78f31`)

Present in our Hebrew corpus (tanach.us) but **absent from most Masoretic
manuscripts**; the same material is at I Chronicles 6:63–64.

| Edition | 21:36–37 |
|---|---|
| **ArtScroll** | Footnote — *"not part of the original Masoretic text of Joshua"* |
| **Koren** | Absent entirely |
| **Miqra according to the Masorah** | Em-dash placeholder |
| JPS 1985 / Gender-Sensitive / Fox | Placeholder |
| JPS 1917 | Present, bracketed |
| **tanach.us (ours)** | **Full text, unmarked** |

**Joshua's call: keep and flag, not strip** — silently dropping verses is worse
than showing them, and a numbering gap would be its own surprise. They *are*
counted in the Joshua 21 and Sefer Joshua totals. `DISPUTED_VERSES` /
`disputed_verse_note()` put a short note in the detail panel; the table takes a
second case in one line.

---

## Cross-verse word spans, opt-in (`3c8cbba`, `32a8aa2`)

`span_search(cross_verse=True)` also returns spans straddling a verse boundary.
**Off by default** — the sof-pasuq is a real division, so including those is the
reader's decision. Cost is small: ~1.4× more candidate windows at max_span=7.

**Two invariants, both load-bearing:** it only bridges **genuinely consecutive**
verses (the TextVariant track holds 7 scattered verses — a naive stream would
invent adjacencies), and it **never bridges books**.

⚠️ **The bug worth remembering:** the first implementation walked bounded
windows with an overlapping tail and **double-emitted** every span inside the
overlap — 19 of 301 real duplicates. Patching the arithmetic failed twice. The
fix was structural: split each track into maximal runs of consecutive verses and
scan each **once, end to end**. No overlap, nothing to double-count. It is also
*faster*. Verified 217/217 against brute force.

---

## Tab 2: combined half-verses (`307f9a0`)

`FirstHalf`/`SecondHalf` as separate radio options forced an arbitrary choice.
Replaced by one **"Half-verses (split at the Asnachta ֑)"** option listing both,
with a `Half` column. `BothHalves` is a **Tab-2-only pseudo-boundary**, not a
stored `boundary_type`, so the detail panel resolves each row back to its real
one. `structure_frame` now takes `*boundaries` (`track` became keyword-only).

Also: Tab 2's print-out carried only the matched unit's calculation — the
selected unit now threads through as `query_info` with a `label`, so the
headings read "Selected Unit (Genesis 1:1)" rather than "Your Word".

---

## The 35th method: Mispar HaMispari HaGadol (`bec3230`)

⚠️ **Stored-data change.** Pardes Rimonim שער ל׳ §9 (`מספריי הגדול`): name each
letter's **milui** total, rather than its standard value as §8 does. Internal
name `MispariHaGadol`, displayed `Mispari HaGadol — מספריי הגדול`.

The Remak's worked example reproduces exactly — yud's milui יוד = 20, and
עשרים = 620 = כתר — and since it is the **only** checksum this method has,
`run_selftest` pins it.

**Partly reconstructed, and labelled as such in the Guide.** He spells only that
one number and it is not a compound, but 15 of the 22 milui totals are.
`compose_number_name()` supplies them under a stated convention: hundreds
first, joined by a conjunctive vav, `אחד עשר` for eleven. Only the latter two
affect a value — **constituent order cannot, because addition commutes**, which
is what defeated the argument that this method was unreconstructible.

A composition invariant in `run_selftest` asserts every generated name denotes
the number it was built from.

Not in `_HEATMAP_EXCLUDE`: 2,893 distinct values over 3,000 sample verses, so it
neither saturates like `KatanMispari` nor breaks correlation like
`HaMerubahKlali`.

## ⚠️ Method counts are DERIVED — never write the literal

`N_CIPHERS = len(CIPHERS)` and
`N_HEATMAP_CIPHERS = len(CIPHERS) - len(_HEATMAP_EXCLUDE)`, interpolated into
ten user-facing strings. **There are TWO counts and they move independently** —
adding the 35th method took the headline to "35 gematria methods" and the
heatmap text to "33-method correlation heatmap" with no manual edits.

⚠️ The heatmap's smaller number is **correct, not stale**: `_HEATMAP_EXCLUDE`
drops `KatanMispari` (9 distinct values, saturates) and `HaMerubahKlali`
(hyperscale squared totals break Pearson). Joshua caught this being wrongly
flagged as a bug. Whether a new cipher belongs in that set is a judgement call
per method, so the second figure must stay derived from the frozenset.

Remaining `34`/`32` literals in `app.py` are unrelated: an Achbi index, a Boneeh
worked example, a verse reference.

## Method list: Mityashev out, Mispar HaMispari in (`a8f4b90`)

⚠️ **Stored-data change — rebuild `tanach.db`.** Count stays **34**.

**Removed `Mityashev`**: no classical source. `מספר מיושב` appears nowhere in
Pardes Rimonim (Gates 30 or 22) and returns **zero hits** across Sefaria. The
function, self-tests and word-boundary plumbing are **retained** so it can be
reinstated in one line if a source turns up. Beware: some sources use "mispar
meyushav" for *Mispar Katan*, a different calculation.

**Added `Mispari`** (Mispar HaMispari), Pardes Rimonim Gate 30 §8: spell each
letter's value as a Hebrew number-word and sum. Cordovero's two worked totals
reproduce exactly — yud → עשרה = 575, heh → חמשה = 353 — and **they fix the
orthography**: only the masculine forms (עשרה, חמשה, שלשים) give his numbers.
Online calculators use feminine/modern forms and differ on **13 of 22 letters**.
Values here will not match those tools; this is deliberate.

**Gate 30 §9 (*Misparei HaGadol*) is deliberately not implemented** — the rule
verifies (yud's milui יוד = 20 → עשרים = 620 = כתר) but only 4 of 22 letters have
a milui total that is a named number, so 68% of a verse would contribute
nothing. Cordovero demonstrates it on one letter as an observation, not a cipher
for summing words.

**Sourcing:** the Guide's 34 "Earliest Source" entries were swept — the only
non-rabbinic citation (1906 Jewish Encyclopedia) is gone. `KololOtiyot` and
`KololEhad` are now cited to Gate 30 §4 directly, both defined in one clause:
*"מספר מוספי הוא שמוסיפין האותיות מן המלה על המספר או המלה עצמה"*.

---

## ⚠️ A prebuilt DB older than the code now self-heals (`b08c1e4`)

**Streamlit Cloud is a fourth deployment** (deploys from GitHub) and it ships
`app.py` while reusing a stale `tanach.db`. When `nikud_partial` was added, its
first search died with an opaque `DatabaseError`. The HF Space and the local host
both rebuild the DB, which is why neither showed it.

`_build_connection` now checks the `units` schema against what the release
queries and **falls through to `build_database`** when anything is missing, with
a warning about the one-time ~30s wait. Any deployment shipping new code against
an old database self-heals instead of failing on first search.

---

## Print-out fixes (`a23f79f`, `a1e0a0b`, `0600686`)

- **Breakdown tables are RTL.** Hebrew headers (אות / מוחלף / ערך) were laid out
  left-to-right. Fixed with `direction:rtl` on `table.bd` rather than reordering
  cells, so it cannot drift from the row-building code. `.num` keeps digits LTR.
- **Large blank gaps.** `.sec` carried `break-inside:avoid`, so a long breakdown
  that could not fit jumped the whole section to the next sheet. Sections may now
  split; `table.bd tr` avoids splitting a row, `.sec-title` has `break-after:avoid`.
- **The total printed on every continued page.** `tfoot` defaults to
  `table-footer-group`, which repeats. Demoted to `table-row-group` in the print
  stylesheet; `thead` still repeats, which is correct.
- **Both calculations everywhere.** Three of seven `render_verse_detail` call
  sites never passed `query_info`, so their exports showed one side of a
  "these are equal" claim. All pass it now.

---

## Atbach ships in two girsaos (`423029e`, `1088128`)

**`ה` and `נ` pair with EACH OTHER, not with themselves.** The earlier
self-pairing failed the Gemara's own case: `מנון → סהדה` requires `נ→ה`. A
three-way `ה→נ→ך→ה` cycle was implemented once on the strength of an external
model agreeing 3/3, then reverted — `מנון`'s two nuns rule it out. **Model
agreement is weak evidence; disagreement is strong evidence of fabrication.**

Two methods now, both shipped:

- **`Atbach` — 27 letters, the DEFAULT.** Tiers complete to 10/100/1000 with
  final forms valued 500–900. This is what is printed in our Rashi; the sequence
  is set out in `ספר הערוך` (ערך אטבח).
- **`AtbachMaharshal` — 22 letters.** Finals count as their base letter, so the
  hundreds have four members pairing to 500. R' Chananel's girsa, cited by the
  Maharshal. Reproduces the sugya directly: `מנון → סהדה`.

Selftest pins both: `מנון → סהדה` (22) and `מנון → סהדש` (27).

On the 27-letter reading the sugya's own word gives `סהדש`, since final `ן` is a
700-letter paired with `ש`. The Maharsha raises exactly this and leaves it
`צריך עיון`; the **Aruch LaNer** answers that the verse's word is `סהדה` and the
`ן` of `מנון` is only how `נ` is written word-finally.

⚠️ **`ך` is under-determined in the 27-letter version.** It is left over after
`ה↔נ`. The Maharsha calls all three mutually interchangeable but demonstrates
only `ה↔נ`; the Maharshal objects that `ך` is stranded. **Currently scored as
itself and documented as a choice.** Parked idea: emit two outputs for every
`הנ"ך`. Do not silently "fix" this — it is a sourcing question, not a bug.

⚠️ When swapping which girsa is the default, the `Rule` and `Source` strings
must move with it. That was missed once and caught late.

---

## ⚠️ Citations: verify against the primary text, always (2026-08-12)

**Full detail is in `RESEARCH_LOG.md` on this branch.** Summary of what changed
and what bit us.

**Several supplied citations were fabricated** — plausible Hebrew attributed to
a real sefer and chapter that does not contain it. Confirmed invented:

- `פרדס רימונים` "Gate 22" for `אגדת` — שער כ״ב has no `אגדת`
- PR 30:8 / 30:2 "defines Achorayim" — neither chapter does
- PR 30:1 `דע כי האותיות מתחלפות … ב״ג ד״ה` — **not one phrase** is in the
  chapter. This was the would-be source for the +2 shift.

The near-miss worth knowing: PR 30:1 *does* contain a real, useful quote — the
Ramak's three-way taxonomy `חלק הצרוף וחלק התמורה וחלק הגמטריא`. A fabricated
quote pinned to a chapter that holds a *different* real quote is the hardest
kind to catch. Nothing goes in without a fetch-and-probe.

### Two traps that produce WRONG verification results

1. **Vocalized text.** Sefaria texts are fully pointed with mid-word gershayim;
   a bare probe `אטב` will not match `בְּאַטְבַּ״ח`. Strip both sides to `א`–`ת`
   before comparing. This produced a false "Atbach is not in Sukkah".
2. **Partial fetches.** Segment-by-segment fetching returns empty strings on
   timeout, and a missing-text `False` is indistinguishable from a real `False`.
   Fetch parallel with retries and check the context reads continuously. This
   produced a spurious `אם אתה בוש` = False on Shabbat 104a.

**A negative result is only evidence if the fetch was complete — say which.**

### Shabbat 104a: better sources for Atbash and Albam (applied)

The page carries **both**, in one sugya: the letter-name mnemonic
(`אָלֶף בִּינָה, גְּמוֹל דַּלִּים`) running into the cipher pairs themselves under
`מדת רשעים` / `מדת צדיקים` — `אתבש אם אתה בוש … גר דק … אלבם אם אתה עושה כן`.
All 11 Albam pairs match our map exactly.

`TALMUD_CIPHERS` is now literally true of every member: Standard (סנהדרין ל״ח),
Atbash + Albam (שבת ק״ד), Atbach (סוכה נ״ב), Gadol's 27-letter sequence
(ספר יצירה ב׳:ב׳). Albam was already at display position 4 — the citation moved,
not the ordering.

**Yalkut Shimoni cut from Albam** (was יתרו רמז רע״א): a likut citing the Gemara
adds nothing behind the Gemara. Decision was cut regardless; *if* it turns out
to be quoting a **midrash**, cite that midrash directly. **That check was never
done** — still open. `פרדס רימונים ל׳:ה׳` stays as the tabulation, since PR is
the baseline naming source throughout the app.

### Not yet applied

PR 30:1's taxonomy quote would make good framing for the temurah group in the
Guide. Verified verbatim, not yet in the app.

---

## Open: the +2 shift (`Agdat`) — PARKED until 231 ships

**No source found after four attempts.** Do not re-litigate without new evidence:

1. Sefaria search for `אגד"ת` / `אג"דת` → **zero hits**, while controls `אבג"ד`
   and `אלב"ם` return many. The search works; the name is not in the corpus.
2. The PR `ב״ג ד״ה` quote that would have sourced it is fabricated (above).
3. `אג דת הש ור` in PR 30:5 is **real but is not a shift** — it is the third of
   the 22 alphabets of the רל״א שערים, a *pairing* (א↔ג, ד↔ת, ה↔ש). Same
   letters, different parsing. **Do not accept this as a +2 source.**
4. Abulafia (`חיי העולם הבא`, `גן נעול`) — **unverifiable**, not on Sefaria, and
   the labels are internally broken: `+3` given as `גדה״ו`, four letters for a
   three-step shift, and `ג→ד` is a +1 step. Already dropped in Pass 1.

**The asymmetry is the argument.** `Avgad` (+1) is easy to source (טעם זקנים,
R' Eliezer Ashkenazi). If ordinal shifts were a classical family, +2 would not
be invisible while +1 is not.

Also settled: **231 cannot mean shifts.** Directed maps give 462; the Remak's
`רל"א שערים מפני שהם רל"א זוגות` counts *pairs* = C(22,2) = 231. The gates
cannot be retrofitted as a +2 source.

**Decision deferred by Joshua to after the 231 work.** Options: cut, keep
flagged as a modern extension with no classical source, or leave parked.
Recommendation on file: **cut** — it is the only one of the 35 resting on a
citation proven invented.

---

## Open: 231 gates (IN PROGRESS)

Generative rule recovered and validated three ways:

- Gate *k* pairs letters whose indices sum to *k−1* mod 22; self-paired letters
  join each other.
- 10 of 22 printed tables reproduce **exactly**; 89.7% of pairs overall — the
  other 12 tables are corrupted in the edition used.
- Yields exactly **231 distinct pairs = C(22,2)**, matching the Remak's own count.

`ספר יצירה ב׳:ד׳` verified verbatim: `קבועות בגלגל ברל"א שערים וחוזר הגלגל פנים
ואחור`. Note the wheel turns **forward and back**, not at arbitrary skip
intervals — readings that gloss this as "rotating across fixed intervals
generates shift rings" are importing the Ra'avad (on ב׳:ה׳) into the mishnah's
words. The Ra'avad's construction yields **pairings, not directed rotations**.

**Still to do:** display handling for ~22 further methods. Direction chosen —
one method + gate selector; Atbash kept separate (well known, straight from
Tanach) *and* also present in the sub-box. Naming suggested: `כ"ב אלפא ביתות`
rather than "231". Ideally verify the 12 corrupted tables against a cleaner
edition. Method grouping in the picker to be revisited once this lands.

---

## Known Issues / Gotchas

| Item | Status |
|------|--------|
| **Never share a DB connection via `@st.cache_resource`** | It is shared across every session and thread. A bare sqlite connection there took the Space down with SIGSEGV once two people searched at the same time. Use `ThreadLocalConnection`; see its section above. |
| **Expander bodies execute while collapsed** | Streamlit runs the code inside `st.expander` even when it is shut, so anything expensive in one costs every rerun. The two heavy Tab 1 panels are opt-in behind a checkbox for this reason. Do not put an unguarded scan in an expander. |
| **`st.cache_data` is process-wide** | A "cold" timing measured after an earlier test on the same server is really a warm one. Measure with an input the cache has not seen. |
| **Dataframes are canvas-rendered** | **DOM assertions cannot see any table contents** — cell text never reaches the accessibility tree. A Playwright check for a column's presence will pass whether the column is right or hidden always. Test table *contents* at the data layer; use the browser only for surrounding UI. This nearly produced a false pass on the Track-column work. |
| `streamlit` pinned to `1.58.0` | Pinned deliberately (`3f1e329`): the loader icon and app-view layout target internal test ids (`stStatusWidget`, `stSidebarCollapsedControl`). Upgrade only with a live re-verify of both. |
| Local `.venv` needs `plotly` | Installed 2026-07-19. Worth knowing why it matters: without it the Tab 4 import aborts the whole script run, so **every tab shows the traceback** and no site-view verification is possible. If a fresh venv shows errors on all tabs, check this first. |
| ~~`use_container_width`~~ | Done (`8f7b636`): all 29 sites use `width="stretch"`. The removal date had already passed; only the 1.58.0 pin was keeping it alive, so an upgrade would have broken every table and chart at once. |
| **First-load 500, fine on refresh** | **Reproduced 2026-07-19, and it is not app-side.** Right after a rebuild: request 1 hung 108s then returned HTTP 500 with a 3,038-byte body; request 2 returned 200 in 29ms. That body is HuggingFace's error page, not our 3,148-byte `index.html` — which is why no code of ours can intercept it. Causes: the Space is `cpu-basic` with a 48h sleep timer (cold wake ≈10s), and any rebuild restarts the container. Build is confirmed healthy: `builddb` ran 100.4s at image-build time, boot is ~10s, run logs clean. Only real mitigations are a keep-warm ping or a service worker; **Joshua declined the service worker** — it would install resident code on every visitor's device. |
| ⚠️ **Stripping cantillation with a RANGE eats the nikud** | `[֑-ֽ]` looks like "the te'amim" but U+05B0–U+05BC are the NIKUD, so that range silently returns bare consonants and every vowel-mark measurement comes out zero. **This cost real time twice in one session.** Strip an explicit set: `range(0x0591,0x05B0)` plus `05BD 05BF 05C0 05C3 05C4 05C5 05C6`. |
| ⚠️ **`tanach.db` holds NO pointed text** | `text_display` is bare consonants; the nikud lives only in `tanach_corpus.jsonl`. Anything needing vocalized text must index the JSONL, not the DB. The old auto-nikud plan assumed the DB had it. |
| ⚠️ **Double-quoted SQL string literals** | `WHERE boundary_type="Verse"` is read by SQLite as an IDENTIFIER, not a string, and silently returns ZERO rows. It produced a wrong "0 verses lack a SecondHalf" measurement that was believed until a reviewer contradicted it. Always single-quote. |
| ⚠️ **Wiktionary's `כתיב מלא` is NOT the bare form** | It is the *plene* spelling, deliberately DIFFERENT from a defectively-spelled pointed headword — `גֹּלֶם` has `כתיב מלא=גולם`. Verifying a pointed form against it rejected **4,993 sound entries (29%)** on a difference that is the field working correctly. Verify against the page **title**, which is the real bare headword. |
| ⚠️ **A bare letter is not proof of partial pointing** | Three bare letters are correct Hebrew: word-final (`דָּוִד`), a mater itself (the yod of `אֱלֹהִים` — *not* word-final), and a letter whose vowel sits on a following mater (`שָׁלוֹם`, holam on the vav leaves the lamed bare). A checker missing any of the three rejected **79%** of Wiktionary. Ground-truth any such check against the corpus: `is_pointed` rejects 16 of 52,481 Tanach tokens (0.03%), all genuine oddities (`יִשָּׂשכָר`, `נְבוּכַדנֶאצַּר`). |
| ⚠️ **`has_unpointed_word` is per-word, not per-letter** | It asks whether a word carries ANY nikud — right for its original job (Sefaria prints Ksiv words entirely bare) but it passes partially-pointed words like Harkavy's `חַיה`. It cannot validate an imported vocalization list; `name_review/validate.py` does that per letter. |
| Local `tanach.db` staleness | `tanach.db` is a **derived artifact** (gitignored, never regenerates on pull) holding boundary types, consonants, display text and all 34 cipher columns. Docker rebuilds it every build; **locally you must**. Two failure modes: a schema change crashes loudly (`no such column`), but a change to stored *values* fails **silently** — the app runs and serves stale data. After pulling anything that touches stored data: `rm tanach.db && python app.py builddb` (~100s). |
| **A stale `tanach.db` now self-heals, but rebuild anyway** | Since `b08c1e4` `_build_connection` checks the `units` schema and rebuilds if a column this release queries is missing — that is what fixed Streamlit Cloud. It costs a one-time ~30s on first load, so locally still run `python app.py builddb` after pulling a ⚠️ commit. |
| **Ksiv/Kri: never re-derive the flag at a query site** | `nikud_partial` is set once at build time precisely so the thirteen query paths cannot drift. Add new paths by reusing the column, not by recomputing `has_unpointed_word`. |
| Transliteration search | Not built. |
| On-screen Hebrew keyboard | Still commented out (`_KBD_KEY`). |
| Auto-nikud for typed input | **Plan written** — `PLAN_nikud_tool.md`, this branch. A separate page: type a word or phrase, get it back vocalized, edit any word from its attested options, copy out or jump to search. Name lists generated and agent-reviewed; `name_review/REVIEW_ME.csv` on `main` holds the 87 rows awaiting Joshua. **842 names need no review.** Nothing built yet. |
| Name sources | CBS list (2,165 names, ⚠️ **UTF-16**) is the inventory; 729 are in Tanach and get nikud free. ⚠️ **Harkavy 1925 was checked and REJECTED** — 30% letter coverage, only 1 of 561 names fully pointed, and שָׂרה carries nothing but a sin dot, which the engine excludes. Do not retry it. |
| ~~Word-span click-through~~ | Confirmed working by the user on 2026-07-19. |
| **`/code-review high` catches gating bugs verification alone misses** | The print/verse-box work (`ea0b5e2`) was fully browser-verified before push — every check passed — but the review still found two real correctness bugs (both confirmed by an independent verifier, fixed in `90b6c04`). The pattern: an `if not app_view:` gate is easy to write when simplifying a *display* line, easy to miss when one of the lines it's hiding is actually a *warning*, not decoration. Worth a review pass after any display-gating change, not just a browser check. |

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Entire app (corpus load, ciphers, PWA + loader head patch, both views) |
| `.streamlit/config.toml` | Static serving + indigo theme |
| `static/manifest.json`, `static/icon-*.png` | PWA assets (served at `/app/static/`) |
| `fetch_corpus.py` | One-time corpus builder |
| `fetch_english.py` | Translation fetcher (Koren + JPS 1917 fallback for Joshua 21:36–37) |
| `tanach_corpus.jsonl` | Corpus data (committed) |
| `tanach_english.jsonl` | Translation, display only (committed) |
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

**There are FOUR live targets. `git push` only updates two of them.**

| Target | How it updates |
|---|---|
| HF Space (production) | `git push space main` → rebuild ~2–3 min |
| GitHub | `git push origin main` |
| **Streamlit Cloud** | deploys from GitHub automatically |
| **Local Streamlit over Tailscale Funnel** | **does NOT update on push** |

The local one runs `host/Start_Gematria.vbs` against the *working directory
itself*, so files update on commit but the **running process keeps serving the
code it imported at startup**. It must be restarted:

```powershell
Stop-Process -Id <pid listening on 8501> -Force
Start-Process wscript.exe -ArgumentList '"...\host\Start_Gematria.vbs"' -WindowStyle Hidden
```

`host/gematria_watchdog.ps1` only relaunches when port 8501 is **down**, so it
will never restart a healthy-but-stale process. Funnel maps port **8443** →
`127.0.0.1:8501`; the bare hostname (no port) is a *different* app.

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

## Session Log (2026-08-03 → 08-05, newest first)

*All pushed and live on all four targets.* ⚠️ marks a commit that changed
**stored data** and therefore required a `tanach.db` rebuild:

- `ffa9cfb` Name-index review lists and validator (data only, no app code)
- `5da07eb` ⚠️ **Milui: chatafim = sheva+base; spellings follow the Remak**
- `e333ea6` Record that chataf milui has no classical basis at all
- `2b5e316` Document the nikud spelling choices no source fixes
- `75c768f` ⚠️ **Nikud: exclude the dagesh; chatafim = sheva+base**
- `a6d0f72` Drop the searched unit from its own results
- `86880e1` Fix cross-method print-out scoring the query with the wrong method
- `be948a8` Print-out: drop the "Consonants searched" row
- `77ca851` Collapse "Browse for a reference" by default
- `40c8723` Print-out: mention colel only when the calculation used it
- `00f5da2` Fix crash on "Use this reference" (session-state key)
- `a360493` Print-out: always state whether colel was applied
- `cf352f8` Fix the two-translation headings
- `3e49859` Show "Browse for a reference" in app view
- `930c5e3` Verse mode: browse above colel, canonical book order, "Kings I"
- `39a5742` **Re-add verse-reference search, with DB reads cached**
- `2b7b710` ⏪ **Revert of `343f311`** — production was down, exit 139
- `343f311` ~~Add verse-reference search~~ (took the Space down; see concurrency)
- `737272e` Move the reconstruction caveat into Source
- `bec3230` ⚠️ **Add Mispar HaMispari HaGadol as a 35th method**
- `ae52d84` Guide: Hebrew sources in a yeshivish register; span search
- `7298a16` Rewrite Guide & Sources: reorder, cut verbosity, fix stale rows

**Three bugs reached production in this stretch** (SIGSEGV, the heading
duplication, the session-state crash), each because a component was verified to
*render* rather than being driven through its actual path. The concurrency
harness now exists; a click-through pass before pushing would catch the rest.

## Session Log (2026-07-28, newest first)

*All pushed and live on all four targets.* ⚠️ marks a commit that changed
**stored data** and therefore required a `tanach.db` rebuild:

- `03baee7` Cut the on-screen notes down
- `7f78f31` Switch the translation to Koren; flag Joshua 21:36-37 as disputed
- `fa28946` Fill the two untranslated Joshua verses; shorten the licences section
- `c99b399` Shape the cross-method drill-down table like every other result table
- `433b7c0` Drop the 'Cantillated:' label from the verse-detail line
- `846cc3a` Actually merge the Kri into the cantillated line (not a second line)
- `1da645c` Kri in the print-out; honest label for pure-vowel methods
- `4a55b10` Ksiv/Kri panel: gate the caveat by method, use a dash, show Kri inline
- `b08c1e4` Rebuild a prebuilt DB whose schema predates this release
- `a1e0a0b` Print the breakdown total once, not on every continued page
- `a23f79f` Print-out: RTL breakdown tables; stop sections forcing page breaks
- `dbd0e58` Stop the print-out rebuilding a suppressed vowel total
- `fd448d1` Block nikud searches from a Ksiv unit in Tab 2
- `a8f4b90` ⚠️ Swap the sourceless Mityashev for Cordovero's Mispar HaMispari
- `550dadc` Source three weakly-cited methods from Pardes Rimonim itself
- `fcbbe1f` Remove the only non-rabbinic citation from the Guide
- `7f037b6` Apply the nikud exclusion in the detail panel too
- `b8ce7aa` ⚠️ Exclude units with an incomplete vowel total from all nikud results
- `17d42b6` Reword the unpointed-Ksiv note; stop pointing at the Kri track
- `58a5f0f` Flag Ksiv words the source prints unpointed
- `44ac11c` ⚠️ **Fix Ksiv/Kri double-counting — 1,104 verses were wrong**
- `29b1538` Drop the edition name from the English headings
- `0600686` Show both calculations in every verse-detail print-out
- `689b401` ⚠️ Merge: English translation, Tab 2 half-verses, cross-verse spans
- `32a8aa2` Fix duplicate cross-verse spans (whole-run scan, not overlapping windows)
- `3c8cbba` Add opt-in cross-verse word spans
- `d271319` Switch translation to JPS 1985; state every text licence explicitly
- `307f9a0` ⚠️ English translation; Tab 2 half-verses; cross-verse spans

---

## Session Log (2026-07-20, newest first)

*All pushed and live.* ⚠️ marks a commit that changed **stored data** and
therefore required a `tanach.db` rebuild:

- `90b6c04` Code review fixes: nikud accuracy warning reaches app view and print export
- `ea0b5e2` Print shows the searched word's own calculation; simplify app-view verse box
- `8a6520a` Matrix: filter by match count, colour by lift
- `b291d94` Show that the 5% threshold moves with the filters
- `88391af` Docs to `docs` branch; spinner inside panel; explain the rate
- `e39dcd0` Handoff: stop the header pointer going stale
- `1af30f4` Handoff: sub_id fixed, use_container_width retired
- `8f7b636` ⚠️ Unique sub_id (book_slug); retire use_container_width
- `b5b0f0c` Handoff: concurrency, performance, vowel-mark fixes
- `0008775` Blank vowel-mark methods when the input has no nikud
- `c389659` Vowel-mark breakdowns; fix nikud values in the detail panel
- `fe6fb2f` Opt-in heavy scans + caching (search 18.6s -> 0.7s)
- `31b3f21` **Fix Space crash**: per-thread sqlite connections (exit 139)
- `ba7000c` Handoff brought current
- `31344dc` ⚠️ TextVariant fork: word-level doublet, word-boundary half splits
- `66206d3` ⚠️ Word-spaced text in result tables (`text_display`)
- `2beff39` ⚠️ Parsha boundary → Sefer; app-view guide accuracy; Verse+Word defaults
- `1c02613` App view: reference column, drop Parsha/Value/SubID, move computed values
- `efc113b` Loader letter aligned with status widget; head snippets replaceable
- `474ff96` Handoff rewrite
- `9b78ccf` Search on type-and-click (st.form), no Enter needed
- `8345087` Method picker before search; Ksiv-only app view; Track only when it varies
- `3f1e329` Word-span detail scores the span not the verse; Hebrew loader icon; streamlit pinned

*Earlier:*
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
