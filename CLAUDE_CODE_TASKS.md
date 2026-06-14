# Claude Code — Task Brief: test & deploy the Tanach Gematria Engine

Hand this whole file to Claude Code (it's in the project root next to `app.py`).
Work top to bottom. Stop and report if any **Acceptance check** fails.

> **Automation boundary (read first):** You can do Phases 1–4 entirely in the
> terminal. For Phase 5, the *recommended* host (Streamlit Community Cloud) is
> deployed through a browser sign-in you cannot perform — do everything up to the
> GitHub push, then print the click-through steps for the human. If the human
> wants a fully hands-off deploy, use **Phase 5 — Option B (Hugging Face Spaces)**,
> which you can complete from the CLI given a token.

---

## Phase 1 — Environment & self-test

```bash
python3 --version                       # need 3.11+ (tested on 3.12)
python3 -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
python app.py selftest
```

**Acceptance:** the last line is `=== ALL SELF-TESTS PASSED ===`.
If a cipher assertion fails, the math changed — diff against the values
documented in SECTION 0–1 of `app.py` before touching anything.

---

## Phase 2 — Local boot smoke test

```bash
streamlit run app.py --server.headless true --server.port 8501 &
ST_PID=$!
sleep 12
curl -s -o /dev/null -w "boot HTTP %{http_code}\n" http://localhost:8501
kill $ST_PID
```

**Acceptance:** `boot HTTP 200` and no traceback in the streamlit output.

---

## Phase 3 — Verify the Sefaria loader LIVE (the previously-untested piece)

The build sandbox blocked Sefaria's host, so the loader has only ever produced a
well-formed request — its response parsing is unverified. Confirm it on a real
network now.

```bash
python - <<'PY'
import app
verses = app.load_from_sefaria(["Genesis 1"])
print("verses fetched:", len(verses))
assert verses, "Loader returned nothing — inspect the raw response (see below)."
v1 = verses[0]
print("first:", v1.book, v1.chapter, v1.verse, "|", v1.text[:50])
# Engine cross-check: real Genesis 1:1 must strip to the canonical 2701.
print("Gen 1:1 Absolute:", app.g_absolute(app.strip_to_consonants(v1.text)))
assert app.g_absolute(app.strip_to_consonants(v1.text)) == 2701
print("LIVE SEFARIA OK")
PY
```

**Acceptance:** ~31 verses fetched, the text shows cantillation marks, and the
Genesis 1:1 cross-check prints `2701` then `LIVE SEFARIA OK`.

**If it fetched 0 verses or the assert fails — debug the response shape:**

```bash
python - <<'PY'
import urllib.request, json, app
url = app.build_sefaria_url("Genesis 1")
req = urllib.request.Request(url, headers={"User-Agent": "tanakh-gematria/1.0"})
data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
print("top keys:", list(data.keys()))
vs = data.get("versions", [])
print("versions:", len(vs), "first keys:", list(vs[0].keys()) if vs else None)
t = vs[0].get("text") if vs else None
print("text type:", type(t), "| first elem:", repr(t[0])[:80] if isinstance(t, list) and t else t)
PY
```

Sefaria's v3 schema is assumed to be `versions[0].text` = list of verse strings
(see SECTION 4 / HANDOFF §6). If the keys differ, adjust the parsing loop in
`load_from_sefaria()` accordingly, then re-run Phase 3 and Phase 1.

---

## Phase 4 — Commit & push to GitHub

```bash
git init
git add .
git commit -m "Tanach gematria engine: tested, Sefaria loader verified"
git branch -M main
```

Create the remote. If the GitHub CLI is installed and authenticated:

```bash
gh auth status
gh repo create tanakh-gematria --public --source=. --remote=origin --push
```

Otherwise create an empty public repo on github.com, then:

```bash
git remote add origin https://github.com/<USER>/tanakh-gematria.git
git push -u origin main
```

**Acceptance:** the repo is on GitHub and contains `app.py`, `requirements.txt`,
`runtime.txt`, `HANDOFF.md`, `.gitignore`.

---

## Phase 5 — Deploy live

### Option A — Streamlit Community Cloud (recommended; needs the human for the last step)

You cannot perform the browser OAuth. Print these instructions for the human and
stop:

1. Go to **share.streamlit.io** and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub.**
3. Repository: the repo from Phase 4. Branch: `main`. Main file path: `app.py`.
4. Click **Deploy**. First build takes ~2 min; the result is a public
   `https://<name>.streamlit.app` URL that opens on a phone and is shareable with
   no viewer login.

(Free apps sleep when idle; the first visitor after a quiet period clicks once to
wake it — ~30s cold start. Normal.)

### Option B — Hugging Face Spaces (fully CLI; do this if the human wants hands-off)

Requires a Hugging Face account + access token (`HF_TOKEN`). Spaces runs Streamlit
natively and reads `requirements.txt`; it needs a `README.md` with YAML
front-matter. Create it, then push:

```bash
cat > README.md <<'MD'
---
title: Tanach Gematria Engine
emoji: 🔯
colorFrom: indigo
colorTo: gray
sdk: streamlit
app_file: app.py
python_version: "3.12"
pinned: false
---

Multi-cipher Tanach gematria search, structural pattern database, and stats
dashboard. See HANDOFF.md for architecture and maintenance notes.
MD

pip install -U huggingface_hub
export HF_TOKEN=<paste-token>          # or: huggingface-cli login
huggingface-cli repo create tanakh-gematria --type space --space_sdk streamlit -y

git add README.md && git commit -m "Add HF Space metadata"
git remote add space https://huggingface.co/spaces/<HF_USER>/tanakh-gematria
git push space main
```

**Acceptance:** the Space builds and the public
`https://huggingface.co/spaces/<HF_USER>/tanakh-gematria` URL loads the app on
mobile. Watch the build logs in the Space; if a dependency fails to resolve,
loosen the floor in `requirements.txt` and push again.

---

## Definition of done

- [ ] `python app.py selftest` passes.
- [ ] Local boot returns HTTP 200, no traceback.
- [ ] Live Sefaria fetch works and the Genesis 1:1 cross-check prints 2701.
- [ ] Code is pushed to GitHub.
- [ ] A public URL is live and opens correctly on a phone browser
      (tabs reflow, sidebar starts collapsed, histograms stack vertically).
- [ ] Report the final public URL back to the human.

If anything blocks you (missing `gh`/`flyctl`/`HF_TOKEN`, a failing Sefaria
schema, a dependency conflict on the host), stop and report exactly which step
and the error — don't paper over a failed acceptance check.
