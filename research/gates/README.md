# How the 231 gates were reconstructed

These scripts are the evidence behind the gate ciphers in `app.py`. They are not
part of the app and nothing imports them — keep them so the reconstruction can
be re-run and audited rather than taken on trust.

**The short version.** `ספר יצירה ב׳:ד׳` sets the 22 letters in a wheel of
`רל"א שערים`. `פרדס רימונים ל׳:ה׳` prints all 22 alphabets and explains the
number: `רל"א שערים מפני שהם רל"א זוגות` — 231 *pairs*, which is exactly every
possible pairing of two letters from 22. **12 of the 22 printed tables are
garbled**, so the app generates them from the rule instead.

**The rule.** Gate *k* pairs the letters whose alphabet positions sum to *k−1*
(counting round the 22). Exactly two letters are left without a partner; they
take each other.

## The scripts

| file | what it does |
|---|---|
| `pr30_5.txt` | The raw `פרדס רימונים ל׳:ה׳` text, as fetched from Sefaria. All 22 printed tables. |
| `gates231.py` | Parses the printed tables and scores the rule against them. Reports exact reproductions and pair-level agreement. |
| `gates_diff.py` | Shows every disagreement, and **why the printed row is the wrong one** — which letters it repeats and which it omits. |
| `gates_validate.py` | Structural check on the generated family: 11 pairs per gate, all 22 letters once each, perfect involution, 231 distinct pairs. |
| `gates_overlap.py` | Compares all 22 gates against every existing cipher in `app.py`. |

Run any of them with the repo's venv from the repo root.

## What they establish

- **10 of 22** printed tables reproduce **exactly**; **217 of 242** pairs agree.
- The generated family yields **exactly 231 distinct pairs**, matching the
  Remak's own count.
- Every gate is structurally sound: 11 pairs, all 22 letters once, and swapping
  twice returns the original letter.
- **The 12 disagreeing rows are print corruption, and this is provable rather
  than assumed.** A valid alphabet uses each letter exactly once. Every
  disagreeing row **repeats a letter and drops another** — row 2 prints two `ח`
  and no `ט`; row 13 has three duplicates and three absences. Such a row cannot
  be a rival correct system, whatever one thinks of the rule.
- The substitutions are Rashi-script letter confusions: `כ↔נ`, `ג↔נ`, `ח↔ט`,
  `ס↔כ`, `ג↔ר`.

## Two results that shaped the code

- **Gate 22 is Atbash.** Reflection is the one rotation that is also a
  constant-sum pairing, so there is no `Gate22` column — the app shows Atbash in
  its place.
- **Albam is not in the family.** It is a +11 *shift*, so its position sums run
  11, 13, 15 … rather than staying constant. Shifts and gates are different
  constructions; this is why the 231 gates cannot be used to source a shift
  cipher such as `Agdat`.

## Still open

The 12 corrected rows are reconstructed from the rule alone. A cleaner printed
edition would let them be checked against a second witness.
