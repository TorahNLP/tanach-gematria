# Tanakh Gematria Engine — Build Reference

**Live site:** https://huggingface.co/spaces/TorahNLP/tanach-gematria
**License:** CC BY-NC 4.0
**Stack:** Python 3.12 · Streamlit · SQLite (in-memory) · Plotly · Pandas

---

## What it is

A single-file Streamlit app (`app.py`) that brings together:

- **34 gematria ciphers** over the full 23,206-verse Masoretic Tanakh
- **Structural analysis** — half-verse splits at the Asnachta, Pesucha/Setuma paragraph detection, Ksiv/Kri variant forking
- **Pattern database** — internal half-verse balance, proximity echoes, cross-method echoes
- **Interactive dashboards** — Plotly histograms, correlation heatmaps, book fingerprints

The corpus is sourced from Sefaria (cantillated Masoretic text) and bundled as `tanach_corpus.jsonl`. Scripture is never typed from memory — wrong letters corrupt every total.

---

## The 36 ciphers

### Standard value group

| Name | Hebrew | Description |
|------|--------|-------------|
| Standard | מספר הכרחי | Standard values: א=1 … ת=400. Finals = base form. |
| Katan | מספר קטן | Drop trailing zeros (ק→1, מ→4), then sum. |
| Gadol | מספר גדול | Like Standard but finals carry 500–900. |
| KatanMispari | קטן מספרי | Sum Standard values, then reduce to digital root. |

### Ordinal group

| Name | Hebrew | Description |
|------|--------|-------------|
| Siduri | מספר סידורי | Ordinal position: א=1 … ת=22. |
| ReverseOrdinal | מספר אחור סידורי | Reverse ordinal: ת=1 … א=22. |

### Mathematical transforms

| Name | Hebrew | Description |
|------|--------|-------------|
| Ribua | מספר מרובע | Sum of squared Standard values per letter (Σv²). |
| HaMerubahKlali | מספר המרובע הכללי | The whole Standard sum squared: (Σv)². |
| Kidmi | מספר קדמי | Cumulative prefix sum of Standard values: each letter = Σ Standard values from א up to it. |

### Name-expansion — 2-letter (standard Lurianic)

| Name | Hebrew | Description |
|------|--------|-------------|
| Milui | מילוי | Spell each letter's full name; sum all spelling letters. א=אלף=111 … |
| Neelam | נעלם | Like Milui but drop the first letter of each name. |
| Emtzaiyot | אמצעיות | Standard value of the second (inner) letter of each Milui name. |
| Ofanim | אופנים | Standard value of the last letter of each Milui name. |

### Vowel-mark (nikud) ciphers

