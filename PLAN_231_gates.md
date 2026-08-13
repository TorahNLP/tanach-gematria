# Plan — the 231 gates (כ״ב אלפא ביתות)  ✅ COMPLETE

> **Shipped 2026-08-12** in `8bc855a` / `40ca9bf`: 21 new methods, 57 total,
> DB rebuilt, live on all four targets. Kept as the record of what was decided
> and why. The shipped state is summarised in `HANDOFF.md`.

Written 2026-08-12, after the research phase closed. Research is **done and
validated**; this file is the build plan. Source detail lives in
`RESEARCH_LOG.md`; the traps that make this risky are in `HANDOFF.md`.

---

## What is being added

The 22 alphabets of the רל״א שערים — `ספר יצירה ב׳:ד׳`, tabulated at
`פרדס רימונים ל׳:ה׳`. Each is a **pairing** cipher on the 22 base letters.

**The rule.** Gate *k* pairs the letters whose alphabet indices sum to
*k−1* (mod 22). Exactly two letters have no partner under that sum (the fixed
points); they take each other.

```python
def gate_pairs(k):                       # k = 1..22
    target = (k - 1) % 22
    seen, pairs, fixed = set(), [], []
    for i, c in enumerate(ALEPH_BET):    # 22 letters, no finals
        if i in seen:
            continue
        j = (target - i) % 22
        if j == i:
            fixed.append(i); continue
        seen.add(i); seen.add(j)
        pairs.append((ALEPH_BET[i], ALEPH_BET[j]))
    if len(fixed) == 2:                  # always exactly 2
        pairs.append((ALEPH_BET[fixed[0]], ALEPH_BET[fixed[1]]))
    return pairs
```

### Validation already performed — do not redo

| check | result |
|---|---|
| Exact reproductions of the printed PR tables | **10 / 22** |
| Pair-level agreement | 217 / 242 = **89.7 %** |
| Distinct pairs across the family | **231** = C(22,2) ✓ |
| All 22 gates structurally valid | ✓ 11 pairs, 22 letters once each, perfect involution |
| Overlap with the existing 35 methods | **only Atbash** (= gate 22) |

**The 12 imperfect rows are print corruption, and this is proven, not assumed.**
A valid alphabet uses all 22 letters exactly once; every mismatched row
**repeats letters and omits others** (row 2 has two `ח` and no `ט`; row 13 has
three duplicates and three absences). They are internally impossible, so they
cannot be a rival correct system. The errors are Rashi-script letter confusions:
`כ↔נ`, `ג↔נ`, `ח↔ט`, `ס↔כ`, `ג↔ר`.

Scripts that produced this: `scratchpad/gates231.py`, `gates_diff.py`,
`gates_validate.py`, `gates_overlap.py`. Worth keeping — see step 6.

### Two facts that shape the design

- **Atbash == gate 22.** It stays its own top-tier entry (well known, straight
  from Tanach) *and* appears as gate 22. Deliberate duplication, not an error.
- **Albam is NOT in the family.** Its index sums are `[11,13,15,17,19,21,…]` —
  it is a *shift*, not a constant-sum pairing. Atbash qualifies only because
  reflection happens to be constant-sum. This is independent confirmation that
  shifts and gates are different constructions, and it is why the `Agdat`
  question stays separate.

---

## Build steps

### 1. ⚠️ Append to `CIPHER_NAMES` — never reorder

`CIPHER_NAMES` is the **DB column order**. Rows insert as a positional tuple
against `CIPHER_INSERT_COLS`, so reordering writes every value into the wrong
column — and a prebuilt DB keeps loading with every number silently wrong.

The 21 new ciphers (gates 1–21; gate 22 is the existing Atbash) go at the
**end** of `CIPHERS`. Naming: `Gate01` … `Gate21`, zero-padded so lexical and
numeric order agree.

Decision on gate 22: **do not add a duplicate column.** Atbash already stores
those values. The gate selector maps 22 → the `Atbash` column.

### 2. Cipher functions

Each gate is a substitution map, then standard values summed. Follow the
existing `ATBASH_MAP` / `ALBAM_MAP` pattern — build the 21 maps from
`gate_pairs(k)` at import rather than pasting literal tables, so the rule stays
the single source of truth and cannot drift from the tables.

Finals fold to their base letter (the gates are defined on 22 letters).

### 3. Display

- `CIPHER_DISPLAY_ORDER` — the gates form their own group at the **end**, after
  the Kolel family. This is display only and safe to reorder.
- `CIPHER_DISPLAY_NAMES` — `שער א׳ (Gate 1)` … `שער כ״א (Gate 21)`.
- The method picker is **already a multiselect** (`"Show matches for method(s)"`),
  so 1 / several / all works with no new UI. That was the requirement.

### 4. Condensed result blocks (Joshua's spec)

Ordinary methods render `<h4 class='mhead'>` + `<p class='mblurb'>` + value line.
For 22 gates that is far too heavy. Gates get a **compact header**: the gate
number and its swap table, no big title, no per-method blurb.

```
שער ז׳ · אז בו גה חת טש יר כק לצ מפ נע דס
Value 407 · 12 results
[table]
   📜 Verse detail   <- already renders here, inside the block
```

One shared explanatory blurb sits above the gate group, not repeated 22 times.

**Verse detail already renders inside each method's own block**, immediately
below that method's table (`app.py` ~6533). Joshua's requirement — detail under
its own box rather than at the page bottom — is existing behaviour; just do not
break it when adding the compact path.

### 5. Rebuild the DB

`tanach.db` is gitignored and built by `python app.py builddb`. The four deploy
targets are in `HANDOFF.md` → Deployment. **The local Tailscale instance does
not update on push — it needs the process restarted.**

Rebuild once, after the ciphers are final. Joshua's note: *"if there is any time
to remake the db it's now."*

### 6. Keep the derivation

Move the four scratchpad scripts into the repo (suggest `research/gates/`) so
the reconstruction can be re-run and audited. They are the evidence for the 12
corrected rows.

### 7. Selftest

Pin the structural invariants, which are strong enough to catch any regression:

- all 22 gates: 11 pairs, 22 distinct letters, perfect involution
- the family yields exactly 231 distinct pairs
- `gate_pairs(22)` equals `ATBASH_MAP` restricted to base letters
- `gate_pairs(k)` is never equal to `ALBAM_MAP` for any k

Also pin one worked value per gate against a hand-checked word.

### 8. Method counts are DERIVED

`HANDOFF.md` already warns: never write the literal count. Adding 21 methods
changes `N_CIPHERS` everywhere it is interpolated. Confirm no literal "35"/"36"
has crept back in.

---

## Deliberately deferred

- **Method grouping in the picker.** Joshua: *"revisit grouping once the 231
  project is complete."* 21 more entries make the list long; that is the
  motivation, but it is separate work.
- **`Agdat` (+2 shift).** Parked pending its own decision. Unaffected by this —
  the gates cannot source it, since directed shifts would give 462, not 231.
- **The 12 corrupted PR rows.** Reconstructed with confidence, but a cleaner
  edition would let us confirm the readings against a second witness rather than
  against the rule alone.
