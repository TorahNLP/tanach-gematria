# Plan — Nikud tool (auto-vowelize a word or phrase)

**Status:** proposed, not started. Written 2026-08-05.
**Supersedes:** the older "auto-nikud" sketch (corpus lookup + ONNX model).

---

## What it is

A separate page: type a Hebrew word or phrase, get it back vocalized, edit any
word's nikud from a list of attested options, then copy it out or send it
straight to the gematria search.

The reason it is a *tool* and not a search-box feature: in the search box you
must resolve to ONE vocalization to compute a value, which forces a guess. In a
standalone tool the ambiguity is just information to show.

## Why it matters

The four vowel-mark methods (HaNekudot, ImHaNekudot, MiluiNekudot,
ImMiluiNekudot) return 0 — or, worse, silently equal Standard — on bare
consonants. Anyone typing a name gets no vowel-mark result at all.

---

## Interaction

1. **Type a word or phrase.** The box fills in with the most likely
   vocalization for every word it can resolve.
2. **Each word is individually editable** — click it, pick another attested
   form from a list showing occurrence counts and the values it produces.
3. **Copy button** for the finished string.
4. **"Search this" button** hands the vocalized text to Tab 1, closing the loop
   the feature exists for.

Auto-fill is a *default, never a commitment*. That is what makes the tool safe
for phrases like `בן הכות הרשע`, where בן is not relational at all — the auto
pick can simply be overridden word by word.

---

## Ranking: what "most likely" means

⚠️ **Collapse candidates by VALUE, not by spelling.** Since the dagesh scores 0
(see [nikud sourcing]), dagesh-only variants are numerically identical and must
not be offered as separate choices — they are one option with two spellings.

Measured effect on the real corpus:

| | raw spellings | after collapsing |
|---|---|---|
| variants across all words | 52,481 | 50,142 |
| words with more than one option | 8,339 (21.3%) | **7,347 (18.8%)** |

On the words that actually matter:

| word | spellings | real options | top share |
|---|---|---|---|
| **בת** | 2 | **1** | 100% — no choice at all |
| **דוד** | 3 | 2 | 99.3% (was 78%) |
| **בן** | 5 | 3 | 91.9% |
| חנה | 4 | 4 | 60% |
| כלב | 5 | 4 | 68% |

**Ranking rule:** most frequent surviving option wins the auto-fill; show every
option with its count and share so a thin default is visible as thin. חנה at
60% and מאיר at a single corpus occurrence must not look like settled answers.

---

## Word roles

Detect the name-phrase shape (`X בן/בת/בר Y`) and treat the roles differently.
**The name list is consulted first in every position** — a relational word is
still overridable, because בן is not always relational.

| Role | Source order | Notes |
|---|---|---|
| Given name | name list → corpus | most typed case |
| Relational בן/בת/בר | fixed correct form, still editable | בֶּן / בַּת; בן has 3 real options, but in a name phrase בֶּן is right |
| Anything else | corpus | ordinary word |
| Not found | flagged | see "words not in Tanach" |

---

## Data sources

**1. Corpus index** — built from `tanach_corpus.jsonl`, NOT the DB.
⚠️ `tanach.db` stores **no pointed text**: `text_display` is bare consonants and
the nikud lives only in the JSONL. This is the piece the old plan assumed away.
Index shape: `{bare_consonants: {pointed_form: count}}`, ~39,184 entries, built
once and cached.

⚠️ Strip cantillation with an explicit character set, NOT the range
`[֑-ֽ]` — that range swallows the nikud itself (U+05B0–U+05BC) and
silently yields consonants. This bug cost real time twice; see the corrected
form in `measure_nikud.py`.

**2. Name list** — to be curated:
   - *Tanach names*: generable from the corpus with their attested nikud.
     Fully pointed, authoritative, free. This half is straightforward.
   - *Non-Tanach names* (Yiddish/modern: זעלדא, גיטל, בריינדל …): **this half
     has no ready source. Checked and rejected: Harkavy 1925.**

### ⚠️ Harkavy 1925 was investigated and is NOT usable — do not retry it

The Harkavy Yiddish-English-Hebrew Dictionary name lists (via bloodandfrogs.com)
look ideal: 561 vocalized names, male and female, exactly the Yiddish coverage
the corpus lacks, from a real published lexicon. They are not usable here.

**Measured letter-vowel coverage: 30% mean, vs 62% for the Tanach corpus.
Only 1 of 561 names is fully pointed.**

| name | Harkavy | coverage |
|---|---|---|
| שרה | שָׂרה | **0%** — that mark is a SIN DOT, which the engine excludes as consonantal, so this yields NO vowel value at all |
| מאיר | מֵאיר | 25% |
| חיה | חַיה | 33% |
| משה | מָשה | 33% |
| יהודית | יְהוּדית | 33% |

Harkavy pointed for **Yiddish readers**, who need only enough diacritics to fix
pronunciation — not full Tiberian vocalization. The lists are genuine; they just
do not answer this question.

⚠️ **`has_unpointed_word` cannot detect this.** It asks "does this word carry
ANY nikud", which is right for its original job (Sefaria prints Ksiv words
entirely bare) but passes all 561 Harkavy names. A partial-pointing check would
need to be per-letter, not per-word. Worth knowing before trusting that function
to validate an imported list.

**So the non-Tanach half remains a curation task**, not a scrape: someone must
decide זעלדא is `זֶעלְדָא`. Options: hand-curate a starter set of the most common
Yiddish names, or accept "not in Tanach → vowel-mark methods unavailable" for
them (see below).

---

## Words not in Tanach

`זעלדא` was 1 of 32 in a realistic name sample. Options, in order of honesty:

1. Name list covers it (the point of curating one).
2. Flag as unvocalized and say the four vowel-mark methods are unavailable for
   that word — consistent with how `nikud_partial` already handles units whose
   vowel data is incomplete.
3. A model guesses (the old "layer 2"). **Deferred, and the case is weaker than
   it looked**: a modern-Hebrew model guessing one vocalization for a word the
   Masoretic corpus itself renders five ways is more confident than the data
   supports. Revisit only if the name list proves insufficient in practice.

---

## Placement

- A page on the **site**, reached the way `?page=guide` already is.
- **Linked from app view** like Guide & Sources, so the PWA can reach it.
- Fifth surface to keep current across four deploy targets — worth noting in
  HANDOFF.

---

## Open questions

1. **Name-list scope** — how many non-Tanach names for v1? A starter list for
   Joshua to correct, or does he supply it?
2. **Multi-word names** — `בן ציון` as a single given name vs `בן` relational.
   Detect, or leave to per-word editing?
3. **Should the tool show gematria values** beside each option? It has them
   already (that is how collapsing works), and they are the reason a reader
   would care which form is chosen.