| Name | Hebrew | Description |
|------|--------|-------------|
| HaNekudot | מספר הנקודות | Geometric value of each vowel mark: dot=10, line=6. Dagesh=10. (Arizal, Sha'ar HaKavanot / Etz Chaim) |
| ImHaNekudot | עם הנקודות | Standard(consonants) + HaNekudot. (Pardes Rimonim Gate 30 Ch. 8, Cordovero 1548) |
| MiluiNekudot | מילוי הנקודות | Standard gematria of the Hebrew NAME of each vowel mark. שבא=303, פתח=488, קמץ=230 … (Gikatilla, Ginnat Egoz 1274) |
| ImMiluiNekudot | עם מילוי הנקודות | Standard(consonants) + MiluiNekudot. |

### Name-expansion — 3-letter / Maleh (כ=כאף, מ=מאם)

| Name | Hebrew | Description |
|------|--------|-------------|
| MiluiMaleh | מילוי מלא | Milui using Maleh spellings: כ=כאף=101, מ=מאם=81. |
| NeelAmMaleh | נעלם מלא | Neelam using Maleh spellings. |
| EmtzaiyotMaleh | אמצעיות מלא | Emtzaiyot using Maleh spellings (כ,מ both yield א=1). |

### Temurah (substitution)

| Name | Hebrew | Description |
|------|--------|-------------|
| Atbash | אתב"ש | Mirror swap א↔ת, ב↔ש … then Standard values. Oldest attested — appears in Jeremiah. |
| Albam | אלב"ם | ROT-11 swap across two 11-letter groups. |
| Achbi | אכב"י | Reverse each half: א↔כ, ב↔י … then Standard values. |
| Atbach | אטב"ח | Pairs summing to 10/100/1000; finals carry 600–900. |
| Avgad | אבג"ד | +1 cyclic shift (א→ב … ת→א), then Standard values. |
| Agdat | אגד"ת | +2 cyclic shift, then Standard values. |
| ReverseAvgad | אבג"ד הפוך | −1 cyclic shift (ב→א … א→ת), then Standard values. |
| AyakBachar | אי"ק בכ"ר | 3×9 cyclic rotation: units↔tens↔hundreds. Tikunei HaZohar 21. |
| AchasBeta | אח"ס בט"ע | 7/7/7 rotation across three groups; ת invariant. Pardes Rimonim. |

### Word-structure ciphers

| Name | Hebrew | Description |
|------|--------|-------------|
| Boneeh | מספר בונה | Prefix-sum stack per word; resets per word. |
| HaAchor | מספר האחור | Standard value × ordinal position within the word; resets per word. |
| Mityashev | מספר מיושב | Standard value × total letter-count of the word; resets per word. |

### Kolel (additive)

| Name | Hebrew | Description |
|------|--------|-------------|
| KololEhad | כולל | Standard total + 1 (the word counted as one unit). |
| KololOtiyot | כולל אותיות | Standard total + number of letters. Also called Mispar Musafi. |

---

## Architecture

### Data pipeline

```
tanach_corpus.jsonl (bundled)
        │
        ▼
load_corpus_jsonl()         ← VerseFork dataclasses, including cantillated_text
        │
        ▼
_build_connection()         ← @st.cache_resource; builds in-memory SQLite
        │
        ├─ fork_verse()               ← emits Ksiv / Kri / TextVariant rows
        ├─ strip_to_consonants()      ← removes nikud, ta'amim, markers
        ├─ split_halves_by_atnach()   ← Asnachta split → consonants for FirstHalf/SecondHalf
        ├─ split_halves_cantillated() ← Asnachta split → raw cantillated slices (nikud preserved)
        ├─ _tokenize_raw_words()      ← raw cantillated tokens aligned to tokenize_words()
        ├─ detect_paragraph_marker()  ← Pesucha פ / Setuma ס
        ├─ compute_all_ciphers()      ← all 36 values; nikud ciphers get cantillated slice
        └─ build_pattern_log()        ← InternalBalance / ProximityEcho (pre-computed at startup)
```

**Nikud cipher dispatch** inside `compute_all_ciphers`:
- `HaNekudot`, `MiluiNekudot` → called with the cantillated text slice
- `ImHaNekudot` → `g_absolute(consonants) + g_hanekudot(cantillated)`
- `ImMiluiNekudot` → `g_absolute(consonants) + g_milui_nekudot(cantillated)`
- All other ciphers → consonant string only

Cantillated text is threaded to **every row type** — Verse, FirstHalf, SecondHalf, Word, Petucha, Setuma. `_tokenize_raw_words` drops letter-less tokens (paseq ׀, sof-pasuq) before index-aligning with `tokenize_words`, preventing off-by-one misalignment on verses containing paseq.

### SQLite schema

Two tables, built at startup and cached per session:

**`units`** — one row per (verse × boundary × track):

| Column | Notes |
|--------|-------|
| `book`, `chapter`, `verse` | Coordinates |
| `boundary_type` | Word / FirstHalf / SecondHalf / Verse / Petucha / Setuma / Perek / Parsha |
| `track` | Ksiv / Kri / TextVariant / Aggregate |
| `text` | Consonants only |
| `sub_id` | Human-readable ref string, e.g. `"Genesis 1:1 1st-half [Ksiv]"` |
| `Standard` … `KololOtiyot` | All 36 cipher values |

**`patterns`** — detected structural patterns:

| Column | Notes |
|--------|-------|
| `pattern_type` | InternalBalance / ProximityEcho (pre-computed; Cross-Method Echo is queried live) |
| `cipher` | Which method triggered the match |
| `value_a`, `value_b` | The matching values |
| `ref_a`, `ref_b` | Human-readable ref strings (parsed by module-level `parse_pattern_ref`) |
| `detail` | Colel flag or other context |

### Code sections inside `app.py`

Search for `# SECTION N` to jump directly:

| Section | Contents |
|---------|----------|
| 0 | Alphabet, value tables, temurah swap maps, `NIKUD_VALS`, `NEKUDA_NAME_VALS` |
| 1 | 36 cipher functions + `CIPHERS` registry |
| 2 | Text cleaning, structural parsing, Asnachta split, `split_halves_cantillated`, `_tokenize_raw_words` |
| 3 | Variant fork engine (Ksiv/Kri/Esther doublets) |
| 4 | `SAMPLE_CORPUS` + `load_from_sefaria` + `load_corpus_jsonl` |
| 5 | SQLite build (`_build_connection`) |
| 6 | Pattern recognition (`build_pattern_log`) — pre-computes InternalBalance + ProximityEcho |
| 6b | Module-level pattern helpers: `parse_pattern_ref`, `internal_balance_matches`, `proximity_echo_matches`, `whole_unit_echo_matches` |
| 7 | Search: `search_value`, `count_value`, `boundary_population`, `search_value_all_methods` (UNION ALL across all ciphers), `search_phrase`, `_xm_count_matrix` (single-pass 36×36 CASE WHEN aggregation for Tab 1 coincidence matrix) |
| 8 | Stats & visualization helpers |
| 8b | `cipher_breakdown()` — letter-by-letter equation for UI |
| 9 | `run_selftest()` |
| 10 | Streamlit UI (`run_app()`), 4 tabs |

---

## The 4 tabs

### Guide & Sources
Project overview, per-tab descriptions, full cipher reference table with Hebrew names and primary sources, variant (Ksiv/Kri) documentation. Section headings are clickable links that jump to the relevant tab.

### 1 · Phrase & Name Matcher
Search any Hebrew phrase or name across all 36 methods simultaneously. Colel (±1) toggle. Filter by reading track (Written/Read) and boundary type. Click any result row for a full verse detail with letter-by-letter breakdown equation.

**Cross-method coincidences** expander: a 36×36 matrix showing, for every cipher value of the input, how many corpus units match under every other method. Colored by coincidence rate (rarer = warmer). Drill-down selectboxes let you inspect any method-pair in detail.

### 2 · Scriptural Structural Explorer
Browse every verse/word/chapter total by boundary type. Extremes table (highest/lowest/mean/median per boundary). Density gap analysis — value ranges with no verse representation.

**Cell-click match lookup:** click any gematria number cell → the table below immediately shows every unit in the corpus that carries that same number under any of the 36 methods, with a Method column indicating which cipher matched (up to 50 results per method, single UNION ALL query). Click a match row to open the verse detail panel. Clicking a non-cipher cell (Book / Chapter / etc.) shows the verse detail for that row directly.

### 3 · Textual Echoes & Anomalies
Unified pattern interface with live SQL queries — all results auto-update with filter changes.

**Method A / Method B selects** at the top drive all three pattern types. **Cross-method toggle** (off = same-method only; on = all A×B combos). **Colel (±1)** toggle.

Three pattern types (multiselect):
- **Internal Balance** — first half under Method A ≈ second half under Method B (same verse, Asnachta split). Cross-method when the toggle is on.
- **Proximity Echo** — two consecutive verses share a value under Method A.
- **Cross-Method Echo** — any two units anywhere in the Tanach share a value across different methods. Unit type (Verse / Petucha / Setuma) is selectable.

Additional filters: **Min value** (suppress low-value noise; set ≥ 41 to exclude Katan), **Focus** text field (filter results to a book or chapter by string match). **Katan warning** banner appears automatically when Katan is selected without a min-value guard. Metrics (count per pattern type) update with every filter change. Click any result row to render both referenced units with cipher breakdown.

### 4 · Macro Statistical Dashboard
Plotly charts: distribution histograms per method, inter-method correlation heatmap, book-level fingerprint (mean Standard per book). All interactive — hover, zoom, download. **Cross-method half-verse balance heatmap** at the bottom shows, for every method pair, the fraction of verses whose first half (row method) equals the second half (column method).

All charts: scroll zoom disabled (`scrollZoom: false`) to prevent accidental zoom on trackpad/mouse. The Plotly toolbar (including reset-axes house icon) appears on hover in the top-right corner.

---

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# Pre-build the database for fast startup (writes tanach.db, ~133 MB, git-ignored)
python app.py builddb

# Logic check — exits 0 with "=== ALL SELF-TESTS PASSED ==="
python app.py selftest

# Launch the app
streamlit run app.py
```

`builddb` pre-computes all 36 cipher values for 23,206 verses and saves the result to `tanach.db`. If `tanach.db` is present, startup restores it into memory (~1–2s) instead of recomputing from scratch (~25s). The file is git-ignored; the Docker build runs `builddb` automatically so the pre-built DB is baked into the image. The Dockerfile step is non-fatal (`|| echo "..."`) — if the build environment lacks memory, it falls back to runtime computation.

Run `python app.py selftest` after **any** engine change. It verifies Genesis 1:1 = 2701, all 36 ciphers, the Asnachta split, paragraph marker exclusion, Esther doublet (+vav = 6), Kri fork, HaNekudot geometric values, MiluiNekudot Gikatilla spellings, DB build, search round-trips, and the Colel window.

---

## Deployment

### Hugging Face Spaces (current live host)

The app is live at `https://huggingface.co/spaces/TorahNLP/tanach-gematria`.
Pushes to the `space` remote redeploy automatically:

```bash
git remote add space https://huggingface.co/spaces/TorahNLP/tanach-gematria
git push space main
```

The Space reads `requirements.txt` directly. The `README.md` front-matter sets `sdk: streamlit` and `app_file: app.py`.

### Streamlit Community Cloud (alternative)

1. Push to a public GitHub repo.
2. Go to **share.streamlit.io** → sign in with GitHub → **Create app**.
3. Pick the repo, branch `main`, main file `app.py`. Click **Deploy**.

Free tier apps sleep after inactivity; first visitor clicks once to wake (~30s).

---

## Mobile behaviour

- Sidebar starts collapsed (`initial_sidebar_state="collapsed"`) so it doesn't cover a phone screen.
- All charts are Plotly — pinch-zoom and tap-tooltip work on mobile.
- `st.tabs` collapse cleanly on narrow screens.
- Wide tables scroll horizontally on touch.

---

## Known constraints & design decisions

- **In-memory SQLite** — rebuilt per session via `@st.cache_resource`. Fast enough for 23k verses; if corpus grows significantly, switch to an on-disk file.
- **Nikud ciphers on aggregate rows** — Perek and Parsha aggregate rows concatenate consonants only (no cantillated slice), so `HaNekudot`/`MiluiNekudot` = 0 and `Im*` variants = Standard value for those rows. All other row types (Verse, FirstHalf, SecondHalf, Word, Petucha, Setuma) carry real nikud values.
- **`_tokenize_raw_words` alignment** — drops letter-less tokens (paseq ׀, sof-pasuq) identically to `tokenize_words` so cantillated word-slices stay index-aligned. A count mismatch (unexpected) safely falls back to `cantillated=""` for that word.
- **`parse_pattern_ref`** (Section 6b, module-level) parses human-readable ref strings (e.g. `"Genesis 1:1 1st-half [Ksiv]"`) back into (book, chapter, verse, boundary) tuples. Formats: `Book ch:v Nth-half [Track]` → FirstHalf/SecondHalf; `Book ch:v` → Verse.
- **`extremes_table` and the Word boundary** — fetches all word rows into pandas to compute statistics. The aggregate could be pushed to SQL for better performance at scale; acceptable for the current corpus size.
- **Scripture integrity** — paragraph markers `{פ}` / `{ס}` are stripped before any gematria count. The self-test asserts `verse_total == Σ word_totals` to guard this invariant. Never hard-code scriptural text from memory.
- **`_HEATMAP_EXCLUDE`** — `KatanMispari` (only 9 distinct values → always ~100% balance) and `HaMerubahKlali` (hyperscale squared values) are excluded from correlation and balance heatmaps. The 4 nikud ciphers are **not** excluded — they carry real non-zero data on all Verse/FirstHalf/SecondHalf rows used by those charts.

## Removed features

### Macro-Micro Resonance (removed)
Detected verses whose gematria value divided evenly into their containing chapter's total (e.g. verse=300, chapter=900 → x3). Removed because: (1) chapter divisions are a 13th-century Christian invention (Stephen Langton) with no standing as a meaningful unit in classical Jewish textual tradition; (2) no Talmudic, Midrashic, Kabbalistic, or Hasidic source uses verse-to-chapter gematria ratios — classical gematria operates at word/phrase level only; (3) with large chapter totals the divisor relationship occurs frequently by chance. The closest real analogue is the figurate/geometric Torah-numerology school (e.g. 703 nesting inside 2701 in Gen 1:1) and Ivan Panin's Bible Numerics — both interesting but adjacent to this specific construct, not precedent for it. Possibly revisit as a Pesucha/Setuma-scoped analysis (those ARE native Jewish structural units) if a source ever surfaces.
