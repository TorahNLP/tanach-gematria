# Tanakh Gematria Engine — Build Reference

**Live site:** https://huggingface.co/spaces/TorahNLP/tanach-gematria
**License:** CC BY-NC 4.0
**Stack:** Python 3.12 · Streamlit · SQLite (in-memory) · Plotly · Pandas

---

## What it is

A single-file Streamlit app (`app.py`) that brings together:

- **12 gematria ciphers** over the full 23,206-verse Masoretic Tanakh
- **Structural analysis** — half-verse splits at the Asnachta, Pesucha/Setuma paragraph detection, Ksiv/Kri variant forking
- **Pattern database** — internal half-verse balance, proximity echoes, macro–micro resonances
- **Interactive dashboards** — Plotly histograms, correlation heatmaps, book fingerprints

The corpus is sourced from Sefaria (cantillated Masoretic text) and bundled as `tanach_corpus.jsonl`. Scripture is never typed from memory — wrong letters corrupt every total.

---

## The 12 ciphers

| Name | Hebrew | Description |
|------|--------|-------------|
| Absolute | מספר הכרחי | Standard values: א=1 … ת=400. Finals = base form. |
| Katan | מספר קטן | Drop trailing zeros (ק→1, מ→4), then sum. |
| Gadol | מספר גדול | Like Absolute but finals carry 500–900. |
| Atbash | אתב"ש | Mirror swap א↔ת, ב↔ש … then Absolute. Oldest attested — appears in Jeremiah. |
| Albam | אלב"ם | ROT-11 swap across two 11-letter groups. |
| Atbah | אטב"ח | Pairs summing to 10/100/1000; finals carry 600–900. Attributed to R. Eliezer b. Yose. |
| Avgad | אבג"ד | +1 cyclic shift (א→ב … ת→א), then Absolute. |
| Siduri | מספר סידורי | Ordinal position: א=1 … ת=22. |
| Ribua | מספר מרובע | Sum of squared Absolute values per letter (Σv²). |
| Kidmi | מספר קדמי | Triangular cumulative: each letter = Σ Absolutes from א up to it. |
| Achbi | אכב"י | Split into two 11-letter groups, reverse each internally. |
| HaNikud | מספר הנקוד | Counts dots in vowel marks only (Dagesh excluded). Sheva=2, Hiriq=1, Tsere=2, Segol=3, Patah=1, Kamatz=2, Holam=1, Kubutz=3, Hataf forms=3. Returns 0 for consonant-only text. |

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
        ├─ fork_verse()     ← emits Ksiv / Kri / TextVariant rows
        ├─ strip_to_consonants()   ← removes nikud, ta'amim, markers
        ├─ split_halves_by_atnach()  ← Asnachta split for half-verse rows
        ├─ detect_paragraph_marker() ← Pesucha פ / Setuma ס
        ├─ compute_all_ciphers()    ← all 12 values; HaNikud gets cantillated_text
        └─ build_pattern_log()     ← InternalBalance / ProximityEcho / MacroMicro
```

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
| `Absolute` … `HaNikud` | All 12 cipher values |

**`patterns`** — detected structural patterns:

| Column | Notes |
|--------|-------|
| `pattern_type` | InternalBalance / ProximityEcho / MacroMicro |
| `cipher` | Which method triggered the match |
| `value_a`, `value_b` | The matching values |
| `ref_a`, `ref_b` | Human-readable ref strings (parsed by `_parse_pattern_ref`) |
| `detail` | Colel flag or other context |

### Code sections inside `app.py`

Search for `# SECTION N` to jump directly:

| Section | Contents |
|---------|----------|
| 0 | Alphabet, value tables, temurah swap maps |
| 1 | 12 cipher functions + `CIPHERS` registry |
| 2 | Text cleaning, structural parsing, Asnachta split |
| 3 | Variant fork engine (Ksiv/Kri/Esther doublets) |
| 4 | `SAMPLE_CORPUS` + `load_from_sefaria` + `load_corpus_jsonl` |
| 5 | SQLite build (`_build_connection`) |
| 6 | Pattern recognition (`build_pattern_log`) |
| 7 | Search with Colel window |
| 8 | Stats & visualization helpers |
| 8b | `cipher_breakdown()` — letter-by-letter equation for UI |
| 9 | `run_selftest()` |
| 10 | Streamlit UI (`run_app()`), 5 tabs |

---

## The 5 tabs

### Guide & Sources
Project overview, per-tab descriptions, full cipher reference table with Hebrew names and primary sources, variant (Ksiv/Kri) documentation.

### 1 · Phrase & Name Matcher
Search any Hebrew phrase or name across all 12 methods simultaneously. Colel (±1) toggle. Filter by reading track (Written/Read) and boundary type. Click any result row for a full verse detail with letter-by-letter breakdown equation.

### 2 · Scriptural Structural Explorer
Browse every verse/word/chapter total by boundary type. Extremes table (highest/lowest/mean/median per boundary). Density gap analysis — value ranges with no verse representation.

### 3 · Textual Echoes & Anomalies
Three pattern types:
- **Internal Balance** — both halves of a verse (split at Asnachta) share the same value (Colel ±1 allowed). Renders both halves side-by-side with breakdown math.
- **Proximity Echo** — two consecutive verses match under a given method.
- **Macro–Micro Resonance** — a single verse's value equals its containing chapter total.

Filter by pattern type and/or gematria method. Click a row to see full verse detail with the active cipher's letter equation.

### 4 · Macro Statistical Dashboard
Plotly charts: distribution histograms per method, inter-method correlation heatmap, book-level fingerprint (mean Absolute per book). All interactive — hover, zoom, download.

---

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# Logic check — exits 0 with "=== ALL SELF-TESTS PASSED ==="
python app.py selftest

# Launch the app
streamlit run app.py
```

Run `python app.py selftest` after **any** engine change. It verifies Genesis 1:1 = 2701, all 12 ciphers, the Asnachta split, paragraph marker exclusion, Esther doublet (+vav = 6), Kri fork, DB build, search round-trips, and the Colel window.

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
- **HaNikud on sub-units** — Word and half-verse rows store 0 for HaNikud (no cantillation data at that granularity). Only Verse/Pesucha/Setuma rows carry real nikud counts.
- **`_parse_pattern_ref`** parses human-readable ref strings (e.g. `"Genesis 1:1 1st-half [Ksiv]"`) back into (book, chapter, verse, boundary) tuples. The three formats it handles: `Book ch:v Nth-half [Track]`, `Book ch:v`, and `Perek Book ch` (last returns None — skipped in the UI).
- **`extremes_table` and the Word boundary** — fetches all word rows into pandas to compute statistics. The aggregate could be pushed to SQL for better performance at scale; acceptable for the current corpus size.
- **Scripture integrity** — paragraph markers `{פ}` / `{ס}` are stripped before any gematria count. The self-test asserts `verse_total == Σ word_totals` to guard this invariant. Never hard-code scriptural text from memory.
