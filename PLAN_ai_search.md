# Plan: Occasion Search + AI assistant

**Status:** proposal, 2026-09-29. Nothing built. Needs Joshua's decisions (bottom).
**Scope:** a new page. Existing pages are unchanged.

## The idea (Joshua, 2026-09-29)

A chat page with a small self-hosted model. The user says what they want
("a pasuk for our new baby", "something for a wedding"), and the bot asks for what
it needs: names, parents' names, family names, surname, dates. It then runs the
engine's searches over every sensible permutation (`x בן y`, `x בן y וz`, with or
without the surname, and so on), at a chosen depth, from basic methods only up to
all 57. It returns the verse, the translation when wanted, and an explanation of
the method in the method's own terms. It also accepts unpointed or English names,
and ties in Hebrew dates.

## Verdict: doable, but the AI is the easy part

Three things decide whether this works, and none of them is the model:

1. **Span search is too slow to run many times.** Measured on the self-hosted PC
   (i7-12700, no GPU), 2026-09-29:
   - one value across all 57 methods, whole units: **0.3 s**
   - one value, one method, word spans up to 6: **~11 s**

   A baby search could easily need 30 name forms × 57 methods. Run span by span,
   that is hours. It has to become a batched scan: prefix sums per method with
   numpy, all target values checked in one pass. Expected cost is roughly a
   second for everything. This is engineering, not research.

2. **Ranking is the real problem.** One value across all methods returns
   **~2,400 rows** (613: 2,448; 1,234: 2,310). Multiply by the permutations and
   there are hundreds of thousands of "matches", and with enough methods any name
   matches something. The value of the feature is choosing the few worth showing
   and being honest about why. See "Ranking".

3. **The model must never compute a value or state a source.** This project has
   already had a fabricated citation (`Agdat`) and misattributed ones (six פרדס
   רימונים rows). A small model will invent both. The engine computes every
   number; every method explanation and citation is pasted in by code from the
   Guide data. The model only chats, gathers details and chooses which tools to
   call.

Given (3), most of the value is in a deterministic layer that works with no AI at
all. So build that first.

## On the method-variant problem (Achas Beta, Atbach)

A chat UI does not settle which girsa is correct; nothing but sources does that.
What it does fix is **display cost**. Today each variant is another entry in a
57-item picker. If variants become first-class sub-methods, each with its own
source, the bot can search all of them and say "under the אור זרוע reading of
אח״ס בט״ע …". The Achas Beta `ת` question then stops being all-or-nothing: the
engine can carry the witnessed tails as named variants instead of choosing one.
That data-model change also helps the ordinary picker, where method grouping is
still a deferred item.

## Architecture

```
chat page (Streamlit st.chat_*)          <- Tailscale copy only, at first
   │  small local model, tool calling    <- llama.cpp / Ollama on the PC
   ▼
occasion layer (pure Python, no Streamlit)
   ├─ name resolution: bare/English → pointed Hebrew (existing nikud tool + index)
   ├─ permutation generator
   ├─ Hebrew date layer
   ├─ batched search (new span index)
   ├─ ranking
   └─ templated explanations (from Guide/method data)
   ▼
existing engine: tanach.db, CIPHERS, TALMUD_CIPHERS / COMMON_CIPHERS tiers
```

The "basic vs esoteric" depth control already exists in effect: `TALMUD_CIPHERS`,
`COMMON_CIPHERS` and the grouped `_DISPLAY_GROUPS`, with the gates last.

## Phases

### Phase 0: engine API and fast spans
- Pull the search code the new layer needs into functions importable without
  Streamlit. They must stay the same functions the site calls, not copies, so
  the two cannot drift.
- Batched span index: for each method, word values → prefix sums → every span of
  length 2..N checked against a *set* of targets in one pass. Verify it gives the
  same rows as `span_search` on a sample of values and methods before anything
  uses it.
