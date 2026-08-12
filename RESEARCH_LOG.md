# Research log — sources and open questions

Working notes on where each method's citation comes from, what was checked
against a primary text, and what is still open. Written 2026-08-12.

**Rule of the road:** every citation in the app should be verifiable against a
primary text. Several supplied citations have turned out to be fabricated —
plausible-looking Hebrew attributed to a real book and chapter that does not
contain it. Check before shipping, and record the check here.

---

## Verification method

Sources are checked by fetching the actual text (Sefaria API v3) and probing for
the quoted phrases. Two traps, both of which have produced wrong answers here:

1. **Vocalized text.** Sefaria's texts are fully pointed and use mid-word
   gershayim. A bare-consonant probe like `אטב` will *not* match `בְּאַטְבַּ״ח`.
   Strip both sides to Hebrew consonants only (`א`–`ת`) before comparing.
   This trap cost a false "not found" on Atbach in Sukkah.

2. **Partial fetches.** A page fetched segment-by-segment can silently come back
   with empty strings on timeout. A missing-text `False` and a genuine `False`
   are indistinguishable. Fetch in parallel with retries, and confirm the
   surrounding context actually reads continuously before trusting a negative.
   This produced a spurious `אם אתה בוש` = False on Shabbat 104a.

A negative result is only evidence if the fetch was complete. Say which.

---

## Verified — safe to rely on

