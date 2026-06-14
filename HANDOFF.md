# Tanakh Gematria Engine — Handoff

A single-file Streamlit app: a multi-cipher Hebrew gematria search engine, a
structural pattern database, and a statistics dashboard over the Tanakh.

This document is written so that **Claude Code** (or any developer) can run it,
deploy it to a public phone-accessible URL, and pick up the open work.

---

## 1. What this is

`app.py` is self-contained. It implements:

- **11 gematria ciphers** — 7 required (Absolute, Katan, Gadol, Atbash, Albam,
  Atbah, Avgad) plus 4 researched additions (Siduri, Ribua, Kidmi, Achbi).
- **Text engine** — strips nikud + ta'amim to the 22 consonants, splits verses
  at the Atnach, parses Petucha/Setuma paragraph markers, tokenizes words.
- **Variant forking** — Ksiv/Kri readings and the Esther 8:11 / 9:27 doublets
  each become their own labelled database row.
- **In-memory SQLite** store, indexed on every cipher and boundary type.
- **Pattern recognition** — internal half-verse balance (with Colel ±1),
  adjacent-verse echoes, and verse-divides-chapter resonances.
- **Search** with an optional Rule-of-the-Colel (±1) window.
- **Dashboards** — extremes ticker, distribution histograms, density gaps.

### Data model — read this before changing anything

The full Masoretic text (23,204 verses) is **deliberately not hard-coded**.
Typing it from memory would inject silent letter errors and poison every total.
Instead:

1. A small, **verified offline `SAMPLE_CORPUS`** (Genesis 1:1–5, the Shema,
   Lev 1:1, both Esther doublets, an illustrative Ksiv/Kri) makes the app run
   with zero network.
2. An optional **`load_from_sefaria()`** loader pulls real cantillated verses
   from the Sefaria API at runtime. Same parsing/cipher/forking pipeline.

The engine is exact; the corpus is honest about its provenance. Preserve that.

---

## 2. File map

| File | Purpose |
|------|---------|
| `app.py` | The entire application + self-test. |
| `requirements.txt` | Runtime dependencies (floors + tested versions). |
| `runtime.txt` | Pins Python 3.12 for Streamlit Community Cloud. |
| `HANDOFF.md` | This file. |

### Code sections inside `app.py` (search for `SECTION N`)

0. Alphabet, value tables, cipher maps
1. The 11 cipher functions + `CIPHERS` registry
2. Text cleaning & structural parsing
3. Variant (Ksiv/Kri/Esther-doublet) fork engine
4. `SAMPLE_CORPUS` + `load_from_sefaria` + `build_sefaria_url`
5. SQLite build
6. Pattern recognition
7. Search (with Colel)
8. Stats & visualization helpers
9. `run_selftest()`
10. Streamlit UI (`run_app()`), 4 tabs

---

## 3. Run it locally

```bash
# from the project directory
python -m venv .venv && source .venv/bin/activate      # optional
pip install -r requirements.txt

# headless logic check — no browser, exits 0 on success
python app.py selftest

# the actual app
streamlit run app.py
# then open the printed Local URL (default http://localhost:8501)
```

`python app.py selftest` is the fast feedback loop. It verifies Genesis 1:1 =
2701, every cipher spot-check, the Atnach split, that paragraph markers are
excluded from totals, the Esther doublet (+vav = 6), the Kri fork, the DB build,
search round-trips, and the Colel window. **Run it after any change to the
engine.**

---

## 4. Deploy to a public, phone-accessible URL

### Recommended: Streamlit Community Cloud (free, simplest)

This gives a public link like `https://<name>.streamlit.app` that anyone can
open on a phone — no login required for viewers — and it redeploys on every
git push. Outbound internet is allowed, so the Sefaria loader works there.

1. Put these files in a **public GitHub repo** (`app.py`, `requirements.txt`,
   `runtime.txt`, `HANDOFF.md`). From this folder:
   ```bash
   git init && git add . && git commit -m "Tanakh gematria engine"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. Go to **share.streamlit.io** → sign in with GitHub → **Create app** →
   **Deploy a public app from GitHub**.
3. Pick the repo, branch `main`, main file `app.py`. Click **Deploy**.
4. First build takes a couple of minutes. You get a shareable `*.streamlit.app`
   URL. Send it to anyone; it opens in mobile Safari/Chrome.

Notes:
- Free apps **sleep after inactivity**; the first visitor sees a "wake" button.
  Fine for sharing, just expect a ~30s cold start.
- To set a custom subdomain, use the app's **Settings → General** in the cloud
  dashboard.

### Alternatives (if you outgrow the free tier)

- **Hugging Face Spaces** — create a Space, SDK = *Streamlit*, push the same
  files. Also free, also a public URL, no sleep on the basic CPU tier.
- **Render / Railway / Fly.io** — general PaaS. Add a start command
  `streamlit run app.py --server.port $PORT --server.address 0.0.0.0` and (for
  Render) a `Procfile` with that line. More control, slightly more setup.

### Custom domain (optional)

Point a CNAME at the platform's hostname per its docs; all three support custom
domains on paid tiers. Not needed just to share a link.

---

## 5. Mobile behaviour

One codebase serves desktop and mobile — no separate build. The mobile-specific
choices already in `app.py`:

- `initial_sidebar_state="collapsed"` so the corpus sidebar doesn't cover a
  phone screen (tap the ☰ to open it).
- Histograms are **stacked vertically** (3 rows × 1 col), so each chart scales
  to the screen width and stays legible instead of three tiny side-by-side plots.
- The four views are `st.tabs`, which collapse cleanly on narrow screens.
- Wide result tables scroll horizontally on touch — expected, not a bug.

If you want to tune further: Streamlit auto-stacks `st.columns` below ~640px, so
existing two-column rows already reflow. Keep new tables narrow where possible.

---

## 6. Open work / things to verify

- **Live Sefaria parsing is unverified.** The build sandbox blocked the host, so
  `load_from_sefaria()` was tested only up to a well-formed request leaving the
  client. On a real network (e.g. once deployed, or locally), confirm the
  response parsing — the code assumes the v3 shape `versions[0].text` is a list
  of verse strings. If Sefaria's schema differs, adjust the loop in SECTION 4.
  Quick local check:
  ```bash
  python -c "import app; vs=app.load_from_sefaria(['Genesis 1']); print(len(vs), vs[0].text[:40] if vs else 'none')"
  ```
- **Cipher conventions** are documented at each function. Atbah uses the strict
  sum-to-10/100/1000 valuation (hundred-class partners carry 600–900). If a user
  expects a different Atbah convention, that's the place to change.
- **Scaling the corpus.** The DB is in-memory and rebuilt per session (cached via
  `@st.cache_resource`). Loading many large books from Sefaria will grow build
  time and memory; if it gets heavy, consider persisting to an on-disk SQLite
  file and loading lazily.
- The `density_gaps` loop walks every integer in the observed value range; fine
  for verses, but don't point it at a boundary whose max value is enormous.

---

## 7. House rules for changes

- After any engine edit, `python app.py selftest` must still pass.
- Never let a paragraph marker (`{פ}`/`{ס}`) reach a gematria total — markers are
  stripped in `strip_to_consonants` before counting, and the self-test guards
  this (verse total must equal the sum of its word tokens).
- Keep the honest-data design: don't hard-code scripture from memory.
