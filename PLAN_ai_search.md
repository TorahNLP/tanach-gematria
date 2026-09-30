# Plan: Occasion Search (AI assistant optional, later)

**Status:** approved in outline, 2026-09-29. **Phase 0 and a first slice of
Phase 1 are live, PIN-gated** (`3615460`; see HANDOFF "Occasion search"): the
form, name forms, same- and cross-method matching, ranking v1. Next up: parsha
and haftara data, dates, and tuning the ranking with Joshua. **Build the form
first** (Joshua: "this may be most of what we need"). The AI chat is Phase 3,
and may never be needed.
**Scope:** a new page on the Tailscale copy, the one maintained deployment
(see "Deployment change" below). Existing pages are unchanged.

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

Two things decide whether this works: ranking, and keeping the model away from facts.

**Search speed is not a blocker.** Measured on the self-hosted PC (i7-12700, no
GPU), 2026-09-29: one value across all 57 methods, over the fixed units, takes
**0.3 s**. Free word spans take **~11 s** per method per value, but **Joshua
ruled spans low priority (2026-09-29)**. A random run of words is the least
likely place to find a coherent match, which is why the engine is built on the
fixed units in the first place: verse, the half verse at the אתנחתא, and the
segments at זקף and the other divisions. Occasion Search searches **fixed units
only**. Spans stay out, or at most behind an explicit opt-in, and a faster span
index is parked until someone asks for it.

1. **Ranking is the real problem.** One value across all methods returns
   **~2,400 rows** (613: 2,448; 1,234: 2,310). Multiply by the permutations and
   there are hundreds of thousands of "matches", and with enough methods any name
   matches something. The value of the feature is choosing the few worth showing
   and being honest about why. See "Ranking".

2. **The model must never compute a value or state a source.** This project has
   already had a fabricated citation (`Agdat`) and misattributed ones (six פרדס
   רימונים rows). A small model will invent both. The engine computes every
   number; every method explanation and citation is pasted in by code from the
   Guide data. The model only chats, gathers details and chooses which tools to
   call.

Given (2), most of the value is in a deterministic layer that works with no AI at
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

## Occasions (list approved by Joshua 2026-09-29)

Every occasion is built from the same few blocks: **people with roles**, **one or
more dates**, and **optional text**. An occasion is configuration: which fields
it shows, which name forms it generates by default, and which date signals it
uses. It is not new code.

| Occasion | People | Dates / extras | Default name form |
|---|---|---|---|
| Birth · bris | child; father; mother; who the child is named after; surname | birth (after sunset?); bris date; that week's parsha + haftara | `x בן/בת y` |
| Pidyon haben | child; father | day 31 | `x בן y` |
| Upsherin / chalakah | child; parents | 3rd birthday | `x בן y` |
| Bar / bat mitzvah | child; parents; grandparents | Hebrew birthday; **parsha + haftara** | `x בן/בת y` |
| Engagement · wedding · sheva brachos | chosson; kallah; both sets of parents; surnames | vort/tenaim date; wedding date; parsha + haftara | each alone; `x ו־y` together |
| Anniversary | couple | wedding date; years | couple together |
| Yahrzeit · hesped · matzeivah | the niftar; father; spouse | date of passing; yahrzeit | `x בן/בת y` (father) |
| Refuah shleimah / tefillah | the person; mother | — | `x בן/בת z` (mother) |
| Siyum | the one making it; the masechta/sefer | date | name + masechta name |
| Chanukas habayis / new business / dedication | family or business name | date; street number | name alone |
| Birthday | person; parents | Hebrew birthday | `x בן/בת y` |
| Conversion (Hebrew name) | new name | date | `x בן/בת אברהם אבינו` |
| Name pasuk (end of Shemoneh Esrei) | one name | — | first/last letter rule, not gematria |