| Claim | Source | Status |
|---|---|---|
| Atbash pairs `א״ת ב״ש ג״ר ד״ק` | שבת ק״ד ע״א | ✅ verified in full text |
| Atbash = `שֵׁשַׁךְ`/`בָּבֶל` | ירמיהו כ״ה:כ״ו, נ״א:מ״א; סנהדרין כ״ב ע״א | ✅ |
| Albam, all 11 pairs | שבת ק״ד ע״א | ✅ verified; matches implementation exactly |
| Albam tabulated | פרדס רימונים ל׳:ה׳ | ✅ |
| Atbach, `סהדה`/`מנון` | סוכה נ״ב ע״ב (R' Chiya) | ✅ |
| 231 gates, `רל״א שערים` | ספר יצירה ב׳:ד׳ | ✅ verbatim |
| Temurah ≠ gematria taxonomy | פרדס רימונים ל׳:א׳ | ✅ verbatim (see below) |
| Avgad (+1 shift) | טעם זקנים (R' Eliezer Ashkenazi) | ✅ |

### Shabbat 104a is the key page

It carries **both** Atbash and Albam, in the same sugya. The page runs the
letter-name mnemonic (`אָלֶף בִּינָה, גְּמוֹל דַּלִּים`) and then works the cipher
pairs themselves under `מדת רשעים` / `מדת צדיקים`:

> `אתבש אם אתה בוש … גר דק … אלבם אם אתה עושה כן`

Earlier note in this project that `אלף בינה` is "a mnemonic, not a cipher" was
too narrow — the page does both and they are continuous.

This makes `TALMUD_CIPHERS` literally true of every member: Standard
(סנהדרין ל״ח), Atbash + Albam (שבת ק״ד), Atbach (סוכה נ״ב), Gadol's 27-letter
sequence (ספר יצירה ב׳:ב׳).

### Pardes Rimonim 30:1 — the taxonomy

Verbatim:

> `והנה מצאנו חלקי הדרוש הזה ג' והם חלק הצרוף וחלק התמורה וחלק הגמטריא`

The Ramak's own three-way split: **tziruf** (permutation) / **temurah**
(substitution) / **gematria** (numerical). Useful as framing for the temurah
group in the Guide. Not yet added to the app.

### Yalkut Shimoni — removed 2026-08-12

Previously cited for Albam (יתרו, רמז רע״א). Cut because the Yalkut is a likut
and Shabbat 104a has the cipher whole; a compilation citing the Gemara adds
nothing behind the Gemara. Decision was: cut regardless, and if it turns out to
be quoting a *midrash* rather than the Gemara, cite that midrash directly on its
own merits. **That check was never completed** — open if anyone wants it.

---

## Fabricated — do not reinstate

| Claimed | Reality |
|---|---|
| PR "Gate 22" for `אגדת` | שער כ״ב contains no `אגדת` |
| PR 30:8 / 30:2 "defines Achorayim" | Neither chapter does |
| PR 30:1 `דע כי האותיות מתחלפות … ב״ג ד״ה` | **Not one phrase** of it appears in the chapter. Read in full (3,640 chars); 30:1 is about tziruf. This was the would-be source for the +2 shift. |

Note the near-miss: PR 30:1 *does* contain a real and useful quote (the taxonomy
above). A fabricated quote attributed to a chapter that contains a *different*
real quote is the hardest kind to catch.

---

## Open: the +2 shift (`Agdat`) — parked

**Status: no source found after four attempts. Parked until the 231-gates work
is done, then decide.**

1. Sefaria search for `אגד"ת` / `אג"דת` → **zero hits**, while controls `אבג"ד`
   and `אלב"ם` return many. The search works; the name is not in the corpus.
2. The PR `ב״ג ד״ה` quote that would have sourced it is fabricated (above).
3. `אג דת הש ור` in PR 30:5 is **real but is not a shift** — it is the third of
   the 22 alphabets of the רל״א שערים, a *pairing* (א↔ג, ד↔ת, ה↔ש). Same four
   letters, different parsing. Do not accept this as a +2 source.
4. Abulafia (`חיי העולם הבא`, `גן נעול`) claimed to construct +1/+2/+3 rings.
   **Unverifiable** — not on Sefaria — and the labels are internally broken:
   `+3` is given as `גדה״ו`, four letters for a three-step shift, and `ג→ד` is a
   +1 step. Reads as constructed, not quoted. Was already dropped from the app
   in Pass 1.

**The asymmetry is the argument.** `Avgad` (+1) is easy to source. If a family
of ordinal shifts were classical, +2 would not be invisible while +1 is not.

Also settled: **231 cannot mean shifts.** Directed maps would give 462; the
Remak's own `רל"א שערים מפני שהם רל"א זוגות` counts *pairs*, = C(22,2) = 231.
So the gates cannot be retrofitted as a +2 source.

Options when we return: cut it, keep it flagged as a modern extension with no
classical source, or leave parked. Recommendation on file: **cut** — it is the
only method of the 35 resting on a citation proven invented.

---

## Open: 231 gates (in progress)

Generative rule recovered and validated three ways:

- Gate *k* pairs letters whose indices sum to *k−1* mod 22; self-paired letters
  join each other.
- 10 of 22 printed tables reproduce **exactly**; 89.7% of pairs overall (the
  other 12 tables are corrupted in the edition used).
- Yields exactly **231 distinct pairs = C(22,2)**, matching the Remak's own count.

Supporting: Ra'avad on ספר יצירה ב׳:ה׳ describes generation by rotation with
skip distances 1–21 — but this produces **pairings, not directed rotations**.

Note ספר יצירה ב׳:ד׳ has the wheel turning `פנים ואחור` (forward and back), not
at arbitrary skip intervals. Readings that gloss this as "rotating across fixed
intervals generates shift rings" are importing the Ra'avad's construction into
the mishnah's words.

**Still to do:** decide display handling for ~22 further methods. Direction
chosen: one method + gate selector, Atbash kept separate (well known, straight
from Tanach) *and* also present in the sub-box. Suggested naming
`כ"ב אלפא ביתות` rather than "231". Ideally verify the 12 corrupted tables
against a cleaner edition.

---

## Open: Atbach `ך` (parked)

27-letter Atbach leaves `ה`, `נ`, `ך` unpaired. `ה↔נ` is forced by the sugya
(`מנון`/`סהדה`). `ך` is under-determined: the Maharsha calls all three mutually
interchangeable but demonstrates only `ה↔נ`; the Maharshal objects that `ך` is
stranded. Currently scored as itself and documented as a choice. Idea parked:
emit two outputs for every `הנ"ך`.

Two girsaos now shipped: **27-letter** (default, as printed in our Rashi, סדר
from ספר הערוך) and **22-letter** (R' Chananel's girsa, cited by the Maharshal).
Aruch LaNer resolves Rashi by reading the verse's word as `סהדה`, the final `ן`
of `מנון` being only how `נ` is written word-finally.
