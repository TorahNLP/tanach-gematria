---
title: Tanach Gematria Engine
emoji: 📜
colorFrom: indigo
colorTo: gray
sdk: docker
pinned: false
---

Multi-method Hebrew gematria search engine, structural pattern database, and
statistical dashboard over the Tanach.

**35 gematria methods**, in families: direct-value, substitution (temurah),
name-expansion (Milui / Neelam / Emtzaiyot, ± Maleh spellings), positional,
vowel-mark (nikud), combined, and sequential/kolel. Each is listed in the app's
Guide with the earliest source known for it.

**Full corpus:** 23,206 cantillated Masoretic verses, bundled.

**English translation:** optional per-verse translation in the verse-detail panel
and print-out (off by default). Display only — it takes no part in any gematria
calculation, and is always shown for the whole verse rather than sliced to match
a sub-verse unit.

## Texts & licences

| Text | Edition | Licence |
|---|---|---|
| Hebrew (all calculations) | *Tanach with Ta'amei Hamikra*, from [tanach.us](http://www.tanach.us/Tanach.xml) via [Sefaria](https://www.sefaria.org) | Public Domain |
| English (display only) | *The Koren Jerusalem Bible*, © Koren Publishers Jerusalem, via Sefaria | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |

The translation is CC-BY-NC: attribution is a condition of the licence, and the
app renders it wherever the English appears, including in exported documents.
Re-licensing this project commercially would require swapping the translation
for the public-domain JPS 1917 (one-line change in `fetch_english.py`, then
refetch). Joshua 21:36–37, which Koren omits, are filled from the
public-domain JPS 1917 — see the note on those verses below.

**Joshua 21:36–37** are present in the Hebrew corpus (tanach.us) but absent
from most Masoretic manuscripts; the same material appears at I Chronicles
6:63–64. ArtScroll footnotes them as "not part of the original Masoretic text
of Joshua", and Koren and *Miqra according to the Masorah* omit them. They are
kept and scored here — silently dropping verses is worse than showing them —
and the verse-detail panel flags them so a total including them is never
mistaken for undisputed.

**Features:** Atnach-based half-verse splitting · Ksiv/Kri + Masoretic textual
variant forking (Itture Sopherim, Esther doublets) · Colel (±1) search ·
pattern detection (internal balance, proximity echoes, macro-micro resonances) ·
correlation & fingerprint heatmaps · letter-by-letter breakdown visualization ·
Guide & Sources tab with method sources and variant docs.

See `BUILD.md` for architecture and build reference.

This application is licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/); see
**Texts & licences** above for the licences of the bundled texts.

## Notes on method selection and implementation

Detail that belongs with the source rather than in the app's Guide.

**A blank Source means the hunt is still open, not that the method is
invented.** Every citation in the Guide has been checked against the primary
text, and where a claimed source turned out not to say what was attributed to
it, the citation was removed rather than softened. Eight rows currently stand
blank:

| method | position |
|---|---|
| מספר סידורי, קטן מספרי, אחור סידורי | Real and standard in modern tables, but the name is not in the rishonim or in Pardes Rimonim. `מספר סידורי` is a 19th-century *grammatical* term (ordinal number, contrasted with `מספר יסודי`); its gematria sense is later. Searched under fifteen phrasings across ~523,000 characters of primary text. |
| אכב"י | A standard temurah that appears in traditional lists. Both cited sources were read and neither supports it: ספר רזיאל המלאך prints no cipher grids at all (not even for Atbash, which it demonstrably uses), and the Radal hit was a signature line. |
| נעלם, אמצעיות, אופנים | Classical methods whose sources could not be reached. Pardes Rimonim's `נעלם`, `אמצעית` and `אופן` are ordinary words in unrelated passages, not these methods. |

**אגד"ת, the +2 shift, has been removed from the method list.** Its original
citation, "Pardes Rimonim Gate 22", was checked and שער כ״ב contains no `אגדת`;
the `אג דת` line at ל׳:ה׳ is the third of the 231 gates, a *pairing* rather than
a rotation. The +1 shift is well attested — `כוזו במוכסז כוזו` on the mezuzah,
and the Arizal deriving `בוכ"ו` from `אהיה` `בחילוף אלפא ביתא דאבג"ד` — and −1
is attested too, `טדהד` in בן יהוידע and שער רוח הקודש. Nothing comparable
turned up for +2. The map, function and self-tests are retained, so it can be
reinstated the moment a source appears.

The likeliest place for the missing attestations is material outside the corpora
searchable here — 18th–20th century kabbalistic lexicons and gematria manuals.
Specific untested leads: קהלת יעקב (Yolles), תורה שלמה vol. 17 (Kasher, 1956),
גנת אגוז (HebrewBooks 9423), and טעם זקנים.

**Verification standard.** Correct arithmetic is not attestation. Several claims
checked during this work computed exactly right and were still misattributed —
`סלם`=40 ordinally is arithmetically true but the Baal HaTurim gives seven
derashos there, all standard gematria on 130. Every source line here was
confirmed by reading the cited text, not by reproducing the number.

**The 231 Gates are generated from the rule, not transcribed from the print.**
Sefer Yetzirah 2:4 fixes the 22 letters in a wheel of רל"א שערים, and Pardes
Rimonim ל׳:ה׳ prints all 22 alphabets. The Remak gives the reason for the
number: רל"א שערים מפני שהם רל"א זוגות — 231 *pairs*, which is exactly C(22,2),
every possible pairing of two letters from 22. That count is also why these are
pairings rather than shifts: 22 directed rotations would give 462.