- Keep the thread-local connection rule (see HANDOFF "Never share a sqlite
  connection").

### Phase 1: "Occasion Search" page (no AI)
A form: occasion (baby / bar-bat mitzvah / wedding / other), names, parents,
surname, dates, depth (Talmud-attested / common / all).
- **Name resolution.** Unpointed input goes through the nikud tool's lookup.
  English input needs an English-variants column in the name index, a lookup
  table rather than a guess (Reuben / Reuven / Ruvi → ראובן). Anything ambiguous
  (Ari → ארי or אריה) is shown as a choice, never picked silently.
- **Permutations**, generated and labelled so the output can say which form
  matched: `x`; `x בן/בת y`; `x בן/בת y וz`; with and without the surname;
  Yiddish kinnuim pairs from the index (צבי הירש). Wedding: each side, both
  names together, `x ו־y`. Cap and dedupe by value, but keep the labels.
- **Dates.** A Hebrew date library (e.g. `pyluach`). ⚠️ **After nightfall is the
  next Hebrew day**: ask whether a birth was after sunset rather than assume.
  Date strings in standard form (`י״ב אדר תשפ״ו`) are searched as text like
  names. The week's parsha is a ranking signal (see below).
- **Name-pasuk custom.** A verse beginning with the name's first letter and
  ending with its last is cheap to find and widely used. Verify the source
  before it gets a citation (HANDOFF "Citations").
- **Output.** A short ranked list: verse, pointed text, which name form, which
  method, value. Translation is behind a toggle. The explanation is built from
  the method's Guide row and its actual letter-by-letter work for *this* name,
  like the existing query-side calculation display.

### Phase 2: model bake-off (on the PC, offline)
- Serve with llama.cpp or Ollama, auto-started like `Start_Gematria.vbs` and
  covered by the watchdog. The PC rebooted on 2026-09-29, so it must come back
  unattended.
- Candidates: 3–8B instruct models with tool calling at 4-bit quantization,
  plus a Hebrew-tuned one (DictaLM). Pick by test, not reputation. The field
  moves quickly, so re-survey current small models when this phase starts.
- CPU-only speed estimate: very roughly 5–15 tokens/s. **Measure it.**
- Fixed test set of about 30 scripted conversations, scored on: Hebrew names
  copied exactly (the main risk for a small model); the right tool calls with
  the right arguments; asking for missing details instead of inventing them;
  never stating a number or source the tools did not return.

### Phase 3: chat page
- New `?view=app&page=ask` beside Search / Guide / Nikud, Tailscale copy first.
- The model's tools are the Phase 1 functions. It never sees the DB.
- Every name is echoed back for confirmation before searching.
- Explanations and citations in answers come from code, inserted verbatim.
- No chat history stored server-side (family names are personal data), and
  nothing persistent on the client (no service worker, per standing decision).

### Phase 4: later, only if wanted
- Variants as first-class sub-methods (Achas Beta tails, Atbach girsaos).
- Date ↔ method-number links ("gate 12 on the 12th"). **Only where a source
  makes the connection**; otherwise it is numerology the engine would be
  inventing. Off by default.
- Hugging Face: a CPU model on the free `cpu-basic` Space (2 vCPU, 16 GB,
  already holding the corpus) would be slow and fragile under concurrent
  users. Either keep chat to the self-hosted copy, or use a hosted API model
  there, which trades away "self-hosted" and needs a cost and privacy decision.

## Ranking (the design work)

Score each hit on:
- **Method tier:** Talmud-attested > common > the rest > gates.
- **Unit:** whole verse > half verse > short span > long span. Exact > kolel.
- **Name form:** full natural form > partial > unusual combination.
- **Agreement:** the same verse hit by several forms or methods ranks up.
- **Context:** the week's parsha for a birth or wedding date; famous or
  thematically fitting verses. Keep this small, and never let it outrank the
  structural signals.

Show a handful. Say how many were found in total, so a hit is not presented as
rarer than it is.

## Decisions for Joshua

1. **Build Phase 1 (form, no AI) first?** Recommended: it is most of the value,
   it is testable, and it is exactly what the bot needs as tools.
2. **Chat on the Tailscale copy only, at least at first?** Recommended.
3. **English names:** a curated variants table (recommended) or model guesses?
4. **Which name forms count**, e.g. mother's name in `בן` forms (as for
   tefillah), surname, kinnuim? Your call on custom.
