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