The rule recovered from it: gate *k* pairs the letters whose alphabet positions
sum to *k*−1, counting round the 22; the two letters left without a partner take
each other.

⚠️ **12 of the 22 printed tables in the edition used are garbled**, so the app
generates the tables from the rule instead. This is demonstrable rather than a
preference: a valid alphabet uses each of the 22 letters exactly once, and every
disagreeing row **repeats a letter and drops another** — row 2 prints two ח and
no ט, row 13 has three duplicates and three absences. Such a row cannot be read
as it stands. The substitutions are Rashi-script letter confusions (כ↔נ, ג↔נ,
ח↔ט, ס↔כ, ג↔ר).

The rule reproduces 10 of the 22 printed rows **exactly**, agrees on 217 of 242
pairs, and yields exactly 231 distinct pairs — matching the Remak's own count.
Gate 22 turns out to be Atbash, which keeps its own name and column, so the app
ships 21 gate methods rather than 22. Albam is *not* in the family: it is a +11
shift, so its position sums run 11, 13, 15 … rather than staying constant.

The derivation scripts and the raw Pardes Rimonim text are in `research/gates/`,
so the reconstruction can be re-run and audited rather than taken on trust. A
cleaner printed edition would let the 12 corrected rows be checked against a
second witness; that has not been done.

**Mispar HaMispari follows the Remak's own orthography.** Pardes Rimonim, Gate 30
§8 gives two worked totals — yud → עשרה = 575 and heh → חמשה = 353 — and only the
masculine spellings (עשרה, חמשה, שלשים) reproduce them. Online calculators
generally use the feminine/modern forms (עשר, ארבע, שלושים) and therefore differ
from this app on **13 of the 22 letters**. That is deliberate: the primary source
is preferred over the popular table. Values here will not match those tools.

**Mispar HaMispari HaGadol (שער ל׳ §9) is partly reconstructed.** The rule is
not in doubt — name each letter's *milui* total rather than its standard value
— and the Remak's worked example reproduces exactly: yud's milui יוד = 20, and
עשרים = 620 = כתר. That example is the only checksum the method has, and it is
pinned in `run_selftest`.

The difficulty is that he spells only that one number, and it is not a
compound. 15 of the 22 milui totals *are* compounds (alef 111, bet 412 …), so
their orthography is set here by `compose_number_name` rather than by him.
Three decisions, and what each rests on:

| Decision | Choice | Basis |
|---|---|---|
| Constituent order | hundreds first | Idiom only — gematria is a sum, so order **cannot change any value** |
| Joining | conjunctive vav (מאה ועשרים) | Dominant biblical form; 27 corpus attestations |
| Eleven | אחד עשר | The living Rabbinic form; עשתי עשר is a frozen archaism of priestly/architectural contexts |

Only the latter two move a total, and each by a few points. Everything else
carries over from the §8 `NUMBER_NAMES` table, which his two worked totals fix.

An argument that this method could *not* be responsibly reconstructed was
examined and rejected. Its headline claim — that constituent order was one of
four fatal unknowns — is worth nothing, because addition commutes
(מאה ואחד עשר and אחד עשר ומאה are both 635). Its second claim, that only one
of 22 letters had determinable orthography, ignored that §8 and §9 draw on the
same number-naming convention, one already shipped.

**The honest objection is about scope, not spelling:** the Remak brings §9 on a
single letter as a remez, not as a cipher for summing running text. Using it
that way extends his rule further than he took it, and the Guide says so on the
method's own row. Precedent for shipping a reconstruction is `ImMiluiNekudot`,
which likewise declares no single classical source.

**Hundreds break the masculine rule, deliberately.** `NUMBER_NAMES` follows the
Remak's anchors for 1–90 (masculine: עשרה, חמשה) but biblical idiom for the
hundreds (feminine: שלש מאות, never שלשה מאות — Hebrew number-gender is
inverted, and מאות is feminine). Two rules in one table, differing by 5 points
per hundreds row. He is silent on 300/400, so neither is his ruling; this is
recorded so it is not mistaken for an oversight and "corrected".

**Mispar Mityashev is not offered.** No classical source could be found for the
method under that name: מספר מיושב appears nowhere in Pardes Rimonim's Sha'ar
HaGematriaot (Gate 30 or 22) and returns no hits across Sefaria's corpus. The
function and its self-tests are retained in `app.py` so it can be reinstated in
one line if a source turns up. Note that some sources use "mispar meyushav" to
mean *Mispar Katan*, which is a different calculation.

**The Colel (±1) toggle** matches `target−1`, `target` and `target+1` (SQL
`BETWEEN`), ordering results by proximity (`ABS(cipher − value)`); the
internal-balance detector likewise flags half-verses equal within ±1 as
`colel±1`. Four methods are exempt and stay exact — `KololEhad`/`KololOtiyot`
(the adjustment *is* the method), `KatanMispari` (a digital root has 9 values, so
±1 spans a third of the space), `HaMerubahKlali` ((S+1)² ≠ S²+1) and `HaNekudot`
(all mark values are even, so an odd target can never match). Enforced in
`search_value`, `count_value`, `search_value_all_methods` and `_xm_count_matrix`.

## Developer documentation

Docs live on the **`docs` branch**, not here — HuggingFace rebuilds and restarts
the Space on any push to `main`, so doc-only edits used to cost a few minutes of
downtime. `HANDOFF.md` (read first), `BUILD.md` and `CLAUDE_CODE_TASKS.md` are
there. Check them out beside the code with:

```bash
git worktree add ../tanakh-docs docs
```