The default name forms follow common custom (mother's name for tefillah,
father's for a matzeivah or an aliyah). **Joshua decides these**; they are
starting points, not rulings. Simchas bas and "naming" were dropped at
Joshua's request, since "birth" covers them.

## Cross-method matching is core (Joshua, 2026-09-29)

Name under method A = verse under method B (for example Standard → Atbash) is
one of the most common kinds of gematria match, and it is tedious in the app
today: Tab 1's "🔀 Cross-method matches" gives a count matrix
(`_xm_count_matrix`) that has to be drilled into cell by cell. Occasion Search
runs cross-method pairs as a first-class search:
- Each name form is valued under every method in the chosen depth, and each
  value is searched against the verses' values under every method in that
  depth. The existing batched matrix query already does 57×57 in ~1.5 s.
- **Pair tiers** for ranking: same method, then the classic pairs (Standard ↔
  Atbash / Albam / Katan …), then both methods basic, then one basic, then
  the rest. Which pairs count as "classic" is Joshua's call; a short
  configurable list, not hard-coded.
- The output names both sides plainly: "your name in Standard = this verse in
  Atbash".

## Parsha and haftara scopes

For a birth, bar/bat mitzvah or wedding, search **within that week's parsha
and haftara** as well as all of Tanach, and rank those hits up.
⚠️ **The data is not there yet:**
- The corpus has **no parsha field** (see `shape_result_columns`: the old
  "Parsha" column held the book name on all 571,521 rows). Parsha verse ranges
  need a static table (54 parshiyos plus the combined weeks), built once from
  a known source and checked in.
- **Haftaros differ by custom** (Ashkenaz / Sefard / Chabad / Teiman …), and
  special Shabbosos replace the regular one (Rosh Chodesh, Machar Chodesh, the
  four parshiyos, Shabbos Chanukah, Shuva …). Ask the user's custom and apply
  the date's special-haftara rules; never silently default. Build and verify
  the table against a primary source (a Chumash/luach), not memory.
- Date → parsha needs Israel vs chutz la'aretz (the reading diverges after a
  Yom Tov that falls on Shabbos). Ask.

## Architecture

```
chat page (Streamlit st.chat_*)          <- Tailscale copy only, at first
   │  small local model, tool calling    <- llama.cpp / Ollama on the PC
   ▼
occasion layer (pure Python, no Streamlit)
   ├─ name resolution: bare/English → pointed Hebrew (existing nikud tool + index)
   ├─ permutation generator
   ├─ Hebrew date layer
   ├─ batched search over fixed units (verse, אתנחתא, זקף …)
   ├─ ranking
   └─ templated explanations (from Guide/method data)
   ▼
existing engine: tanach.db, CIPHERS, TALMUD_CIPHERS / COMMON_CIPHERS tiers
```

The "basic vs esoteric" depth control already exists in effect: `TALMUD_CIPHERS`,
`COMMON_CIPHERS` and the grouped `_DISPLAY_GROUPS`, with the gates last.

## Phases

### Phase 0: engine API
- Pull the search code the new layer needs into functions importable without
  Streamlit. They must stay the same functions the site calls, not copies, so
  the two cannot drift.
- Batch the fixed-unit search: every name form's value in one query per method
  tier rather than one query per value.
- ~~Fast span index~~: parked. Spans are low priority (see Verdict).
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
- **Unit:** whole verse > half verse > smaller fixed segment. Exact > kolel.
- **Name form:** full natural form > partial > unusual combination.
- **Agreement:** the same verse hit by several forms or methods ranks up.
- **Context:** the week's parsha for a birth or wedding date; famous or
  thematically fitting verses. Keep this small, and never let it outrank the
  structural signals.

Show a handful. Say how many were found in total, so a hit is not presented as
rarer than it is.

## Decisions for Joshua

1. ✅ **Form first** (decided 2026-09-29). It may be all that is needed.
2. ✅ **Tailscale is the one maintained deployment** (decided 2026-09-29). See
   "Deployment change".
3. ~~English names~~: **low priority** (Joshua, 2026-09-29): it was cited as a
   perk of an AI, not a need for the target audience.
4. **Which name forms count**, e.g. mother's name in `בן` forms (as for
   tefillah), surname, kinnuim? Your call on custom.
5. ✅ **Classic pairs** = both methods in `TALMUD_CIPHERS` (Joshua: "the Chazal
   sourced ones", 2026-09-29). Ranking scores each side by tier.
6. **Default haftara custom**, if any, and Israel vs chutz la'aretz default.

## Future, separate project: Hebrew / yeshiva-facing UI

Joshua is considering a Hebrew-first UI aimed at a yeshiva audience, possibly
as a toggle the way Sefaria does it. **Not part of this project**, but Occasion
Search should keep every user-facing string in one table from the start, so a
Hebrew toggle later is a translation job, not a rewrite.

## Deployment change (decided 2026-09-29)

The Tailscale copy becomes the only maintained deployment, matched to GitHub
`main`. Hugging Face and Streamlit Cloud stop being maintained. The GitHub
Pages loader (`torahnlp.github.io/tanach-gematria/`) iframes the **Streamlit
Cloud** app (`tanach-gematria.streamlit.app`), so it goes with it or must be
repointed. HF history is **identical** to GitHub (same 245 commits on `main`,
no Space discussions), so nothing needs saving from it.
