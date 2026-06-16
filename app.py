# -*- coding: utf-8 -*-
"""
Tanach Gematria Search Engine, Structural Pattern Database & Statistical Visualizer
===================================================================================

A self-contained Streamlit application implementing:

  * A complete, correct multi-cipher gematria engine (11 ciphers).
  * Real consonant-cleaning (strips nikud + ta'amim, keeps the 22 base letters).
  * Asnachta-based half-verse splitting and Petucha/Setuma paragraph parsing.
  * A Ksiv/Kri + Masoretic textual-variant forking engine (Itture Sopherim,
    Esther doublets — see TEXTUAL_VARIANT_SPECS).
  * An in-memory, fully indexed SQLite store.
  * Proximity / internal-balance pattern recognition.
  * A Colel ("plus/minus one") aware search engine.
  * Pandas/Matplotlib/Seaborn macro-statistical dashboards.

CORPUS NOTE
-----------
The Masoretic text of the Tanach runs to 23,206 verses. Scripture is never
hard-coded from memory (wrong letters → wrong gematria → a beautiful but
worthless tool). The text is supplied in order of priority:

  (1) tanach_corpus.jsonl — 23,206 cantillated verses pre-fetched from
      Sefaria and bundled with the app. Loaded automatically at startup.
  (2) SAMPLE_CORPUS — a small verified fallback (Genesis 1:1-5, the Shema,
      variant examples) used only when the JSONL file is absent.
  (3) load_from_sefaria() — appends individual references on demand.
      Same parsing/cipher/forking pipeline as paths (1) and (2).

Run:  streamlit run app.py
Self-test (no Streamlit UI):  python app.py selftest
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# SECTION 0.  HEBREW ALPHABET, VALUE TABLES & CIPHER MAPPINGS
# ---------------------------------------------------------------------------

# The 22 base consonants in canonical order (positions 1..22).
ALEFBET = "אבגדהוזחטיכלמנסעפצקרשת"

# Final (sofit) forms -> their base letter.
FINAL_TO_BASE = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
FINALS = set(FINAL_TO_BASE.keys())

# Every Hebrew consonant we will ever keep after cleaning.
HE_CONSONANTS = set(ALEFBET) | FINALS

# Standard absolute values (Mispar Hechrachi / Mispar Yaschar): Alef=1 .. Tav=400.
_STD_SEQUENCE = [1, 2, 3, 4, 5, 6, 7, 8, 9,
                 10, 20, 30, 40, 50, 60, 70, 80, 90,
                 100, 200, 300, 400]
STANDARD: Dict[str, int] = {ALEFBET[i]: _STD_SEQUENCE[i] for i in range(22)}

# Mispar Gadol final-letter values (500..900).
GADOL_FINALS = {"ך": 500, "ם": 600, "ן": 700, "ף": 800, "ץ": 900}

# Ordinal values (Mispar Siduri): Alef=1 .. Tav=22.
ORDINAL: Dict[str, int] = {ALEFBET[i]: i + 1 for i in range(22)}

# Triangular / cumulative values (Mispar Kidmi a.k.a. Meshulash):
# each letter = sum of standard values of every letter up to and including it.
#   Alef=1, Bet=1+2=3, Gimel=1+2+3=6, ... Tav=sum(all)=1495.
_kidmi_running = 0
KIDMI: Dict[str, int] = {}
for _ch in ALEFBET:
    _kidmi_running += STANDARD[_ch]
    KIDMI[_ch] = _kidmi_running


def _normalize_final(ch: str) -> str:
    """Map a final form to its base letter; leave base letters unchanged."""
    return FINAL_TO_BASE.get(ch, ch)


# --- Temurah substitution maps (all defined over the 22 base letters) --------

# Atbash (א"ת ב"ש): mirror the alphabet  (Alef<->Tav, Bet<->Shin, ...).
ATBASH_MAP = {ALEFBET[i]: ALEFBET[21 - i] for i in range(22)}

# Albam (א"ל ב"ם): ROT-11 cyclic swap  (Alef<->Lamed, Bet<->Mem, ...).
ALBAM_MAP = {ALEFBET[i]: ALEFBET[(i + 11) % 22] for i in range(22)}

# Avgad (א"ב ג"ד): +1 Caesar shift  (Alef->Bet, ..., Tav wraps -> Alef).
AVGAD_MAP = {ALEFBET[i]: ALEFBET[(i + 1) % 22] for i in range(22)}

# Agdat (אגד"ת): +2 Caesar shift  (Alef->Gimel, ..., Tav wraps -> Bet).
AGDAT_MAP: Dict[str, str] = {ALEFBET[i]: ALEFBET[(i + 2) % 22] for i in range(22)}

# Achbi (א"כ ב"י): split the 22 letters into two halves of 11 and reverse each
# half internally (Alef<->Kaf, Bet<->Yod, ... ; Lamed<->Tav, Mem<->Shin, ...).
ACHBI_MAP: Dict[str, str] = {}
for i in range(11):                       # first half indices 0..10
    ACHBI_MAP[ALEFBET[i]] = ALEFBET[10 - i]
for i in range(11, 22):                   # second half indices 11..21
    ACHBI_MAP[ALEFBET[i]] = ALEFBET[32 - i]

# Atbah (א"ט ב"ח): pairwise substitution whose pairs sum to 10, 100 or 1000.
#   units (sum 10):    Alef-Tet, Bet-Het, Gimel-Zayin, Dalet-Vav, He-He(self)
#   tens  (sum 100):   Yod-Tzadi, Kaf-Pe, Lamed-Ayin, Mem-Samech, Nun-Nun(self)
#   hundreds (sum 1000): Qof-Tzadi(final), Resh-Pe(final), Shin-Nun(final),
#                        Tav-Mem(final)
# The hundred-class partners are final forms valued at their HUNDREDS value
# (600..900), so the defining sum-to-1000 relation is preserved exactly:
#   Qof(100)+900=1000, Resh(200)+800=1000, Shin(300)+700=1000, Tav(400)+600=1000.
ATBAH_MAP = {
    "א": "ט", "ט": "א", "ב": "ח", "ח": "ב", "ג": "ז", "ז": "ג",
    "ד": "ו", "ו": "ד", "ה": "ה",
    "י": "צ", "צ": "י", "כ": "פ", "פ": "כ", "ל": "ע", "ע": "ל",
    "מ": "ס", "ס": "מ", "נ": "נ",
    "ק": "ץ", "ר": "ף", "ש": "ן", "ת": "ם",
}

# Value of each letter's Atbah partner. Final-form partners take their Gadol
# (hundreds) value so that letter + partner sums to 10 / 100 / 1000 exactly.
ATBAH_VALUE = {
    ch: (GADOL_FINALS[ATBAH_MAP[ch]] if ATBAH_MAP[ch] in GADOL_FINALS
         else STANDARD[ATBAH_MAP[ch]])
    for ch in ALEFBET
}


# Milui (מילוי): primary letter-name spellings (Lurianic standard convention).
# Each letter's name is spelled as a Hebrew word; Milui sums Standard values of
# all spelling letters. Neelam (hidden) drops the first letter of each spelling.
LETTER_NAME_SPELLING: Dict[str, str] = {
    "א": "אלף",  "ב": "בית",  "ג": "גימל", "ד": "דלת",  "ה": "הא",
    "ו": "ואו",  "ז": "זין",  "ח": "חית",  "ט": "טית",  "י": "יוד",
    "כ": "כף",   "ל": "למד",  "מ": "מם",   "נ": "נון",  "ס": "סמך",
    "ע": "עין",  "פ": "פא",   "צ": "צדי",  "ק": "קוף",  "ר": "ריש",
    "ש": "שין",  "ת": "תיו",
}

def _spelling_val(spelling: str) -> int:
    """Sum Standard values of a letter-name spelling (handles finals)."""
    return sum(STANDARD.get(FINAL_TO_BASE.get(c, c), 0) for c in spelling)

MILUI_VALS: Dict[str, int]  = {k: _spelling_val(v)     for k, v in LETTER_NAME_SPELLING.items()}
NEELAM_VALS: Dict[str, int] = {k: _spelling_val(v[1:]) for k, v in LETTER_NAME_SPELLING.items()}

# Nikud (vowel-point) dot counts for Mispar HaNikud.
# Only the 12 standard vowel points (U+05B0–U+05BB) are counted; dagesh,
# meteg and shin/sin dots are excluded. A string without nikud scores 0.
NIKUD_DOTS: Dict[str, int] = {
    "ְ": 2,  # Sheva — two stacked dots
    "ֱ": 3,  # Hataf Segol — sheva-pair + one segol-dot
    "ֲ": 3,  # Hataf Patah — sheva-pair + patah stroke
    "ֳ": 3,  # Hataf Kamatz — sheva-pair + kamatz stroke
    "ִ": 1,  # Hiriq — single dot
    "ֵ": 2,  # Tsere — two horizontal dots
    "ֶ": 3,  # Segol — three dots in triangle
    "ַ": 1,  # Patah — one horizontal stroke
    "ָ": 2,  # Kamatz — horizontal + vertical = two strokes
    "ֹ": 1,  # Holam — single dot above
    "ֺ": 1,  # Holam haser for vav — single dot
    "ֻ": 3,  # Kubutz — three diagonal dots
}


def _katan_digit(value: int) -> int:
    """Reduce a standard letter value to a single significant digit.

    Strips trailing zeros: 400->4, 200->2, 90->9, 20->2, 10->1, 7->7.
    """
    while value >= 10 and value % 10 == 0:
        value //= 10
    return value


# ---------------------------------------------------------------------------
# SECTION 1.  THE GEMATRIA ENGINE  (one pure function per cipher)
# ---------------------------------------------------------------------------
#
# Every function receives an ALREADY-CLEANED consonant string (22 base letters
# plus, possibly, final forms) and returns an integer. They never raise on
# stray characters: anything outside the value tables contributes 0.

def g_absolute(s: str) -> int:
    """Mispar Hechrachi / Yaschar - standard absolute value."""
    return sum(STANDARD.get(_normalize_final(c), 0) for c in s)


def g_katan(s: str) -> int:
    """Mispar Katan - each letter reduced to its significant digit, then summed."""
    return sum(_katan_digit(STANDARD.get(_normalize_final(c), 0)) for c in s)


def g_gadol(s: str) -> int:
    """Mispar Gadol - final forms valued 500..900, others standard."""
    total = 0
    for c in s:
        if c in GADOL_FINALS:
            total += GADOL_FINALS[c]
        else:
            total += STANDARD.get(_normalize_final(c), 0)
    return total


def g_siduri(s: str) -> int:
    """Mispar Siduri - ordinal value (Alef=1 .. Tav=22)."""
    return sum(ORDINAL.get(_normalize_final(c), 0) for c in s)


def g_ribua(s: str) -> int:
    """Mispar Meruba Prati (Ribua) - sum of each standard value squared."""
    return sum(STANDARD.get(_normalize_final(c), 0) ** 2 for c in s)


def g_kidmi(s: str) -> int:
    """Mispar Kidmi (HaKadmon) - triangular cumulative value per letter."""
    return sum(KIDMI.get(_normalize_final(c), 0) for c in s)


def _temurah_value(s: str, mapping: Dict[str, str]) -> int:
    """Substitute each letter via a temurah map, then take the Standard value."""
    total = 0
    for c in s:
        base = _normalize_final(c)
        swapped = mapping.get(base, base)
        total += STANDARD.get(_normalize_final(swapped), 0)
    return total


def g_atbash(s: str) -> int:
    """Atbash (א"ת ב"ש) - mirror swap, then standard value."""
    return _temurah_value(s, ATBASH_MAP)


def g_albam(s: str) -> int:
    """Albam (א"ל ב"ם) - ROT-11 swap, then standard value."""
    return _temurah_value(s, ALBAM_MAP)


def g_avgad(s: str) -> int:
    """Avgad (א"ב ג"ד) - +1 shift, then standard value."""
    return _temurah_value(s, AVGAD_MAP)


def g_atbah(s: str) -> int:
    """Atbah (א"ט ב"ח) - sum-to-10/100/1000 substitution.

    Each letter is replaced by its Atbah partner's value; hundred-class partners
    carry their full 600..900 value so letter+partner sums to exactly 10/100/1000.
    """
    return sum(ATBAH_VALUE.get(_normalize_final(c), 0) for c in s)


def g_achbi(s: str) -> int:
    """Achbi (א"כ ב"י) - reversed-half swap, then standard value."""
    return _temurah_value(s, ACHBI_MAP)


def g_nikud(text: str) -> int:
    """Mispar HaNikud - count the dots in each vowel-point (nikud) mark.

    Operates on raw (cantillated / vocalised) text, not stripped consonants.
    Returns 0 for consonant-only strings — correct behaviour.
    """
    return sum(NIKUD_DOTS.get(ch, 0) for ch in text)


def g_agdat(s: str) -> int:
    """Agdat (אגד"ת) - +2 cyclic shift, then standard value."""
    return _temurah_value(s, AGDAT_MAP)


def g_katan_mispari(s: str) -> int:
    """Mispar Katan Mispari - sum Standard values first, then reduce total to digital root."""
    total = g_absolute(s)
    while total >= 10:
        total = sum(int(d) for d in str(total))
    return total


def g_milui(s: str) -> int:
    """Mispar Milui (מילוי) - spell each letter's full name, sum all spelling letters."""
    return sum(MILUI_VALS.get(_normalize_final(c), 0) for c in s)


def g_neelam(s: str) -> int:
    """Mispar Neelam (נעלם) - like Milui but drop the first letter of each name (hidden portion)."""
    return sum(NEELAM_VALS.get(_normalize_final(c), 0) for c in s)


def g_meshulash(s: str) -> int:
    """Mispar Meshulash - sum of running prefix sums: v1 + (v1+v2) + (v1+v2+v3) + ..."""
    total = 0
    running = 0
    for c in s:
        running += STANDARD.get(_normalize_final(c), 0)
        total += running
    return total


def g_kaful(s: str) -> int:
    """Mispar Kaful - each letter's Standard value × its ordinal position in the unit."""
    total = 0
    pos = 0
    for c in s:
        v = STANDARD.get(_normalize_final(c), 0)
        if v:
            pos += 1
            total += pos * v
    return total


def g_mityashev(s: str) -> int:
    """Mispar Mityashev - each letter's Standard value × total letter count in the unit."""
    vals = [STANDARD.get(_normalize_final(c), 0) for c in s
            if STANDARD.get(_normalize_final(c), 0)]
    n = len(vals)
    return sum(v * n for v in vals)


def g_kolel_ehad(s: str) -> int:
    """Mispar Kolel (Word) - Standard total + 1 (the word counted as a single unit)."""
    return g_absolute(s) + 1


def g_kolel_otiyot(s: str) -> int:
    """Mispar Kolel (Letters) - Standard total + count of letters in the unit."""
    n = sum(1 for c in s if STANDARD.get(_normalize_final(c), 0))
    return g_absolute(s) + n


# Ordered registry of every cipher. The order here is the column order used
# throughout the database and the UI.
# NOTE: HaNikud operates on cantillated text; all others take consonants.
# compute_all_ciphers handles the dispatch so callers use a uniform API.
CIPHERS: Dict[str, Callable[[str], int]] = {
    "Standard": g_absolute,     # Mispar Hechrachi / Yaschar      (required)
    "Katan": g_katan,           # Mispar Katan (reduced)          (required)
    "Gadol": g_gadol,           # Mispar Gadol (final 500-900)    (required)
    "Atbash": g_atbash,         # א"ת ב"ש                          (required)
    "Albam": g_albam,           # א"ל ב"ם                          (required)
    "Atbah": g_atbah,           # א"ט ב"ח                          (required)
    "Avgad": g_avgad,           # א"ב ג"ד                          (required)
    "Siduri": g_siduri,         # Mispar Siduri (ordinal)         (researched)
    "Ribua": g_ribua,           # Mispar Meruba Prati (squared)   (researched)
    "Kidmi": g_kidmi,           # Mispar Kidmi / HaKadmon         (researched)
    "Achbi": g_achbi,           # א"כ ב"י temurah variant         (researched)
    "HaNikud": g_nikud,         # Mispar HaNikud (nikud dots)     (researched)
    "Agdat": g_agdat,           # אגד"ת +2 shift                  (researched)
    "KatanMispari": g_katan_mispari,  # digital-root of total     (researched)
    "Milui": g_milui,           # Mispar Milui (filled names)     (researched)
    "Neelam": g_neelam,         # Mispar Neelam (hidden portion)  (researched)
    "Meshulash": g_meshulash,   # Mispar Meshulash (prefix sums)  (researched)
    "Kaful": g_kaful,           # Mispar Kaful (pos × value)      (researched)
    "Mityashev": g_mityashev,   # Mispar Mityashev (val × count)  (researched)
    "KololEhad": g_kolel_ehad,  # Kolel +1 (word unit)            (researched)
    "KololOtiyot": g_kolel_otiyot,  # Kolel +N (letter count)     (researched)
}
CIPHER_NAMES: List[str] = list(CIPHERS.keys())

# Display labels for cipher selector widgets. Internal names stay as short
# CIPHER_NAMES keys (Python dicts, SQL columns); these labels are used only
# in interactive selectors via format_func, never as DB column names.
CIPHER_DISPLAY_NAMES: Dict[str, str] = {
    "Standard": "Standard — מספר הכרחי",
    "Katan":    "Katan — מספר קטן",
    "Gadol":    "Gadol — מספר גדול",
    "Atbash":   "Atbash — אתב\"ש",
    "Albam":    "Albam — אלב\"ם",
    "Atbah":    "Atbah — אטב\"ח",
    "Avgad":    "Avgad — אבג\"ד",
    "Siduri":   "Siduri — מספר סידורי",
    "Ribua":    "Ribua — מספר מרובע",
    "Kidmi":    "Kidmi — מספר קדמי",
    "Achbi":        "Achbi — אכב\"י",
    "HaNikud":      "HaNikud — מספר הנקוד",
    "Agdat":        "Agdat — אגד\"ת",
    "KatanMispari": "Katan Mispari — קטן מספרי",
    "Milui":        "Milui — מילוי",
    "Neelam":       "Neelam — נעלם",
    "Meshulash":    "Meshulash — מספר משולש",
    "Kaful":        "Kaful — מספר כפול",
    "Mityashev":    "Mityashev — מספר מיושב",
    "KololEhad":    "Kolel (Word) — כולל",
    "KololOtiyot":  "Kolel (Letters) — כולל אותיות",
}

# Human-readable one-liners shown next to each cipher selector in the UI.
CIPHER_BLURB: Dict[str, str] = {
    "Standard": "Standard values — א=1, ב=2 … י=10, כ=20 … ת=400. Summed.",
    "Katan":    "Reduced values — drop trailing zeros (ק→1, מ→4), then sum.",
    "Gadol":    "Like Standard, but final forms count higher: ך=500 … ץ=900.",
    "Atbash":   "Mirror swap: א↔ת, ב↔ש, ג↔ר … then Standard values of swapped letters.",
    "Albam":    "ROT-11 swap: א↔ל, ב↔מ, ג↔נ … then Standard values of swapped letters.",
    "Atbah":    "Pairs summing to 10/100/1000: א↔ט, ב↔ח … ק↔ץ. Finals carry 600–900.",
    "Avgad":    "+1 cyclic shift: א→ב, ב→ג … ת→א. Then Standard values of shifted letters.",
    "Siduri":   "Ordinal position: א=1, ב=2, ג=3 … ת=22. Sequence, not value.",
    "Ribua":    "Sum of squared values: Σ v² per letter.",
    "Kidmi":    "Triangular cumulative: each letter = sum of all Standard values up to it. א=1, ב=3 … ת=1495.",
    "Achbi":        "Reverse each half of the alphabet: א↔כ, ב↔י … ל↔ת, מ↔ש … Then Standard.",
    "HaNikud":      "Counts the dots inside each vowel mark (nikud) — not the consonants themselves.",
    "Agdat":        "+2 cyclic shift: א→ג, ב→ד … ש→א, ת→ב. Then Standard values of shifted letters.",
    "KatanMispari": "Sum all Standard values first; then reduce the total to a single digital root.",
    "Milui":        "Spell each letter's full name (א=אלף=111, ב=בית=412 …); sum all spelling letters.",
    "Neelam":       "Like Milui but drop the first letter of each name — only the hidden remainder (א→לף=110 …).",
    "Meshulash":    "Stacked prefix sums: v₁ + (v₁+v₂) + (v₁+v₂+v₃) + … Grows with word length.",
    "Kaful":        "Each Standard value × its ordinal position in the unit (1st×v₁ + 2nd×v₂ + …).",
    "Mityashev":    "Each Standard value × total letter count in the unit: Σ(vᵢ × N).",
    "KololEhad":    "Standard total + 1 (the word counted as one collective unit).",
    "KololOtiyot":  "Standard total + number of letters in the unit (one per letter).",
}

# Friendly display labels for variant tracks and boundary types in the UI.
TRACK_LABELS: Dict[str, str] = {
    "Ksiv":        "Written (כְּתִיב)",
    "Kri":         "Read (קְרֵי)",
    "TextVariant": "Textual variant",
    "Aggregate":   "Chapter / Parsha total",
}
BOUNDARY_LABELS: Dict[str, str] = {
    "Word":       "Word (תיבה)",
    "FirstHalf":  "First half-verse (before Asnachta)",
    "SecondHalf": "Second half-verse (after Asnachta)",
    "Verse":      "Verse (פסוק)",
    "Perek":      "Chapter (פרק)",
    "Parsha":     "Torah portion (פרשה)",
    "Petucha":    "Open paragraph (Pesucha פ)",
    "Setuma":     "Closed paragraph (Setuma ס)",
}


def compute_all_ciphers(consonants: str, cantillated: str = "") -> Dict[str, int]:
    """Return {cipher_name: value} for a cleaned consonant string.

    HaNikud is dispatched to `cantillated` (if provided) rather than
    consonants, so it reflects the actual vowel-dot count. When
    `cantillated` is empty, HaNikud returns 0 (correct for consonant-only
    strings).
    """
    result = {}
    for name, fn in CIPHERS.items():
        if name == "HaNikud":
            result[name] = fn(cantillated)
        else:
            result[name] = fn(consonants)
    return result


# ---------------------------------------------------------------------------
# SECTION 2.  TEXT CLEANING & STRUCTURAL PARSING
# ---------------------------------------------------------------------------

ATNACH = "\u0591"          # HEBREW ACCENT ETNAHTA - marks the verse's major split
MAQAF = "\u05BE"           # HEBREW PUNCTUATION MAQAF (word joiner)
SOF_PASUQ = "\u05C3"       # HEBREW PUNCTUATION SOF PASUQ

# Paragraph markers as they appear in scribal / Sefaria text. We treat ONLY the
# bracketed / parenthesised / sof-pasuq-adjacent isolated forms as structural,
# never a bare pe/samekh that is part of a word.
_PETUCHA_RE = re.compile(r"[\{\(]\s*פ\s*[\}\)]|׃\s*פ(?=\s|$)")
_SETUMA_RE = re.compile(r"[\{\(]\s*ס\s*[\}\)]|׃\s*ס(?=\s|$)")
# Matches every isolated structural-marker form (braced/parenthesised, OR a bare
# pe/samekh sitting just after a Sof Pasuq). Used to delete markers BEFORE
# consonant extraction so that a פ/ס marker can never leak into a gematria total.
_MARKER_STRIP_RE = re.compile(r"[\{\(]\s*[פס]\s*[\}\)]|׃\s*[פס](?=\s|$)")


def strip_to_consonants(text: str) -> str:
    """Remove nikud, ta'amim, punctuation, markers - keep only the 22 letters.

    Structural paragraph markers ({פ}/{ס} and the Sof-Pasuq form) are deleted
    FIRST, so a marker's pe/samekh never contaminates a gematria total. Final
    forms are preserved as written (needed for Mispar Gadol); other ciphers
    normalise them internally.
    """
    text = _MARKER_STRIP_RE.sub(" ", text)
    return "".join(ch for ch in text if ch in HE_CONSONANTS)


def detect_paragraph_marker(text: str) -> Optional[str]:
    """Return 'Petucha', 'Setuma' or None for a verse-trailing scribal marker."""
    if _PETUCHA_RE.search(text):
        return "Petucha"
    if _SETUMA_RE.search(text):
        return "Setuma"
    return None


def tokenize_words(text: str) -> List[str]:
    """Split a verse into word tokens (on whitespace + maqaf), cleaned to
    consonants, dropping empty/marker-only tokens."""
    no_markers = _MARKER_STRIP_RE.sub(" ", text)
    raw = re.split(r"[\s" + re.escape(MAQAF) + r"]+", no_markers)
    words = []
    for tok in raw:
        cons = strip_to_consonants(tok)
        if cons:
            words.append(cons)
    return words


def split_halves_by_atnach(text: str) -> Tuple[str, str]:
    """Split a (vocalised/cantillated) verse into first/second half by Asnachta.

    First half  = start .. through the word bearing the Asnachta mark.
    Second half = remainder up to Sof Pasuq.
    If no Asnachta is present, the whole verse is treated as the first half.
    Returns (first_half_consonants, second_half_consonants).
    """
    idx = text.find(ATNACH)
    if idx == -1:
        return strip_to_consonants(text), ""
    # Extend the split to the end of the Asnachta-bearing word so we don't sever
    # a word in the middle.
    end = idx
    while end < len(text) and text[end] not in (" ", "\t", MAQAF, SOF_PASUQ):
        end += 1
    first = strip_to_consonants(text[:end])
    second = strip_to_consonants(text[end:])
    return first, second


# ---------------------------------------------------------------------------
# SECTION 3.  VARIANT (KSIV / KRI / ESTHER-DOUBLET) FORK ENGINE
# ---------------------------------------------------------------------------

@dataclass
class VerseInput:
    """A raw verse plus its variant metadata, before forking/calculation."""
    book: str
    chapter: int
    verse: int
    parsha: str
    text: str                                  # cantillated text (+markers)
    kri_text: Optional[str] = None             # full Kri reading (cantillated/plain)
    # Textual variant fork: replace `doublet_from` with `doublet_to` in consonants.
    doublet_from: Optional[str] = None
    doublet_to: Optional[str] = None
    variant_note: Optional[str] = None      # human-readable explanation
    variant_category: Optional[str] = None  # "Ittur Sopherim" | "Doublet" | etc.


@dataclass
class VerseFork:
    """One concrete, fully resolved reading of a verse (a DB row source)."""
    sub_id: str
    book: str
    chapter: int
    verse: int
    parsha: str
    variant_track: str          # 'Ksiv' | 'Kri' | 'TextVariant' | 'Aggregate'
    full_consonants: str
    first_half: str
    second_half: str
    paragraph_marker: Optional[str]
    words: List[str] = field(default_factory=list)
    cantillated_text: str = ""  # full cantillated verse (for HaNikud)


def _base_id(v: VerseInput) -> str:
    abbr = "".join(w[0] for w in v.book.split())[:4] or v.book[:4]
    return f"{abbr}_{v.chapter}_{v.verse}"


def fork_verse(v: VerseInput) -> List[VerseFork]:
    """Expand a verse into 1..n fully-calculated reading tracks.

    * Always emits a 'Ksiv' track from the physical written text.
    * Emits a 'Kri' track when kri_text is supplied (and differs).
    * Emits a 'TextVariant' track when a textual-variant substitution is supplied.
    """
    forks: List[VerseFork] = []
    marker = detect_paragraph_marker(v.text)
    bid = _base_id(v)

    # --- Track A: Ksiv (ground-truth written consonants) ---
    fh, sh = split_halves_by_atnach(v.text)
    forks.append(VerseFork(
        sub_id=f"{bid}_Ksiv", book=v.book, chapter=v.chapter, verse=v.verse,
        parsha=v.parsha, variant_track="Ksiv",
        full_consonants=strip_to_consonants(v.text),
        first_half=fh, second_half=sh, paragraph_marker=marker,
        words=tokenize_words(v.text),
        cantillated_text=v.text,
    ))

    # --- Track B: Kri (vocalised / read tradition) ---
    if v.kri_text:
        kfh, ksh = split_halves_by_atnach(v.kri_text)
        kri_full = strip_to_consonants(v.kri_text)
        if kri_full != forks[0].full_consonants:
            forks.append(VerseFork(
                sub_id=f"{bid}_Kri", book=v.book, chapter=v.chapter, verse=v.verse,
                parsha=v.parsha, variant_track="Kri",
                full_consonants=kri_full, first_half=kfh, second_half=ksh,
                paragraph_marker=marker, words=tokenize_words(v.kri_text),
                cantillated_text=v.kri_text,
            ))

    # --- Doublet: textual-variant alternative reading ---
    if v.doublet_from and v.doublet_to:
        base_cons = forks[0].full_consonants
        if v.doublet_from in base_cons:
            doub_cons = base_cons.replace(v.doublet_from, v.doublet_to, 1)
            # Re-derive halves from the substituted source text so that
            # first_half + second_half == doub_cons even when the substitution
            # word straddles the Asnachta boundary.
            doub_text = v.text.replace(v.doublet_from, v.doublet_to, 1)
            dfh, dsh = split_halves_by_atnach(doub_text)
            if dfh + dsh != doub_cons:
                # Fallback: substitution only exists in bare consonants, not in
                # the cantillated text; split doub_cons at the Ksiv first-half length.
                fh_len = len(forks[0].first_half)
                dfh = doub_cons[:fh_len]
                dsh = doub_cons[fh_len:]
            forks.append(VerseFork(
                sub_id=f"{bid}_Variant", book=v.book, chapter=v.chapter,
                verse=v.verse, parsha=v.parsha, variant_track="TextVariant",
                full_consonants=doub_cons, first_half=dfh, second_half=dsh,
                paragraph_marker=marker,
                words=[w.replace(v.doublet_from, v.doublet_to, 1)
                       if v.doublet_from in w else w for w in forks[0].words],
                cantillated_text=doub_text,
            ))
    return forks


# ---------------------------------------------------------------------------
# SECTION 4.  VERIFIED OFFLINE SAMPLE CORPUS  (+ optional Sefaria loader)
# ---------------------------------------------------------------------------
#
# Each verse below is a real, verified Masoretic verse carrying nikud + ta'amim
# (so half-verse splitting on the Asnachta is genuine). Paragraph markers shown in
# {פ}/{ס} braces are illustrative anchors for the parser; the full scribal layout
# is populated when you load the complete corpus from Sefaria.

SAMPLE_CORPUS: List[VerseInput] = [
    # --- Genesis / Bereshit (gematria of 1:1 famously totals 2701) ---
    VerseInput("Bereshit", 1, 1, "Bereshit",
               "בְּרֵאשִׁ֖ית בָּרָ֣א אֱלֹהִ֑ים אֵ֥ת הַשָּׁמַ֖יִם וְאֵ֥ת הָאָֽרֶץ׃"),
    VerseInput("Bereshit", 1, 2, "Bereshit",
               "וְהָאָ֗רֶץ הָיְתָ֥ה תֹ֙הוּ֙ וָבֹ֔הוּ וְחֹ֖שֶׁךְ עַל־פְּנֵ֣י תְה֑וֹם "
               "וְר֣וּחַ אֱלֹהִ֔ים מְרַחֶ֖פֶת עַל־פְּנֵ֥י הַמָּֽיִם׃"),
    VerseInput("Bereshit", 1, 3, "Bereshit",
               "וַיֹּ֥אמֶר אֱלֹהִ֖ים יְהִ֣י א֑וֹר וַֽיְהִי־אֽוֹר׃"),
    VerseInput("Bereshit", 1, 4, "Bereshit",
               "וַיַּ֧רְא אֱלֹהִ֛ים אֶת־הָא֖וֹר כִּי־ט֑וֹב וַיַּבְדֵּ֣ל אֱלֹהִ֔ים "
               "בֵּ֥ין הָא֖וֹר וּבֵ֥ין הַחֹֽשֶׁךְ׃"),
    VerseInput("Bereshit", 1, 5, "Bereshit",
               "וַיִּקְרָ֨א אֱלֹהִ֤ים ׀ לָאוֹר֙ י֔וֹם וְלַחֹ֖שֶׁךְ קָ֣רָא לָ֑יְלָה "
               "וַֽיְהִי־עֶ֥רֶב וַֽיְהִי־בֹ֖קֶר י֥וֹם אֶחָֽד׃ {פ}"),

    # --- Deuteronomy / Va'etchanan: the Shema and following verses ---
    VerseInput("Devarim", 6, 4, "Va'etchanan",
               "שְׁמַ֖ע יִשְׂרָאֵ֑ל יְהוָ֥ה אֱלֹהֵ֖ינוּ יְהוָ֥ה ׀ אֶחָֽד׃"),
    VerseInput("Devarim", 6, 5, "Va'etchanan",
               "וְאָ֣הַבְתָּ֔ אֵ֖ת יְהוָ֣ה אֱלֹהֶ֑יךָ בְּכָל־לְבָֽבְךָ֥ "
               "וּבְכָל־נַפְשְׁךָ֖ וּבְכָל־מְאֹדֶֽךָ׃"),
    VerseInput("Devarim", 6, 6, "Va'etchanan",
               "וְהָי֞וּ הַדְּבָרִ֣ים הָאֵ֗לֶּה אֲשֶׁ֨ר אָנֹכִ֧י מְצַוְּךָ֛ "
               "הַיּ֖וֹם עַל־לְבָבֶֽךָ׃"),

    # --- Leviticus / Vayikra opening ---
    VerseInput("Vayikra", 1, 1, "Vayikra",
               "וַיִּקְרָ֖א אֶל־מֹשֶׁ֑ה וַיְדַבֵּ֤ר יְהוָה֙ אֵלָ֔יו "
               "מֵאֹ֥הֶל מוֹעֵ֖ד לֵאמֹֽר׃ {ס}"),

    # --- Esther 8:11  (DOUBLET: להשמיד  vs  ולהשמיד) ---
    # Consonant-level doublet substitution demonstrates the fork engine. The
    # full Masoretic verse populates via Sefaria; here the verified doublet word
    # is carried inside a faithful fragment of the verse.
    VerseInput("Esther", 8, 11, "Esther",
               "אֲשֶׁר֩ נָתַ֨ן הַמֶּ֜לֶךְ לַיְּהוּדִ֣ים ׀ "
               "לְהִקָּהֵל֮ וְלַעֲמֹ֣ד עַל־נַפְשָׁם֒ לְהַשְׁמִיד֩ "
               "וְלַהֲרֹ֨ג וּלְאַבֵּ֜ד אֶת־כׇּל־חֵ֣יל עַ֤ם וּמְדִינָה֙ הַצָּרִ֣ים אֹתָ֔ם׃",
               doublet_from="להשמיד", doublet_to="ולהשמיד"),

    # --- Esther 9:27  (DOUBLET: וקבל  vs  וקבלו) ---
    VerseInput("Esther", 9, 27, "Esther",
               "קִיְּמ֣וּ וְקִבְּל֣וּ הַיְּהוּדִים֩ ׀ עֲלֵיהֶ֨ם ׀ וְעַל־זַרְעָ֜ם "
               "וְעַ֨ל כָּל־הַנִּלְוִ֤ים עֲלֵיהֶם֙ וְלֹ֣א יַעֲב֔וֹר׃",
               doublet_from="וקבל", doublet_to="וקבלו"),

    # --- Illustrative Ksiv/Kri fork (documented perpetual variant: לא / לו) ---
    # The written (Ksiv) consonants are לא; the read (Kri) tradition is לו. This
    # is a real, well-documented class of Masoretic ksiv/kri; the fragment below
    # is illustrative scaffolding for the fork engine, not a quoted full verse.
    VerseInput("Tehillim", 100, 3, "—",
               "דְּע֗וּ כִּֽי־יְהוָה֮ ה֤וּא אֱלֹ֫הִ֥ים ה֣וּא עָ֭שָׂנוּ וְלֹ֣א אֲנַ֑חְנוּ",
               kri_text="דְּע֗וּ כִּֽי־יְהוָה֮ ה֤וּא אֱלֹ֫הִ֥ים ה֣וּא עָ֭שָׂנוּ וְל֣וֹ אֲנַ֑חְנוּ"),
]


# ---------------------------------------------------------------------------
# SECTION 4b.  MASORETIC TEXTUAL VARIANT REGISTRY
# ---------------------------------------------------------------------------
#
# Canonical list of documented Masoretic textual variants whose fork can be
# computed automatically. Keys are (book, chapter, verse) as they appear in
# tanach_corpus.jsonl (English names). Each spec provides consonant-level
# doublet_from / doublet_to for the fork engine.
#
# Sources verified: BT Nedarim 37b (Itture Sopherim), Mekhilta / Yalkut
# Shimoni / Sifre (Tiqqune Sopherim), Masoretic tradition (Esther doublets).

TEXTUAL_VARIANT_SPECS: dict = {
    # --- Itture Sopherim (BT Nedarim 37b) —
    #     Five places where scribes omitted a conjunctive vav.
    #     TextVariant restores the vav (+6 Standard).
    ("Genesis", 18, 5): {
        "from": "אחר", "to": "ואחר",
        "category": "Ittur Sopherim",
        "note": "Received text: אַחַר. Traditional reading adds a vav: וְאַחַר. (BT Nedarim 37b)",
    },
    ("Genesis", 24, 55): {
        "from": "אחר", "to": "ואחר",
        "category": "Ittur Sopherim",
        "note": "Received text: אַחַר. Traditional reading adds a vav: וְאַחַר. (BT Nedarim 37b)",
    },
    ("Numbers", 31, 2): {
        "from": "אחר", "to": "ואחר",
        "category": "Ittur Sopherim",
        "note": "Received text: אַחַר. Traditional reading adds a vav: וְאַחַר. (BT Nedarim 37b)",
    },
    ("Psalms", 36, 7): {
        "from": "משפטך", "to": "ומשפטך",
        "category": "Ittur Sopherim",
        "note": "Received text: מִשְׁפָּטֶךָ. Traditional reading: וּמִשְׁפָּטֶךָ. (BT Nedarim 37b)",
    },
    ("Psalms", 68, 26): {
        "from": "אחר", "to": "ואחר",
        "category": "Ittur Sopherim",
        "note": "Received text: אַחַר. Traditional reading adds a vav: וְאַחַר. (BT Nedarim 37b)",
    },
    # --- Esther doublets (Masoretic plene/defective variants) ---
    ("Esther", 8, 11): {
        "from": "להשמיד", "to": "ולהשמיד",
        "category": "Doublet",
        "note": "Some manuscripts read וְלְהַשְׁמִיד (with vav); received Masoretic text lacks it.",
    },
    ("Esther", 9, 27): {
        "from": "וקבל", "to": "וקבלו",
        "category": "Doublet",
        "note": "Kethiv: וְקִבֵּל. Qere: וְקִבְּלוּ. Both attested in Masoretic manuscripts.",
    },
}

# Documentation-only tables surfaced in the Guide tab (not engine-active).
TIQQUNE_SOPHERIM = [
    {"Ref": "Genesis 18:22",    "Received text": "אַבְרָהָם עוֹדֶנּוּ עֹמֵד לִפְנֵי ה׳", "Traditional original": "ה׳ עוֹדֶנּוּ עֹמֵד לִפְנֵי אַבְרָהָם", "Note": "Scribes reversed subject/object to avoid saying God 'stood before' Abraham.", "Source": "Mekhilta; Sifre Num. §84; Tanḥuma"},
    {"Ref": "Numbers 11:15",    "Received text": "בְּרָעָתִי", "Traditional original": "בְּרָעָתֶךָ", "Note": "Moses's rebuke softened from 'Your evil' to 'my evil'.", "Source": "ibid."},
    {"Ref": "Numbers 12:12",    "Received text": "אִמֵּנוּ / בְּשָׂרֵנוּ", "Traditional original": "אִמּוֹ / בְּשָׂרוֹ", "Note": "Plural changed to third-person singular to reduce directness.", "Source": "ibid."},
    {"Ref": "I Samuel 3:13",    "Received text": "מְקַלְלִים לָהֶם", "Traditional original": "מְקַלְלִים לִי / לֵאלֹהִים", "Note": "Offense against God softened by pronoun change.", "Source": "ibid."},
    {"Ref": "II Samuel 16:12",  "Received text": "בְּעֹנִי / בְּעֵינִי", "Traditional original": "בְּעֵינֵי ה׳", "Note": "Divine reference removed.", "Source": "ibid."},
    {"Ref": "II Samuel 20:1",   "Received text": "לְאֹהָלָיו", "Traditional original": "לֵאלֹהָיו", "Note": "Scribes changed 'his gods' to 'his tents' (idolatry connotation avoided).", "Source": "ibid."},
    {"Ref": "I Kings 12:16",    "Received text": "לְאֹהָלָיו", "Traditional original": "לֵאלֹהָיו", "Note": "Same as II Sam 20:1.", "Source": "ibid."},
    {"Ref": "Jeremiah 2:11",    "Received text": "כְּבוֹדָם", "Traditional original": "כְּבוֹדִי", "Note": "Israel's 'glory' substituted for God's 'glory'.", "Source": "ibid."},
    {"Ref": "Ezekiel 8:17",     "Received text": "אַפָּם", "Traditional original": "אַפִּי", "Note": "Pronoun changed from divine first-person to human third-person.", "Source": "ibid."},
    {"Ref": "Hosea 4:7",        "Received text": "כְּבוֹדָם", "Traditional original": "כְּבוֹדִי", "Note": "Their glory substituted for My glory (reverence correction).", "Source": "ibid."},
    {"Ref": "Habakkuk 1:12",    "Received text": "לֹא נָמוּת", "Traditional original": "לֹא תָמוּת", "Note": "1st-person plural substituted for 2nd-person to avoid asserting God's mortality.", "Source": "ibid."},
    {"Ref": "Zechariah 2:12",   "Received text": "עֵינוֹ", "Traditional original": "עֵינִי", "Note": "His eye / My eye pronoun shift.", "Source": "ibid."},
    {"Ref": "Malachi 1:13",     "Received text": "אוֹתוֹ", "Traditional original": "אוֹתִי", "Note": "Object pronoun shifted away from first-person divine.", "Source": "ibid."},
    {"Ref": "Psalms 106:20",    "Received text": "כְּבוֹדָם", "Traditional original": "כְּבוֹדִי", "Note": "Same correction as Hosea 4:7.", "Source": "ibid."},
    {"Ref": "Job 7:20",         "Received text": "עָלֶיךָ / עָלַי", "Traditional original": "(variant; list differs by source)", "Note": "Pronoun shift to reduce theological offence.", "Source": "ibid."},
    {"Ref": "Job 32:3",         "Received text": "אֶת אִיּוֹב", "Traditional original": "אֶת ה׳", "Note": "God replaced by Job as object of condemnation.", "Source": "ibid."},
    {"Ref": "Lamentations 3:20", "Received text": "תָּשִׁיחַ עָלַי", "Traditional original": "(variant)", "Note": "Divine subject softened.", "Source": "ibid."},
    {"Ref": "Numbers 11:15 / Job 7:20 (lists vary)", "Received text": "—", "Traditional original": "—", "Note": "The 18th entry varies across sources (Mekhilta, Sifre, Yalkut Shimoni, Masorah Magna do not agree on a single unified list).", "Source": "Midrash Rabbah; Yalkut Shimoni"},
]

DOUBLET_PASSAGES = [
    {"Passage A": "Psalms 14",       "Passage B": "Psalms 53",            "Note": "Nearly word-for-word; divine name differs (YHWH vs Elohim). One of the most striking intra-biblical doublets.",               "Source": "BHS apparatus; Goshen-Gottstein"},
    {"Passage A": "Isaiah 36–39",    "Passage B": "II Kings 18:13–20:19", "Note": "Parallel narrative; Kings contains one additional verse (II Kgs 18:14–16) absent from Isaiah.",                                 "Source": "BHS apparatus"},
    {"Passage A": "II Samuel 22",    "Passage B": "Psalms 18",            "Note": "The 'Song of David' appears in both books with minor textual differences — a rare intra-canonical variant passage.",              "Source": "BHS apparatus; Talmon (1960)"},
]


def apply_textual_variants(verses: List[VerseInput]) -> List[VerseInput]:
    """Inject Masoretic textual-variant fork data from TEXTUAL_VARIANT_SPECS.

    Safe to call on both the JSONL and SAMPLE_CORPUS paths; skips any verse
    that already has doublet_from set (manual SAMPLE_CORPUS entries win).
    """
    for v in verses:
        spec = TEXTUAL_VARIANT_SPECS.get((v.book, v.chapter, v.verse))
        if spec and not v.doublet_from:
            v.doublet_from = spec["from"]
            v.doublet_to = spec["to"]
            v.variant_note = spec.get("note", "")
            v.variant_category = spec.get("category", "")
    return verses


def load_from_sefaria(refs: List[str], timeout: int = 20) -> List[VerseInput]:
    """OPTIONAL: ingest real Masoretic verses (with ta'amim) from the Sefaria API.

    This is intentionally network-gated and best-effort: it returns whatever it
    can fetch and is skipped automatically when offline. The parsing / cipher /
    forking pipeline is identical to the bundled-corpus path.

    `refs` are Sefaria references, e.g. ["Genesis 1", "Psalms 23"].
    """
    import json
    import urllib.parse
    import urllib.request

    out: List[VerseInput] = []
    base = "https://www.sefaria.org/api/v3/texts/"
    # The "Tanach with Ta'amei Hamikra" version carries cantillation marks.
    # The version title contains spaces / a pipe / an apostrophe, so it MUST be
    # percent-encoded (quote_via=quote -> %20 for spaces) or urlopen will raise
    # InvalidURL before any request is sent.
    query = urllib.parse.urlencode(
        {"version": "hebrew|Tanach with Ta'amei Hamikra"},
        quote_via=urllib.parse.quote,
    )
    for ref in refs:
        try:
            url = f"{base}{urllib.parse.quote(ref)}?{query}"
            req = urllib.request.Request(
                url, headers={"User-Agent": "tanach-gematria/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            book = data.get("book", ref.split()[0])
            sections = data.get("sections", [1])
            try:
                start_chap = int(sections[0]) if sections else 1
            except (ValueError, TypeError):
                continue
            versions = data.get("versions", [])
            if not versions:
                continue
            text = versions[0].get("text", [])
            # Sefaria v3: single-chapter ref -> flat list of verse strings;
            # multi-chapter ref -> list of chapters, each a list of verse strings.
            if text and isinstance(text[0], list):
                chapters = text
            else:
                chapters = [text]
            for ci, chap_verses in enumerate(chapters):
                chap_num = start_chap + ci
                for vi, vtext in enumerate(chap_verses, start=1):
                    if isinstance(vtext, list):
                        vtext = " ".join(vtext)
                    if not vtext:
                        continue
                    out.append(VerseInput(book, chap_num, vi, book, vtext))
        except Exception:
            # Network unreachable / ref not found / schema drift: skip this ref.
            continue
    return out


CORPUS_FILE   = pathlib.Path(__file__).parent / "tanach_corpus.jsonl"
PREBUILT_DB   = pathlib.Path(__file__).parent / "tanach.db"


def load_from_jsonl(path: pathlib.Path = CORPUS_FILE) -> List[VerseInput]:
    """Load the pre-fetched full Tanach corpus from a local JSONL file.

    Each line: {"book": str, "chapter": int, "verse": int, "text": str}
    Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []
    verses = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            verses.append(VerseInput(
                book=row["book"],
                chapter=int(row["chapter"]),
                verse=int(row["verse"]),
                parsha=row["book"],
                text=row["text"],
            ))
    return verses


def build_sefaria_url(ref: str) -> str:
    """Return the exact (well-formed, percent-encoded) URL the loader requests.

    Exposed so the request can be inspected/tested without a live network.
    """
    import urllib.parse
    base = "https://www.sefaria.org/api/v3/texts/"
    query = urllib.parse.urlencode(
        {"version": "hebrew|Tanach with Ta'amei Hamikra"},
        quote_via=urllib.parse.quote,
    )
    return f"{base}{urllib.parse.quote(ref)}?{query}"


# ---------------------------------------------------------------------------
# SECTION 5.  SQLITE DATABASE BUILD (tokens, structures, variants, patterns)
# ---------------------------------------------------------------------------

CIPHER_COLS = ", ".join(f"{c} INTEGER" for c in CIPHER_NAMES)
CIPHER_PLACEHOLDERS = ", ".join(["?"] * len(CIPHER_NAMES))
CIPHER_INSERT_COLS = ", ".join(CIPHER_NAMES)


def _cipher_tuple(consonants: str, cantillated: str = "") -> Tuple[int, ...]:
    vals = compute_all_ciphers(consonants, cantillated)
    return tuple(vals[c] for c in CIPHER_NAMES)


def build_database(verses: List[VerseInput]) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with every structural unit indexed by every
    cipher, then run the pattern-recognition pass."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    cur = conn.cursor()

    cur.execute(f"""
        CREATE TABLE units (
            unit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            sub_id         TEXT,
            book           TEXT,
            chapter        INTEGER,
            verse          INTEGER,
            parsha         TEXT,
            boundary_type  TEXT,
            variant_track  TEXT,
            consonants     TEXT,
            text_display   TEXT,
            {CIPHER_COLS}
        )
    """)

    def insert(sub_id, book, chapter, verse, parsha, boundary, track, cons,
               disp=None, cantillated=""):
        if not cons:
            return
        cur.execute(
            f"""INSERT INTO units
                (sub_id, book, chapter, verse, parsha, boundary_type,
                 variant_track, consonants, text_display, {CIPHER_INSERT_COLS})
                VALUES (?,?,?,?,?,?,?,?,?,{CIPHER_PLACEHOLDERS})""",
            (sub_id, book, chapter, verse, parsha, boundary, track, cons,
             disp or cons, *_cipher_tuple(cons, cantillated)),
        )

    # ---- Micro structures: words, half-verses, full verses (per fork) ----
    # Dedupe overlapping refs so a verse isn't double-counted in aggregation.
    seen: set = set()
    unique_verses: List[VerseInput] = []
    for v in verses:
        key = (v.book, v.chapter, v.verse)
        if key not in seen:
            seen.add(key)
            unique_verses.append(v)

    all_forks: List[VerseFork] = []
    for v in unique_verses:
        all_forks.extend(fork_verse(v))

    for f in all_forks:
        # Pass cantillated_text for Verse rows so HaNikud gets the vowel count.
        # Sub-unit rows (halves, words) only have consonants → HaNikud = 0 there.
        insert(f.sub_id, f.book, f.chapter, f.verse, f.parsha,
               "Verse", f.variant_track, f.full_consonants,
               cantillated=f.cantillated_text)
        insert(f"{f.sub_id}_FH", f.book, f.chapter, f.verse, f.parsha,
               "FirstHalf", f.variant_track, f.first_half)
        insert(f"{f.sub_id}_SH", f.book, f.chapter, f.verse, f.parsha,
               "SecondHalf", f.variant_track, f.second_half)
        for wi, w in enumerate(f.words, start=1):
            insert(f"{f.sub_id}_W{wi}", f.book, f.chapter, f.verse, f.parsha,
                   "Word", f.variant_track, w)
        if f.paragraph_marker:
            insert(f"{f.sub_id}_{f.paragraph_marker}", f.book, f.chapter,
                   f.verse, f.parsha, f.paragraph_marker, f.variant_track,
                   f.full_consonants, cantillated=f.cantillated_text)

    # ---- Macro structures: Perek, Parsha (Ksiv track aggregation) ----
    ksiv = [f for f in all_forks if f.variant_track == "Ksiv"]

    def aggregate(group_key_fn, boundary_name, id_fn):
        buckets: Dict[Tuple, List[VerseFork]] = {}
        for f in ksiv:
            buckets.setdefault(group_key_fn(f), []).append(f)
        for key, members in buckets.items():
            members.sort(key=lambda m: (m.chapter, m.verse))
            cons = "".join(m.full_consonants for m in members)
            sample = members[0]
            insert(id_fn(key, sample), sample.book,
                   sample.chapter if boundary_name == "Perek" else 0,
                   0, sample.parsha, boundary_name, "Aggregate", cons)

    aggregate(lambda f: (f.book, f.chapter), "Perek",
              lambda k, s: f"PEREK_{k[0]}_{k[1]}")
    aggregate(lambda f: (f.parsha,), "Parsha",
              lambda k, s: f"PARSHA_{k[0]}")

    # ---- Paragraph blocks: accumulate verses until a marker closes a block ----
    block: List[VerseFork] = []
    block_n = 0
    for f in sorted(ksiv, key=lambda m: (m.book, m.chapter, m.verse)):
        block.append(f)
        if f.paragraph_marker:
            block_n += 1
            cons = "".join(m.full_consonants for m in block)
            insert(f"BLOCK_{f.paragraph_marker}_{block_n}", block[0].book,
                   block[0].chapter, block[0].verse, block[0].parsha,
                   f.paragraph_marker, "Aggregate", cons)
            block = []

    conn.commit()

    # ---- Indices on every cipher column + boundary/variant/structure keys ----
    cur.execute("CREATE INDEX idx_boundary ON units(boundary_type)")
    cur.execute("CREATE INDEX idx_variant ON units(variant_track)")
    cur.execute("CREATE INDEX idx_bcv ON units(book, chapter, verse)")
    cur.execute("CREATE INDEX idx_parsha ON units(parsha)")
    for c in CIPHER_NAMES:
        cur.execute(f"CREATE INDEX idx_{c} ON units({c})")
    conn.commit()

    build_pattern_log(conn)
    return conn


# ---------------------------------------------------------------------------
# SECTION 6.  PATTERN RECOGNITION & ECHO-MATCHING
# ---------------------------------------------------------------------------

def build_pattern_log(conn: sqlite3.Connection) -> None:
    """Scan the DB and populate a `patterns` table of noteworthy anomalies."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE patterns (
            pattern_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT,
            cipher       TEXT,
            value_a      INTEGER,
            value_b      INTEGER,
            ref_a        TEXT,
            ref_b        TEXT,
            detail       TEXT
        )
    """)

    df = pd.read_sql_query(
        "SELECT sub_id, book, chapter, verse, parsha, boundary_type, "
        "variant_track, " + ", ".join(CIPHER_NAMES) + " FROM units", conn)

    def log(ptype, cipher, va, vb, ra, rb, detail):
        cur.execute(
            "INSERT INTO patterns(pattern_type,cipher,value_a,value_b,ref_a,ref_b,detail)"
            " VALUES (?,?,?,?,?,?,?)",
            (ptype, cipher, int(va), int(vb), ra, rb, detail))

    # --- 6a. Internal verse balance: FirstHalf == SecondHalf (+/- Colel 1) ---
    fh = df[df.boundary_type == "FirstHalf"]
    sh = df[df.boundary_type == "SecondHalf"]
    fh_idx = {(r.book, r.chapter, r.verse, r.variant_track): r for _, r in fh.iterrows()}
    sh_idx = {(r.book, r.chapter, r.verse, r.variant_track): r for _, r in sh.iterrows()}
    for key in fh_idx:
        if key not in sh_idx:
            continue
        a, b = fh_idx[key], sh_idx[key]
        for c in CIPHER_NAMES:
            if a[c] == 0 or b[c] == 0:
                continue
            diff = abs(int(a[c]) - int(b[c]))
            if diff <= 1:
                kind = "exact" if diff == 0 else "colel±1"
                log("InternalBalance", c, a[c], b[c],
                    f"{a.book} {a.chapter}:{a.verse} 1st-half [{key[3]}]",
                    f"{a.book} {a.chapter}:{a.verse} 2nd-half [{key[3]}]",
                    f"halves balanced ({kind})")

    # --- 6b. Proximity echoes: adjacent full verses share a value ---
    vfull = df[(df.boundary_type == "Verse") & (df.variant_track == "Ksiv")]
    vfull = vfull.sort_values(["book", "chapter", "verse"]).reset_index(drop=True)
    for i in range(len(vfull) - 1):
        r0, r1 = vfull.iloc[i], vfull.iloc[i + 1]
        if r0.book != r1.book:
            continue
        adjacent = (r0.chapter == r1.chapter and r1.verse == r0.verse + 1)
        if not adjacent:
            continue
        for c in CIPHER_NAMES:
            if r0[c] and r0[c] == r1[c]:
                log("ProximityEcho", c, r0[c], r1[c],
                    f"{r0.book} {r0.chapter}:{r0.verse}",
                    f"{r1.book} {r1.chapter}:{r1.verse}",
                    "adjacent verses share value")

    conn.commit()


# ---------------------------------------------------------------------------
# SECTION 6b.  MODULE-LEVEL PATTERN MATCH HELPERS
# ---------------------------------------------------------------------------

def parse_pattern_ref(ref_str: str):
    """Parse a pattern ref string → (book, chapter, verse, boundary) or None.

    Handles:
      'Book ch:v 1st-half [Track]'  → FirstHalf
      'Book ch:v 2nd-half [Track]'  → SecondHalf
      'Book ch:v'                   → Verse
    """
    m = re.match(r'^(.+?)\s+(\d+):(\d+)\s+(1st|2nd)-half', ref_str)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3)), (
            "FirstHalf" if m.group(4) == "1st" else "SecondHalf")
    m = re.match(r'^(.+?)\s+(\d+):(\d+)\s*', ref_str)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3)), "Verse"
    return None


def internal_balance_matches(
    conn: sqlite3.Connection,
    methods_a: List[str],
    methods_b: List[str],
    colel: bool = False,
    min_value: int = 0,
    limit: int = 500,
) -> pd.DataFrame:
    """Return verses where first-half[ma] ≈ second-half[mb] for each (ma, mb) pair."""
    tol = 1 if colel else 0
    parts = []
    for ma in methods_a:
        for mb in methods_b:
            df = pd.read_sql_query(
                f"SELECT 'Internal Balance' AS Pattern, "
                f"u1.{ma} AS 'Value A', u2.{mb} AS 'Value B', "
                f"u1.sub_id AS 'Reference A', u2.sub_id AS 'Reference B', "
                f"u1.book AS Book, u1.chapter AS Chapter, u1.verse AS Verse "
                "FROM units u1 JOIN units u2 "
                "ON u1.book=u2.book AND u1.chapter=u2.chapter AND u1.verse=u2.verse "
                "WHERE u1.boundary_type='FirstHalf' AND u2.boundary_type='SecondHalf' "
                "AND u1.variant_track='Ksiv' AND u2.variant_track='Ksiv' "
                f"AND u1.{ma} > ? AND u2.{mb} > ? "
                f"AND ABS(u1.{ma} - u2.{mb}) <= ? "
                f"ORDER BY u1.book, u1.chapter, u1.verse LIMIT ?",
                conn, params=[min_value, min_value, tol, limit],
            )
            if not df.empty:
                df.insert(1, "Method B", mb)
                df.insert(1, "Method A", ma)
                parts.append(df)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def proximity_echo_matches(
    conn: sqlite3.Connection,
    methods: List[str],
    colel: bool = False,
    min_value: int = 0,
    limit: int = 500,
) -> pd.DataFrame:
    """Return consecutive verse pairs sharing a value under each method."""
    tol = 1 if colel else 0
    parts = []
    for m in methods:
        df = pd.read_sql_query(
            f"SELECT 'Proximity Echo' AS Pattern, "
            f"u1.{m} AS 'Value A', u2.{m} AS 'Value B', "
            f"u1.sub_id AS 'Reference A', u2.sub_id AS 'Reference B', "
            f"u1.book AS Book, u1.chapter AS Chapter, u1.verse AS Verse "
            "FROM units u1 JOIN units u2 "
            "ON u1.book=u2.book AND u1.chapter=u2.chapter AND u2.verse=u1.verse+1 "
            "WHERE u1.boundary_type='Verse' AND u2.boundary_type='Verse' "
            "AND u1.variant_track='Ksiv' AND u2.variant_track='Ksiv' "
            f"AND u1.{m} > ? AND ABS(u1.{m} - u2.{m}) <= ? LIMIT ?",
            conn, params=[min_value, tol, limit],
        )
        if not df.empty:
            df.insert(1, "Method B", m)
            df.insert(1, "Method A", m)
            parts.append(df)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def whole_unit_echo_matches(
    conn: sqlite3.Connection,
    methods_a: List[str],
    methods_b: List[str],
    boundary: str = "Verse",
    min_value: int = 0,
    limit: int = 300,
) -> pd.DataFrame:
    """Return unit pairs anywhere in Tanach sharing a value cross-method (ma ≠ mb).

    Each (ma, mb) pair where ma != mb is queried separately.  The two directions
    (u1=A, u2=B) and (u1=B, u2=A) are both included when both equalities hold.
    """
    parts = []
    for ma in methods_a:
        for mb in methods_b:
            if ma == mb:
                continue
            df = pd.read_sql_query(
                f"SELECT 'Cross-Method Echo' AS Pattern, "
                f"u1.{ma} AS 'Value A', u2.{mb} AS 'Value B', "
                f"u1.sub_id AS 'Reference A', u2.sub_id AS 'Reference B', "
                f"u1.book AS Book, u1.chapter AS Chapter, u1.verse AS Verse "
                f"FROM units u1 JOIN units u2 ON u1.{ma} = u2.{mb} "
                "WHERE u1.boundary_type=? AND u2.boundary_type=? "
                "AND u1.variant_track='Ksiv' AND u2.variant_track='Ksiv' "
                f"AND u1.{ma} > ? AND u2.{mb} > ? "
                "AND u1.rowid != u2.rowid "
                "LIMIT ?",
                conn, params=[boundary, boundary, min_value, min_value, limit],
            )
            if not df.empty:
                df.insert(1, "Method B", mb)
                df.insert(1, "Method A", ma)
                parts.append(df)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


# ---------------------------------------------------------------------------
# SECTION 7.  SEARCH ENGINE (with the Rule of the Colel)
# ---------------------------------------------------------------------------

def search_value(conn: sqlite3.Connection, cipher: str, value: int,
                 colel: bool = False, tracks: Optional[List[str]] = None,
                 boundaries: Optional[List[str]] = None,
                 limit: int = 500) -> pd.DataFrame:
    """Find every structural unit whose `cipher` value matches `value`.

    With `colel=True` the search also returns value-1 and value+1 matches.
    """
    if cipher not in CIPHER_NAMES:
        raise ValueError(f"Unknown cipher {cipher!r}")
    where = []
    params: List = []
    if colel:
        where.append(f"{cipher} BETWEEN ? AND ?")
        params += [value - 1, value + 1]
    else:
        where.append(f"{cipher} = ?")
        params.append(value)
    if tracks:
        where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
        params += tracks
    if boundaries:
        where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
        params += boundaries
    sql = (f"SELECT book AS Book, chapter AS Chapter, verse AS Verse, "
           f"boundary_type AS Boundary, variant_track AS Track, parsha AS Parsha, "
           f"consonants AS Text, {cipher} AS Value, sub_id AS SubID "
           f"FROM units WHERE " + " AND ".join(where) +
           f" ORDER BY ABS({cipher} - ?), Book, Chapter, Verse LIMIT ?")
    params += [value, limit]
    return pd.read_sql_query(sql, conn, params=params)


def count_value(conn: sqlite3.Connection, cipher: str, value: int,
                colel: bool = False,
                tracks: Optional[List[str]] = None,
                boundaries: Optional[List[str]] = None) -> int:
    """Exact match count (no LIMIT) — used for coincidence-rate denominators."""
    where, params = [], []
    if colel:
        where.append(f"{cipher} BETWEEN ? AND ?")
        params += [value - 1, value + 1]
    else:
        where.append(f"{cipher} = ?")
        params.append(value)
    if tracks:
        where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
        params += tracks
    if boundaries:
        where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
        params += boundaries
    sql = "SELECT COUNT(*) FROM units WHERE " + " AND ".join(where)
    return int(pd.read_sql_query(sql, conn, params=params).iloc[0, 0])


def boundary_population(conn: sqlite3.Connection,
                        tracks: Optional[List[str]] = None,
                        boundaries: Optional[List[str]] = None) -> int:
    """Total units matching the given track/boundary filters — the denominator."""
    where, params = [], []
    if tracks:
        where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
        params += tracks
    if boundaries:
        where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
        params += boundaries
    sql = "SELECT COUNT(*) FROM units" + (" WHERE " + " AND ".join(where) if where else "")
    return int(pd.read_sql_query(sql, conn, params=params).iloc[0, 0])


def search_phrase(conn: sqlite3.Connection, phrase_consonants: str,
                  colel: bool = False, tracks: Optional[List[str]] = None,
                  boundaries: Optional[List[str]] = None) -> Dict[str, object]:
    """Compute every cipher value for the input phrase and search each one."""
    values = compute_all_ciphers(phrase_consonants)
    results = {c: search_value(conn, c, values[c], colel, tracks, boundaries)
               for c in CIPHER_NAMES}
    return {"values": values, "results": results}


def search_value_all_methods(
    conn: sqlite3.Connection, value: int, limit_per_method: int = 50
) -> pd.DataFrame:
    """Search `value` across all 12 ciphers in a single UNION ALL query.

    Returns a DataFrame with a leading 'Method' column so the caller can see
    which cipher produced each match.
    """
    unions, params = [], []
    for c in CIPHER_NAMES:
        # Each branch must be wrapped in a subquery for LIMIT to be valid inside UNION ALL
        unions.append(
            f"SELECT * FROM ("
            f"SELECT '{c}' AS Method, book AS Book, chapter AS Chapter, "
            f"verse AS Verse, boundary_type AS Boundary, variant_track AS Track, "
            f"consonants AS Text, {c} AS Value, sub_id AS SubID "
            f"FROM units WHERE {c}=? AND variant_track='Ksiv' LIMIT {limit_per_method})"
        )
        params.append(value)
    sql = "SELECT * FROM (" + " UNION ALL ".join(unions) + ") ORDER BY Method, Book, Chapter, Verse"
    return pd.read_sql_query(sql, conn, params=params)


def normalize_query(raw: str) -> str:
    """Clean a Hebrew query string down to its 22-letter consonant skeleton."""
    return strip_to_consonants(raw)


def _xm_count_matrix(
    conn: sqlite3.Connection,
    a_vals: Dict[str, int],
    colel: bool,
    tracks: Optional[List[str]],
    boundaries: Optional[List[str]],
) -> pd.DataFrame:
    """Build the 12×12 cross-method count matrix in a single SQL pass.

    Replaces 144 individual COUNT queries with one query containing 144
    CASE WHEN expressions, scanning the units table once.
    """
    where, params = [], []
    if tracks:
        where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
        params += tracks
    if boundaries:
        where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
        params += boundaries
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    cases = []
    for ma in CIPHER_NAMES:
        v = int(a_vals[ma])
        for mb in CIPHER_NAMES:
            cond = (f"{mb} BETWEEN {v - 1} AND {v + 1}" if colel
                    else f"{mb} = {v}")
            cases.append(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)")
    sql = f"SELECT {', '.join(cases)} FROM units {where_clause}"
    row = pd.read_sql_query(sql, conn, params=params).iloc[0]
    n = len(CIPHER_NAMES)
    matrix_rows = {}
    for i, ma in enumerate(CIPHER_NAMES):
        matrix_rows[f"{ma} ({a_vals[ma]})"] = [
            int(row.iloc[i * n + j]) for j in range(n)
        ]
    return pd.DataFrame.from_dict(matrix_rows, orient="index", columns=CIPHER_NAMES)


# ---------------------------------------------------------------------------
# SECTION 8.  STATISTICS & VISUALIZATION HELPERS
# ---------------------------------------------------------------------------

def structure_frame(conn: sqlite3.Connection, boundary: str,
                    track: str = "Ksiv") -> pd.DataFrame:
    trk = "Aggregate" if boundary in ("Perek", "Parsha") else track
    return pd.read_sql_query(
        "SELECT * FROM units WHERE boundary_type=? AND variant_track=?",
        conn, params=[boundary, trk])


def extremes_table(conn: sqlite3.Connection,
                   boundaries: List[str]) -> pd.DataFrame:
    """Max/Min/Mean/Median/Std of the Standard value per macro-structure type."""
    rows = []
    for b in boundaries:
        df = structure_frame(conn, b)
        if df.empty:
            continue
        col = df["Standard"]
        rows.append({
            "Structure": b, "Count": int(col.count()),
            "Max": int(col.max()), "Min": int(col.min()),
            "Mean": round(float(col.mean()), 1),
            "Median": float(col.median()),
            "StdDev": round(float(sd), 1) if pd.notna(sd := col.std()) else 0.0,
        })
    return pd.DataFrame(rows)


def density_gaps(conn: sqlite3.Connection, cipher: str = "Standard",
                 boundary: str = "Verse") -> Dict[str, object]:
    """Identify 'dead zones': value ranges with zero verse representation."""
    df = structure_frame(conn, boundary)
    if df.empty:
        return {"min": 0, "max": 0, "present": set(), "gaps": []}
    vals = sorted(set(int(v) for v in df[cipher] if v))
    present = set(vals)
    gaps = []
    for i in range(len(vals) - 1):
        if vals[i + 1] - vals[i] > 1:
            gaps.append((vals[i] + 1, vals[i + 1] - 1))
    return {"min": vals[0] if vals else 0, "max": vals[-1] if vals else 0,
            "present": present, "gaps": gaps}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# SECTION 8b.  CIPHER BREAKDOWN HELPER (for in-UI letter-by-letter display)
# ---------------------------------------------------------------------------

def cipher_breakdown(cipher: str, consonants: str) -> Optional[List[Tuple[str, int]]]:
    """Return [(display_label, letter_value)] for equation display in the UI.

    Each element is one letter (or swap arrow for temurah ciphers) plus its
    value contribution. Returns None for HaNikud (not letter-based) or empty
    input — callers should show a note instead.
    """
    if cipher in ("HaNikud", "KatanMispari", "KololEhad", "KololOtiyot") or not consonants:
        return None
    result: List[Tuple[str, int]] = []
    for ch in consonants:
        base = _normalize_final(ch)
        if cipher == "Standard":
            result.append((ch, STANDARD.get(base, 0)))
        elif cipher == "Katan":
            result.append((ch, _katan_digit(STANDARD.get(base, 0))))
        elif cipher == "Gadol":
            val = GADOL_FINALS.get(ch, STANDARD.get(base, 0))
            result.append((ch, val))
        elif cipher == "Atbash":
            swapped = ATBASH_MAP.get(base, base)
            val = STANDARD.get(_normalize_final(swapped), 0)
            result.append((f"{ch}→{swapped}", val))
        elif cipher == "Albam":
            swapped = ALBAM_MAP.get(base, base)
            val = STANDARD.get(_normalize_final(swapped), 0)
            result.append((f"{ch}→{swapped}", val))
        elif cipher == "Avgad":
            swapped = AVGAD_MAP.get(base, base)
            val = STANDARD.get(_normalize_final(swapped), 0)
            result.append((f"{ch}→{swapped}", val))
        elif cipher == "Atbah":
            partner = ATBAH_MAP.get(base, base)
            val = ATBAH_VALUE.get(base, 0)
            result.append((f"{ch}↔{partner}", val))
        elif cipher == "Achbi":
            swapped = ACHBI_MAP.get(base, base)
            val = STANDARD.get(_normalize_final(swapped), 0)
            result.append((f"{ch}→{swapped}", val))
        elif cipher == "Siduri":
            result.append((ch, ORDINAL.get(base, 0)))
        elif cipher == "Ribua":
            v2 = STANDARD.get(base, 0)
            result.append((f"{ch}²", v2 * v2))
        elif cipher == "Kidmi":
            result.append((ch, KIDMI.get(base, 0)))
        elif cipher == "Agdat":
            swapped = AGDAT_MAP.get(base, base)
            val = STANDARD.get(_normalize_final(swapped), 0)
            result.append((f"{ch}→{swapped}", val))
        elif cipher == "Milui":
            spelling = LETTER_NAME_SPELLING.get(base, "")
            result.append((f"{ch}={spelling}", MILUI_VALS.get(base, 0)))
        elif cipher == "Neelam":
            spelling = LETTER_NAME_SPELLING.get(base, "")
            hidden = spelling[1:] if spelling else ""
            result.append((f"{ch}→{hidden}", NEELAM_VALS.get(base, 0)))
        elif cipher == "Meshulash":
            # Show the running prefix sum that this letter contributes to the stack.
            running = sum(STANDARD.get(_normalize_final(c2), 0)
                          for c2 in consonants[:consonants.index(ch) + 1])
            result.append((ch, running))
        elif cipher == "Kaful":
            pos = sum(1 for c2 in consonants[:consonants.index(ch) + 1]
                      if STANDARD.get(_normalize_final(c2), 0))
            val = STANDARD.get(base, 0)
            result.append((f"{ch}×{pos}", val * pos))
        elif cipher == "Mityashev":
            n = sum(1 for c2 in consonants if STANDARD.get(_normalize_final(c2), 0))
            val = STANDARD.get(base, 0)
            result.append((f"{ch}×{n}", val * n))
        else:
            result.append((ch, 0))
    return result


# SECTION 9.  SELF-TEST  (python app.py selftest)
# ---------------------------------------------------------------------------

def run_selftest() -> None:
    print("=== Gematria engine self-test ===")
    g11 = strip_to_consonants(SAMPLE_CORPUS[0].text)
    assert g_absolute(g11) == 2701, g_absolute(g11)
    print(f"Genesis 1:1 consonants: {g11}")
    print(f"  Standard = {g_absolute(g11)} (expected 2701)  OK")

    shalom = "שלום"
    assert g_absolute(shalom) == 376, g_absolute(shalom)
    assert g_siduri(shalom) == 52, g_siduri(shalom)
    print(f"  שלום Standard={g_absolute(shalom)} (376), Siduri={g_siduri(shalom)} (52)  OK")

    assert g_atbash("א") == 400 and g_atbash("ב") == 300
    assert g_albam("א") == 30 and g_avgad("א") == 2
    assert g_achbi("א") == 20 and g_atbah("א") == 9
    # Atbah must preserve the defining sum-to-10/100/1000 relation, incl. hundreds.
    assert g_atbah("ק") == 900 and g_atbah("ר") == 800
    assert g_atbah("ש") == 700 and g_atbah("ת") == 600
    for L, total in (("א", 10), ("ה", 10), ("י", 100), ("נ", 100),
                     ("ק", 1000), ("ר", 1000), ("ש", 1000), ("ת", 1000)):
        assert g_absolute(L) + g_atbah(L) == total, (L, g_atbah(L))
    assert g_gadol("ם") == 600 and g_absolute("ם") == 40
    assert g_ribua("אב") == 5 and g_kidmi("ג") == 6
    assert g_katan("ר") == 2 and g_katan("י") == 1
    # HaNikud: consonant-only string → 0; cantillated בְּרֵאשִׁ֖ית → 5
    # (sheva=2 + tsere=2 + hiriq=1; dagesh and taamim excluded)
    assert g_nikud("שלום") == 0, g_nikud("שלום")
    assert g_nikud("בְּרֵאשִׁ֖ית") == 5, g_nikud("בְּרֵאשִׁ֖ית")
    assert g_nikud(SAMPLE_CORPUS[0].text) > 0
    # New ciphers — spot-checks using חבד (ח=8, ב=2, ד=4)
    chabad = "חבד"
    assert g_agdat(chabad) == 20,          g_agdat(chabad)        # ח→י(10)+ב→ד(4)+ד→ו(6)
    assert g_katan_mispari(chabad) == 5,   g_katan_mispari(chabad) # 14 → 1+4=5
    assert g_milui(chabad) == 1264,        g_milui(chabad)         # חית+בית+דלת
    assert g_neelam(chabad) == 1250,       g_neelam(chabad)        # ית+ית+לת
    assert g_meshulash(chabad) == 32,      g_meshulash(chabad)     # 8+10+14
    assert g_kaful(chabad) == 24,          g_kaful(chabad)         # 8×1+2×2+4×3
    assert g_mityashev(chabad) == 42,      g_mityashev(chabad)     # (8+2+4)×3
    assert g_kolel_ehad(chabad) == 15,     g_kolel_ehad(chabad)    # 14+1
    assert g_kolel_otiyot(chabad) == 17,   g_kolel_otiyot(chabad)  # 14+3
    # Structural: every cipher must have a display name and blurb
    assert set(CIPHER_NAMES) == set(CIPHER_DISPLAY_NAMES) == set(CIPHER_BLURB), \
        "CIPHERS / CIPHER_DISPLAY_NAMES / CIPHER_BLURB keys out of sync"
    print(f"  All {len(CIPHER_NAMES)} ciphers pass spot-checks  OK")

    fh, sh = split_halves_by_atnach(SAMPLE_CORPUS[0].text)
    print(f"  Gen 1:1 first half  : {fh}")
    print(f"  Gen 1:1 second half : {sh}")
    assert fh and sh

    # Regression: a {פ}/{ס} paragraph marker must NOT leak into any total.
    for vi in (4, 8):                      # Gen 1:5 has {פ}, Lev 1:1 has {ס}
        v = SAMPLE_CORPUS[vi]
        verse_cons = strip_to_consonants(v.text)
        word_sum = sum(g_absolute(w) for w in tokenize_words(v.text))
        assert g_absolute(verse_cons) == word_sum, (
            v.book, v.chapter, v.verse, g_absolute(verse_cons), word_sum)
        assert not (verse_cons.endswith("פ") or verse_cons.endswith("ס")) \
            or verse_cons in (v.text,)     # no trailing stray marker letter
    print("  Paragraph markers excluded from gematria (verse == Σwords)  OK")

    forks_811 = fork_verse(SAMPLE_CORPUS[9])
    tracks = {f.variant_track for f in forks_811}
    print(f"  Esther 8:11 tracks  : {sorted(tracks)}")
    assert "TextVariant" in tracks
    ksiv_v = next(f for f in forks_811 if f.variant_track == "Ksiv")
    doub_v = next(f for f in forks_811 if f.variant_track == "TextVariant")
    assert (g_absolute(doub_v.full_consonants)
            - g_absolute(ksiv_v.full_consonants) == g_absolute("ו"))
    print(f"    Ksiv abs={g_absolute(ksiv_v.full_consonants)}  "
          f"Doublet abs={g_absolute(doub_v.full_consonants)}  (+vav=6)  OK")

    forks_kk = fork_verse(SAMPLE_CORPUS[-1])
    kk_tracks = {f.variant_track for f in forks_kk}
    print(f"  Tehillim 100:3 tracks: {sorted(kk_tracks)}")
    assert "Kri" in kk_tracks

    conn = build_database(SAMPLE_CORPUS)
    n_units = conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    n_pat = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
    print(f"  DB built: {n_units} units, {n_pat} patterns logged  OK")

    res = search_value(conn, "Standard", 2701)
    assert (res["Value"] == 2701).any()
    print(f"  Search Standard=2701 -> {len(res)} hit(s)  OK")

    res_c = search_value(conn, "Standard", 2700, colel=True)
    assert (res_c["Value"] == 2701).any()
    print(f"  Colel search 2700±1 -> {len(res_c)} hit(s) (incl. 2701)  OK")

    ext = extremes_table(conn, ["Verse", "Perek", "Parsha", "Petucha", "Setuma"])
    print("  Extremes table:")
    print(ext.to_string(index=False))
    print("\n=== ALL SELF-TESTS PASSED ===")
    conn.close()


# ---------------------------------------------------------------------------
# SECTION 10.  STREAMLIT USER INTERFACE
# ---------------------------------------------------------------------------

def run_app() -> None:
    import streamlit as st

    st.set_page_config(page_title="Tanach Gematria Engine",
                       page_icon="📜", layout="wide",
                       initial_sidebar_state="collapsed")
    @st.cache_resource(show_spinner="Loading Tanach…")
    def _build_connection(extra_refs_key: str, _nonce: int):
        bundled = load_from_jsonl()
        verses = bundled if bundled else list(SAMPLE_CORPUS)
        verses = apply_textual_variants(verses)
        fetched_ok = True
        if extra_refs_key:
            refs = [r.strip() for r in extra_refs_key.split(";") if r.strip()]
            fetched = load_from_sefaria(refs)
            if fetched:
                verses += fetched
            elif refs:
                fetched_ok = False
        verse_index = {(v.book, v.chapter, v.verse): v for v in verses}
        # Fast path: restore pre-built DB from disk (baked into Docker image).
        # Skips the 20–30s cipher computation on cold starts.
        if not extra_refs_key and PREBUILT_DB.exists():
            disk = sqlite3.connect(str(PREBUILT_DB), check_same_thread=False)
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            disk.backup(conn)
            disk.close()
            return conn, len(verses), True, verse_index
        return build_database(verses), len(verses), fetched_ok, verse_index

    def get_connection(extra_refs_key: str):
        nonce = st.session_state.get("sefaria_retry_nonce", 0)
        conn, n, ok, verse_index = _build_connection(extra_refs_key, nonce)
        if not ok:
            st.warning("Couldn't fetch the requested Sefaria refs "
                       "(offline or rate-limited). Showing the base corpus without them.")
            if st.button("Retry Sefaria fetch"):
                st.session_state["sefaria_retry_nonce"] = nonce + 1
                st.rerun()
        return conn, n, verse_index

    conn, n_loaded, verse_index = get_connection("")

    with st.sidebar:
        st.header("⚙️ Corpus")
        st.caption(f"{n_loaded:,} Masoretic verses — loaded from bundled corpus.")
        st.divider()
        st.subheader(f"Active methods ({len(CIPHER_NAMES)})")
        st.write(", ".join(CIPHER_NAMES))
        st.caption("Traditional: Standard, Katan, Gadol, Atbash, Albam, Atbah, Avgad. "
                   "Researched additions: Siduri, Ribua, Kidmi, Achbi, HaNikud.")

    DETAIL_BOUNDARIES = {"Word", "FirstHalf", "SecondHalf", "Verse", "Petucha", "Setuma"}

    def _paragraph_run(book, chapter, verse):
        """Return all VerseInputs in the same Petucha/Setuma block as (book, chapter, verse)."""
        seq = sorted(
            (v for v in verse_index.values() if v.book == book),
            key=lambda v: (v.chapter, v.verse),
        )
        block, target = [], (int(chapter), int(verse))
        for v in seq:
            block.append(v)
            if detect_paragraph_marker(v.text):
                if any((b.chapter, b.verse) == target for b in block):
                    return block
                block = []
        return block or None

    def _highlight_in_verse(cantillated: str, boundary: str, matched_cons) -> str:
        """Return cantillated text as HTML with the matched sub-unit wrapped in <mark>."""
        import re as _re
        if boundary in ("FirstHalf", "SecondHalf") and ATNACH in cantillated:
            idx = cantillated.index(ATNACH)
            end = cantillated.find(" ", idx)
            split = end if end != -1 else len(cantillated)
            first, rest = cantillated[:split], cantillated[split:]
            if boundary == "FirstHalf":
                return f"<mark>{first}</mark>{rest}"
            return f"{first}<mark>{rest}</mark>"
        if boundary == "Word" and matched_cons:
            # Split on whitespace AND maqaf so sub-tokens align with tokenize_words;
            # maqaf-joined pairs (עַל־פְּנֵי) are two DB words, not one space-token.
            parts = _re.split(r"([\s־]+)", cantillated)
            result, found = [], False
            for part in parts:
                if not found and part and strip_to_consonants(part) == matched_cons:
                    result.append(f"<mark>{part}</mark>")
                    found = True
                else:
                    result.append(part)
            return "".join(result)
        return cantillated

    def render_verse_detail(book, chapter, verse, boundary, matched_text=None,
                            active_method=None):
        if boundary not in DETAIL_BOUNDARIES:
            return
        v = verse_index.get((book, int(chapter), int(verse)))
        if v is None:
            st.info("Source text not available for this unit.")
            return
        friendly_boundary = BOUNDARY_LABELS.get(boundary, boundary)
        st.markdown(f"**{book} {chapter}:{verse}** · _{friendly_boundary}_")
        sub_unit = boundary in ("Word", "FirstHalf", "SecondHalf")
        if sub_unit and v.text:
            matched_cons = strip_to_consonants(matched_text) if matched_text else None
            highlighted = _highlight_in_verse(v.text, boundary, matched_cons)
            st.markdown(f"**Cantillated:** {highlighted}", unsafe_allow_html=True)
        else:
            st.markdown(f"**Cantillated:** {v.text}")
        # Values: matched sub-unit when available, full verse otherwise
        if sub_unit and matched_text:
            cons = strip_to_consonants(matched_text)
            st.markdown(f"**Matched consonants:** `{cons}`")
        else:
            cons = strip_to_consonants(v.text)
            st.markdown(f"**Consonants:** `{cons}`")
        # Compute values — pass cantillated text for HaNikud
        cantillated_src = matched_text if (sub_unit and matched_text) else v.text
        vals = compute_all_ciphers(cons, cantillated_src)
        st.dataframe(pd.DataFrame([vals]), use_container_width=True, hide_index=True)
        # Letter-by-letter breakdown for the active method
        if active_method and active_method in CIPHERS:
            if active_method == "HaNikud":
                nikud_val = g_nikud(cantillated_src)
                st.caption(f"**{active_method}:** {CIPHER_BLURB.get(active_method, '')} "
                           f"Dot count = {nikud_val}")
            else:
                breakdown = cipher_breakdown(active_method, cons)
                if breakdown:
                    parts = " + ".join(f"{lbl}({val})" for lbl, val in breakdown)
                    total = sum(val for _, val in breakdown)
                    st.caption(f"**{active_method}:** {parts} = {total}")
        if boundary in ("Petucha", "Setuma"):
            run = _paragraph_run(book, chapter, verse)
            if run and len(run) > 1:
                st.markdown("**Full paragraph block:**")
                for rv in run:
                    st.markdown(f"- {rv.book} {rv.chapter}:{rv.verse} — {rv.text}")

    tab_guide, tab1, tab2, tab3, tab4 = st.tabs([
        "📖 Guide & Sources",
        "1 · Phrase & Name Matcher",
        "2 · Scriptural Structural Explorer",
        "3 · Textual Echoes & Anomalies",
        "4 · Macro Statistical Dashboard",
    ])

    # ===================== TAB GUIDE: GUIDE & SOURCES ==================
    with tab_guide:
        st.title("Tanach Gematria Search & Structural Pattern Engine")
        st.markdown(
            "A multi-method Hebrew gematria engine over the complete Masoretic text — "
            "23,206 cantillated verses sourced from Sefaria. "
            "Search for phrases and names, explore structural patterns, and analyse the "
            "statistical fingerprint of the Tanach across 12 gematria methods."
        )
        st.divider()

        with st.expander("How to use this app", expanded=True):
            st.caption(
                "📖 **Guide & Sources** (this tab) — Start here. "
                "Explains all 12 gematria methods with earliest Talmudic or medieval sources, "
                "reading tracks, boundary types, and the Rule of the Colel. "
                "Also contains the full Masoretic variant registry."
            )

            st.markdown("**1 · Phrase & Name Matcher**")
            st.markdown(
                "Type any Hebrew word, name, or phrase. The engine strips vowel marks and "
                "cantillation down to the 22 consonants and computes values across all 12 methods "
                "simultaneously. Select a method to see every matching structural unit in the "
                "Tanach — word, half-verse, verse, paragraph, or chapter. Click any result row "
                "to open the full cantillated verse with the matched portion highlighted and a "
                "letter-by-letter breakdown for the chosen method. "
                "Toggle **Rule of the Colel (±1)** to also match values one above or below — "
                "a standard leniency in traditional gematria practice. "
                "Open **🔀 Cross-method coincidences** below the results to see a 12×12 matrix "
                "showing how every cipher value of your input matches every corpus method — "
                "rare coincidences are highlighted, and you can drill into any pair."
            )

            st.markdown("**2 · Scriptural Structural Explorer**")
            st.markdown(
                "Browse the entire Tanach by structural unit: Chapter (פרק Perek), "
                "Torah portion (פרשה Parsha), open paragraph (Pesucha פ), "
                "closed paragraph (Setuma ס), or individual Verse (פסוק). "
                "Every row shows gematria totals under all 12 methods for that block. "
                "Click a row to open the verse detail panel."
            )

            st.markdown("**3 · Textual Echoes & Anomalies**")
            st.markdown(
                "The engine automatically scans the corpus for three structural patterns:\n"
                "- **Internal Balance** — a verse whose two halves (split at the Asnachta mark) "
                "share the same gematria value, or differ by only 1 (Colel).\n"
                "- **Proximity Echo** — two consecutive verses sharing the same value under a given method.\n\n"
                "A **Cross-Method Half-Verse Balance** section below the pattern table lets you "
                "pick any two methods and find verses where the first half under method X equals "
                "the second half under method Y — a cross-method extension of Internal Balance.\n\n"
                "Filter by pattern type or method, then click a row to see the referenced verses."
            )

            st.markdown("**4 · Macro Statistical Dashboard**")
            st.markdown(
                "High-level statistics across the full corpus: highest and lowest values by structure, "
                "value-distribution histograms, a 12-method correlation heatmap, a per-book fingerprint "
                "chart, and integer ranges with no verse representation. All charts are interactive — "
                "hover, zoom, and download. A **cross-method half-verse balance heatmap** at the "
                "bottom shows, for every method pair, the fraction of verses whose first half "
                "(row method) equals the second half (column method)."
            )


        st.divider()
        st.subheader("📖 Reference material")
        st.caption(
            "All method rules are exact (verified against the engine code). "
            "Historical attributions are traditional and noted where uncertain."
        )

        with st.expander("The 21 gematria methods", expanded=True):
            st.table(pd.DataFrame([
                {"Method": "Standard",
                 "Hebrew": "מספר הכרחי / ישר (Mispar Hechrachi)",
                 "Rule": "Standard values: א=1 … י=10, כ=20 … ק=100 … ת=400. Finals = same as base form.",
                 "Earliest Source": "29th hermeneutical rule of the Baraita of R. Eliezer ben Yose ha-Gelili (c. 200 CE). BT Sanhedrin 38a; BT Nedarim 32a (318 servants = אֱלִיעֶזֶר). The term 'gematriot' appears in Mishnah Avot 3:18."},
                {"Method": "Katan",
                 "Hebrew": "מספר קטן (Mispar Katan)",
                 "Rule": "Reduce each letter to its significant digit (drop trailing zeros: ק=1, מ=4), then sum.",
                 "Earliest Source": "Medieval. No Talmudic source for this specific reduction. Formalized in Hasidei Ashkenaz tradition (12th–13th c.), appearing in works such as Sefer Gematriot (attr. R. Yehuda he-Hasid, d. 1217)."},
                {"Method": "Gadol",
                 "Hebrew": "מספר גדול (Mispar Gadol)",
                 "Rule": "Like Standard, but final forms carry 500–900: ך=500, ם=600, ן=700, ף=800, ץ=900.",
                 "Earliest Source": "27-letter sequence including finals described in Sefer Yetzirah 2:2 (3rd–6th c. CE). Consolidated and systematized in Siftei Yeshanim (R. Shabbethai Bass, 17th c.)."},
                {"Method": "Atbash",
                 "Hebrew": "אתב\"ש (At-Bash)",
                 "Rule": "Mirror the alphabet: א↔ת, ב↔ש, ג↔ר … then Standard values of the swapped letters.",
                 "Earliest Source": "The oldest attested gematria method — appears in the Tanach itself. 'Sheshach' (שֵׁשַׁךְ) in Jeremiah 25:26 and 51:41 is Babel (בָּבֶל) by Atbash. Recognized explicitly in BT Sanhedrin 22b. Classified as a temurah system in Sefer Yetzirah ch. 2."},
                {"Method": "Albam",
                 "Hebrew": "אלב\"ם (Al-Bam)",
                 "Rule": "Split 22 letters into two groups of 11; swap across groups: א↔ל, ב↔מ, ג↔נ … (ROT-11).",
                 "Earliest Source": "Explicitly detailed in Yalkut Shimoni (Yisro, Remez 271). Classical temurah in Sefer Yetzirah ch. 2 (3rd–6th c. CE)."},
                {"Method": "Atbah",
                 "Hebrew": "אטב\"ח (At-Bach)",
                 "Rule": "Pairs whose values sum to 10/100/1000: א↔ט, ב↔ח; י↔צ, כ↔פ; ק↔ץ … Finals carry 600–900.",
                 "Earliest Source": "Attributed to Rabbi Chiya (late 2nd/early 3rd c. CE). The phrase 'in the Atbah of Rabbi Chiya' (בְּאַטְבַּ״ח שֶׁל רַבִּי חִיָּיא) appears explicitly in BT Sukkah 52b. Also classified in the Baraita of 32 Hermeneutical Rules of R. Eliezer ben Yose ha-Gelili."},
                {"Method": "Avgad",
                 "Hebrew": "אבג\"ד (Av-Gad / Abgad)",
                 "Rule": "+1 cyclic shift: א→ב, ב→ג … ת→א. Then Standard values of the shifted letters. Also known as Mispar Ha'Ahari (next-letter value).",
                 "Earliest Source": "Codified in Ta'am Zekenim (R. Eliezer Ashkenazi). Cyclic letter-shifting tradition rooted in Sefer Yetzirah (3rd–6th c. CE). R. Abraham Abulafia (13th c.) employs the next-letter method in his prophetic Kabbalah texts."},
                {"Method": "Siduri",
                 "Hebrew": "מספר סידורי (Mispar Siduri)",
                 "Rule": "Ordinal position: א=1, ב=2 … ת=22. Sequence, not standard value.",
                 "Earliest Source": "Formally categorized as a gematria method in Pardes Rimonim (Sha'ar HaGematria, Gate 30) by R. Moshe Cordovero (1548). Ordinal counting is implicit in earlier Talmudic letter-position discussions (e.g. BT Shabbat 104a)."},
                {"Method": "Ribua",
                 "Hebrew": "מספר מרובע / פרטי (Mispar Meruba Prati)",
                 "Rule": "Square each individual letter's Standard value, then sum all squares (Σ vᵢ² — per letter, not the total squared).",
                 "Earliest Source": "Mainstreamed by the Ba'al HaTurim (R. Jacob ben Asher, 14th c.) in his Torah commentary. Also documented in Pardes Rimonim (Gate 30)."},
                {"Method": "Kidmi",
                 "Hebrew": "מספר קדמי (Mispar Kidmi / HaKadmon)",
                 "Rule": "Triangular cumulative: each letter's value = sum of all Standard values from א up to it. א=1, ב=3, ג=6 … ת=1495.",
                 "Earliest Source": "Mapped in Pardes Rimonim (Gate 30, Ch. 8) by R. Moshe Cordovero (1548)."},
                {"Method": "Achbi",
                 "Hebrew": "אכב\"י (Ach-Bi)",
                 "Rule": "Split into two 11-letter groups, reverse each internally: א↔כ, ב↔י … ל↔ת, מ↔ש …",
                 "Earliest Source": "Outlined as a structural matrix in Sefer Raziel HaMalach. Part of the temurah permutation tradition in Sefer Yetzirah ch. 2 (3rd–6th c. CE)."},
                {"Method": "HaNikud",
                 "Hebrew": "מספר הנקוד (Mispar HaNikud)",
                 "Rule": "Count the dots in each vowel mark (nikud): Sheva=2, Hiriq=1, Tsere=2, Segol=3, Patah=1, Kamatz=2, Holam=1, Kubutz=3, Hataf forms=3. Dagesh, meteg and shin/sin dots excluded. Returns 0 for unvocalised text.",
                 "Earliest Source": "Modern computational extension. No classical Talmudic or Midrashic source. Requires cantillated (vocalised) source text — only verse-level totals carry meaningful values in this engine."},
                {"Method": "Agdat",
                 "Hebrew": "אגד\"ת (Ag-Dat)",
                 "Rule": "+2 cyclic shift: א→ג, ב→ד … ש→א, ת→ב. Then Standard values of the shifted letters.",
                 "Earliest Source": "Explicitly detailed in Pardes Rimonim, Gate 22 (R. Moshe Cordovero, 1548). Companion to Avgad (+1) in the family of linear-shift temurah ciphers."},
                {"Method": "KatanMispari",
                 "Hebrew": "קטן מספרי (Mispar Katan Mispari)",
                 "Rule": "Sum all Standard values first; then iteratively reduce the grand total to a single digit (digital root). Differs from Katan, which reduces each letter before summing.",
                 "Earliest Source": "Cataloged by early Renaissance Jewish scholars; referenced in the 1906 Jewish Encyclopedia under gematria variants. Treated in Pardes Rimonim (Gate 30)."},
                {"Method": "Milui",
                 "Hebrew": "מילוי / מספר שמי (Mispar Milui)",
                 "Rule": "Spell each letter's full name as a Hebrew word, then sum Standard values of all spelling letters. א=אלף=111, ב=בית=412, ח=חית=418 …",
                 "Earliest Source": "A pillar of Lurianic Kabbalah (16th c.). Deployed in the Zoharic Sifra diTzni'uta (Book of Concealment). Comprehensively treated in Pardes Rimonim, Gate 30 (R. Moshe Cordovero, 1548)."},
                {"Method": "Neelam",
                 "Hebrew": "נעלם (Mispar Neelam — Hidden)",
                 "Rule": "Like Milui, but drop the first letter of each spelling — only the hidden remainder counts. א→לף=110, ח→ית=410 …",
                 "Earliest Source": "Formally codified in Pardes Rimonim (Sha'ar HaGematria, Gate 30). Used in Kabbalah to identify hidden spiritual energies sustaining an outer visible concept."},
                {"Method": "Meshulash",
                 "Hebrew": "מספר משולש (Mispar Meshulash)",
                 "Rule": "Stacked prefix sums: v₁ + (v₁+v₂) + (v₁+v₂+v₃) + … Each prefix sub-total is added to the running total. Note: distinct from Kidmi, which is a per-letter alphabet-triangular.",
                 "Earliest Source": "Zohar; Pardes Rimonim (R. Moshe Cordovero, 1548)."},
                {"Method": "Kaful",
                 "Hebrew": "מספר כפול (Mispar Kaful)",
                 "Rule": "Each letter's Standard value × its ordinal position within the unit: 1st letter × v₁ + 2nd letter × v₂ + … (ח in position 1 = 8×1=8; ב in position 2 = 2×2=4 …).",
                 "Earliest Source": "Detailed in Sefer Raziel HaMalach (medieval Kabbalistic compilation); used by Chassidei Ashkenaz (12th–13th c.) pietists."},
                {"Method": "Mityashev",
                 "Hebrew": "מספר מיושב (Mispar Mityashev)",
                 "Rule": "Each letter's Standard value × total letter count in the unit: Σ(vᵢ × N). For a 3-letter word, every letter's value is multiplied by 3.",
                 "Earliest Source": "Traced to early Italian Kabbalistic manuscripts; documented in operational gematria manuals."},
                {"Method": "KololEhad",
                 "Hebrew": "כולל (Kolel — Word)",
                 "Rule": "Standard total + 1. The word itself is counted as one additional collective unit. Widely used as a ±1 adjustment to link words whose values differ by one.",
                 "Earliest Source": "Ubiquitous in Chassidic philosophy and Kabbalah. Heavily employed by the Ba'al HaTurim (R. Jacob ben Asher, 14th c.) to link thematically related phrases."},
                {"Method": "KololOtiyot",
                 "Hebrew": "כולל אותיות (Kolel — Letters)",
                 "Rule": "Standard total + number of letters in the unit. Each letter adds 1 beyond its gematria value, reflecting the physical presence of the letter-vessels.",
                 "Earliest Source": "Kabbalistic practice; variant of the Kolel principle found across Chassidic and Kabbalistic literature."},
            ]))

        with st.expander("Variant tracks"):
            st.markdown("""
**Ksiv (כְּתִיב — "Written")** — The consonantal text exactly as written in the Torah scroll. The default track; every verse is recorded here. The Masoretes went to extraordinary lengths to preserve this text letter-perfect.

**Kri (קְרֵי — "Read")** — The text as traditionally *read aloud*, sometimes differing from the written form. Marginal notes mark every divergence. Different consonants → different gematria totals. Example: Psalms 100:3 written לֹא (alef), read לוֹ (vav), difference = 1 in Standard.

*Qere Perpetuum* — A subset of Kri: substitutions so consistent they receive only one marginal note for all occurrences. Chief example: in the Torah, הִיא ("she") is written as הוּא ("he") 33 times; a single note at Genesis 3:20 covers all (cf. BT Yevamot). Implemented via the Kri track.

**TextVariant (Masoretic Textual Variants)** — Documented variant readings of a specific word or phrase, forked as an alternate gematria row for the same verse. Two sub-categories are engine-active:
""")
            st.markdown("**Itture Sopherim — Five scribal omissions (BT Nedarim 37b)**")
            st.dataframe(pd.DataFrame([
                {"Reference": f"{b} {c}:{v}", "Received (Masoretic)": spec["from"],
                 "TextVariant (with vav)": spec["to"], "Note": spec["note"]}
                for (b, c, v), spec in TEXTUAL_VARIANT_SPECS.items()
                if spec["category"] == "Ittur Sopherim"
            ]), use_container_width=True, hide_index=True)
            st.markdown("**Esther doublets**")
            st.dataframe(pd.DataFrame([
                {"Reference": f"{b} {c}:{v}", "Received": spec["from"],
                 "Variant": spec["to"], "Note": spec["note"]}
                for (b, c, v), spec in TEXTUAL_VARIANT_SPECS.items()
                if spec["category"] == "Doublet"
            ]), use_container_width=True, hide_index=True)
            st.markdown("""
**Aggregate** — Structural totals (Perek/Parsha sums from Ksiv verses). Not a text variant; a statistical macro-unit.

---
**Tiqqune Sopherim (תיקוני סופרים) — 18 scribal corrections (documented, not engine-forked)**

These 18 places are where the Masoretic tradition records that scribes emended the text — mainly to remove anthropomorphisms or avoid theological offence. The received Masoretic text already contains the corrected reading. The "original" wording is preserved in rabbinic literature (Mekhilta, Sifre Num. §84, Yalkut Shimoni, Tanḥuma). Note: the exact list of 18 varies across sources.
""")
            st.dataframe(pd.DataFrame(TIQQUNE_SOPHERIM), use_container_width=True, hide_index=True)
            st.markdown("""
**Doublet passages (documented, not engine-forked)**

These are separate references that share nearly identical text — two distinct verses in two different books, not two readings of one verse. The fork engine doesn't apply here; they are best studied by comparing the two passages directly.
""")
            st.dataframe(pd.DataFrame(DOUBLET_PASSAGES), use_container_width=True, hide_index=True)

        with st.expander("Boundary types"):
            st.dataframe(pd.DataFrame([
                {"Boundary": "Word (תיבה)",      "Meaning": "Single word token, split on space and maqaf (־).",                                             "Why meaningful": "Smallest meaning-bearing unit; classic gematria target (name totals, first/last words)."},
                {"Boundary": "FirstHalf",         "Meaning": "From verse start to the Asnachta-bearing word (inclusive).",                                     "Why meaningful": "The Asnachta (֑) is the verse's primary cantillation pause — its main syntactic division."},
                {"Boundary": "SecondHalf",        "Meaning": "From after the Asnachta to verse end.",                                                          "Why meaningful": "The second syntactic unit; internal balance between halves is a recognized gematria pattern."},
                {"Boundary": "Verse (פסוק)",      "Meaning": "One Masoretic verse, ending at Sof Pasuq (׃).",                                               "Why meaningful": "The canonical citation and reading unit."},
                {"Boundary": "Pesucha / Petucha (פ)", "Meaning": "'Open' paragraph — a full blank line to end of scroll column; a major thematic break.",     "Why meaningful": "A deliberate Masoretic division, larger than a verse. One of two authentic paragraph units."},
                {"Boundary": "Setuma (ס)",        "Meaning": "'Closed' paragraph — a short gap mid-line; a minor thematic break.",                           "Why meaningful": "The finer Masoretic paragraph division. Both Petucha and Setuma predate chapter numbering."},
                {"Boundary": "Perek (פרק)",       "Meaning": "Chapter boundary.",                                                                             "Why meaningful": "Introduced ~13th century CE (not a Masoretic unit). Convenient macro-aggregation for reference."},
                {"Boundary": "Parsha (פרשה)",     "Meaning": "Weekly Torah reading portion.",                                                                 "Why meaningful": "The liturgical macro-unit for Torah reading; largest aggregation level."},
            ]), use_container_width=True, hide_index=True)

        with st.expander("The Rule of the Colel (כּוֹלֵל)"):
            st.markdown("""
The *Colel* (כּוֹלֵל, "the inclusive / the whole") permits adding or subtracting **1** to a gematria total — conventionally counting "the word itself" or "the number as a unit" as one extra. A match within ±1 of the target is treated as equivalent.

This principle appears throughout Kabbalistic and Hasidic commentary and is invoked by various authorities (including the Vilna Gaon and Baal HaTurim–style annotations). Its precise origin is diffuse; present it as a traditional/widely-used principle rather than pinning it to a single text.

**How the toggle works in this engine:** when enabled, `search_value` matches `target−1`, `target`, and `target+1` (SQL `BETWEEN`), and results are ordered by proximity (`ABS(cipher − value)`). The internal-balance detector likewise flags half-verses equal within ±1 as `colel±1`.
""")

    # ======================= TAB 1: PHRASE MATCHER =======================
    with tab1:
        st.subheader("Phrase & Name Matcher")
        c1, c2 = st.columns([3, 2])
        with c1:
            raw = st.text_input(
                "Hebrew phrase or name",
                value="שלום", help="Type or paste Hebrew. Nikud and ta'amim are "
                "stripped automatically; only the 22 consonants are counted.")
        with c2:
            colel = st.toggle("Rule of the Colel (±1)", value=False,
                              help="Also match Value−1 and Value+1.")
        cons = normalize_query(raw)
        st.markdown(f"**Cleaned consonants:** `{cons or '—'}`")

        cc1, cc2 = st.columns(2)
        with cc1:
            tracks = st.multiselect(
                "Reading tracks",
                ["Ksiv", "Kri", "TextVariant"],
                default=["Ksiv"],
                format_func=lambda t: TRACK_LABELS.get(t, t))
        with cc2:
            bounds = st.multiselect(
                "Text units",
                ["Word", "FirstHalf", "SecondHalf", "Verse",
                 "Perek", "Parsha", "Petucha", "Setuma"],
                default=["Word", "Verse", "FirstHalf", "SecondHalf"],
                format_func=lambda b: BOUNDARY_LABELS.get(b, b))

        # Perek/Parsha rows are stored under the "Aggregate" track (a DB tag,
        # not a reading tradition). Auto-include it when those boundaries are selected.
        effective_tracks = list(tracks)
        if any(b in (bounds or []) for b in ("Perek", "Parsha")) and "Aggregate" not in effective_tracks:
            effective_tracks.append("Aggregate")

        if cons:
            payload = search_phrase(conn, cons, colel=colel,
                                    tracks=effective_tracks or None, boundaries=bounds or None)
            vals = payload["values"]
            st.markdown("#### Computed values across all methods")
            st.dataframe(pd.DataFrame([vals]), use_container_width=True,
                         hide_index=True)

            cipher = st.selectbox("Show matches for method", CIPHER_NAMES, index=0,
                                  format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c))
            st.caption(CIPHER_BLURB.get(cipher, ""))
            res = payload["results"][cipher]
            tgt = vals[cipher]
            st.markdown(
                f"#### Matches for **{cipher} = {tgt}**"
                + (f" (Colel window {tgt-1}–{tgt+1})" if colel else "")
                + f" — {len(res)} result(s)")
            if res.empty:
                st.info("No structural unit in the loaded corpus matches this value. "
                        "Load more chapters from Sefaria to widen the search space.")
            else:
                event = st.dataframe(
                    res, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row", key="t1_sel")
                sel = event.selection.rows
                if sel:
                    row = res.iloc[sel[0]]
                    with st.expander("📜 Verse detail", expanded=True):
                        render_verse_detail(
                            row["Book"], row["Chapter"], row["Verse"], row["Boundary"],
                            matched_text=row.get("Text"), active_method=cipher)

            with st.expander("🔀 Cross-method coincidences", expanded=False):
                st.caption(
                    "Rows = your word under **Method A** (value shown); columns = "
                    "corpus **Method B** searched. Cell = match count, colored by "
                    "coincidence rate — warmer color = rarer = more notable. "
                    "Colel, track, and unit filters are shared with the search above."
                )
                a_vals = dict(vals)
                a_vals["HaNikud"] = g_nikud(raw)
                pop = boundary_population(conn, effective_tracks or None, bounds or None) or 1
                xm_sparse = st.toggle(
                    "Only show notable coincidences (rate < 5%)", key="xm_sparse"
                )
                xm_df = _xm_count_matrix(
                    conn, a_vals, colel, effective_tracks or None, bounds or None
                )
                if xm_sparse:
                    xm_df = xm_df.where(xm_df / pop < 0.05, 0)
                rate_mat = xm_df / pop
                st.dataframe(
                    xm_df.style.background_gradient(
                        cmap="YlOrRd_r", axis=None,
                        gmap=rate_mat.to_numpy(),
                    ),
                    use_container_width=True,
                )
                st.caption(
                    "★ The **HaNikud** column is non-zero only for Verse / Petucha / "
                    "Setuma rows — word and half-verse units store no vowel data."
                )
                st.markdown("**Drill into a pair**")
                dc1, dc2 = st.columns(2)
                with dc1:
                    drill_a = st.selectbox(
                        "Method A", CIPHER_NAMES, key="xm_drill_a",
                        format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c)
                    )
                with dc2:
                    drill_b = st.selectbox(
                        "Method B", CIPHER_NAMES, index=0, key="xm_drill_b",
                        format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c)
                    )
                drill_val = a_vals[drill_a]
                st.markdown(
                    f"**{drill_a}({raw.strip()}) = {drill_val}** "
                    f"→ corpus units with **{drill_b} = {drill_val}**"
                    + (" ± 1" if colel else "")
                )
                drill_res = search_value(
                    conn, drill_b, drill_val, colel, effective_tracks or None, bounds or None
                )
                if drill_res.empty:
                    st.info("No corpus unit matches this pair at the current filters.")
                else:
                    ev_drill = st.dataframe(
                        drill_res, use_container_width=True, hide_index=True,
                        on_select="rerun", selection_mode="single-row",
                        key="xm_drill_sel",
                    )
                    if ev_drill.selection.rows:
                        rd = drill_res.iloc[ev_drill.selection.rows[0]]
                        with st.expander("📜 Verse detail", expanded=True):
                            render_verse_detail(
                                rd["Book"], rd["Chapter"], rd["Verse"],
                                rd["Boundary"], matched_text=rd.get("Text"),
                                active_method=drill_b,
                            )
        else:
            st.warning("Enter a Hebrew or transliterable phrase to search.")

    # ===================== TAB 2: STRUCTURAL EXPLORER =====================
    with tab2:
        st.subheader("Scriptural Structural Explorer")
        kind = st.radio(
            "Browse by",
            ["Perek", "Parsha", "Petucha", "Setuma", "Verse"],
            horizontal=True,
            format_func=lambda b: BOUNDARY_LABELS.get(b, b))
        df = structure_frame(conn, kind)
        if df.empty:
            st.info(f"No {BOUNDARY_LABELS.get(kind, kind)} units in the loaded corpus yet.")
        else:
            display_cols = (["book", "chapter", "verse", "parsha",
                             "variant_track"] + CIPHER_NAMES)
            show = df[[c for c in display_cols if c in df.columns]].rename(
                columns={"book": "Book", "chapter": "Chapter", "verse": "Verse",
                         "parsha": "Parsha", "variant_track": "Track"})
            show["Track"] = show["Track"].map(lambda t: TRACK_LABELS.get(t, t))
            q = st.text_input("Filter (book / parsha contains)", "")
            if q:
                mask = (show["Book"].str.contains(q, case=False, na=False) |
                        show["Parsha"].str.contains(q, case=False, na=False))
                show = show[mask]
            st.caption("Click any gematria value cell to find every unit in the corpus "
                       "that shares that number, across all 12 methods.")
            t2_col_config = {
                "Book":    st.column_config.TextColumn("Book", width="medium"),
                "Chapter": st.column_config.NumberColumn("Chapter", width="small"),
                "Verse":   st.column_config.NumberColumn("Verse", width="small"),
                "Parsha":  st.column_config.TextColumn("Parsha", width="medium"),
                "Track":   st.column_config.TextColumn("Track", width="small"),
            }
            for _c in CIPHER_NAMES:
                t2_col_config[_c] = st.column_config.NumberColumn(_c, width="small")
            event2 = st.dataframe(
                show, use_container_width=True, hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config=t2_col_config,
                height=400,
                key="t2_sel")
            st.caption(f"{len(show)} {BOUNDARY_LABELS.get(kind, kind)} unit(s). "
                       "Every method column is an indexed gematria total for that block.")

            cipher_pick = st.selectbox(
                "Look up matches for which method's value?",
                CIPHER_NAMES, index=0, key="t2_cipher_pick",
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                help="Pick a gematria method, then select a row above. The bottom "
                     "panel lists every corpus unit sharing that row's value under "
                     "any of the 12 methods.")

            sel_rows = event2.selection.rows
            if sel_rows:
                row2 = show.iloc[sel_rows[0]]

                # Show this row's values across all 12 methods.
                summary = {c: int(row2[c]) for c in CIPHER_NAMES if c in row2.index}
                st.markdown("**Selected unit — values across all 12 methods:**")
                st.dataframe(pd.DataFrame([summary]),
                             use_container_width=True, hide_index=True)

                cell_val = int(row2[cipher_pick])
                st.markdown(
                    f"**{cipher_pick} = {cell_val}** — every unit in the corpus "
                    f"that shares this value (up to 50 per method):")
                match_df = search_value_all_methods(conn, cell_val)
                if match_df.empty:
                    st.info("No corpus unit has this exact value under any method.")
                    if kind in DETAIL_BOUNDARIES:
                        with st.expander("📜 Verse detail", expanded=True):
                            render_verse_detail(
                                row2["Book"], row2["Chapter"], row2["Verse"], kind)
                else:
                    ev_match = st.dataframe(
                        match_df[["Method", "Book", "Chapter", "Verse",
                                  "Boundary", "Text", "Value"]],
                        use_container_width=True, hide_index=True,
                        on_select="rerun", selection_mode="single-row",
                        key="t2_match_sel")
                    st.caption(f"{len(match_df)} match(es) across "
                               f"{match_df['Method'].nunique()} method(s).")
                    if ev_match.selection.rows:
                        rm = match_df.iloc[ev_match.selection.rows[0]]
                        with st.expander("📜 Verse detail", expanded=True):
                            render_verse_detail(
                                rm["Book"], rm["Chapter"], rm["Verse"],
                                rm["Boundary"], matched_text=rm.get("Text"),
                                active_method=str(rm.get("Method", "")))

    # ===================== TAB 3: ECHOES & ANOMALIES =====================
    with tab3:
        st.subheader("Textual Echoes & Anomalies")

        # --- Filter controls ---
        col_m1, col_m2, col_opts = st.columns([3, 3, 2])
        with col_m1:
            t3_ma = st.multiselect(
                "Method A", CIPHER_NAMES, default=["Standard"], key="t3_ma",
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                help="Gematria method for the first element of each pattern")
        with col_m2:
            t3_mb = st.multiselect(
                "Method B", CIPHER_NAMES, default=["Standard"], key="t3_mb",
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                help="Method for the second element. When Cross-method is off, Method A is used for both.")
        with col_opts:
            t3_cross = st.toggle(
                "Cross-method", False, key="t3_cross",
                help="When on, all A×B method combinations are tested. "
                     "When off, only same-method (A=B) patterns.")
            t3_colel = st.toggle("Colel (±1)", False, key="t3_colel")

        col_pt, col_bnd, col_mv, col_foc = st.columns([3, 2, 2, 3])
        with col_pt:
            sel_patterns = st.multiselect(
                "Pattern types",
                ["Internal Balance", "Proximity Echo", "Cross-Method Echo"],
                default=["Internal Balance", "Proximity Echo"],
                key="t3_ptypes")
        with col_bnd:
            t3_boundary = st.selectbox(
                "Unit type (Cross-Method Echo)", ["Verse", "Petucha", "Setuma"],
                key="t3_bnd",
                help="Boundary type used for the Cross-Method Echo search")
        with col_mv:
            t3_minval = st.number_input(
                "Min value filter", min_value=0, value=0, step=10, key="t3_minval",
                help="Exclude units whose gematria value is below this threshold")
        with col_foc:
            t3_focus = st.text_input(
                "Focus (e.g. 'Genesis 1' or 'Psalms')", "", key="t3_focus",
                help="Filter results to references containing this text")

        # Effective method sets
        eff_a = t3_ma or CIPHER_NAMES
        eff_b = (t3_mb if t3_cross else eff_a) or CIPHER_NAMES

        if ("Katan" in eff_a or "Katan" in eff_b) and int(t3_minval) < 41:
            st.warning(
                "⚠️ **Mispar Katan** collapses values to 1–40, producing a very high "
                "match rate. Set **Min value ≥ 41** or deselect Katan to reduce noise.")

        # --- Build unified results ---
        frames = []
        with st.spinner("Searching patterns…"):
            if "Internal Balance" in sel_patterns:
                ib = internal_balance_matches(
                    conn, eff_a, eff_b, colel=t3_colel,
                    min_value=int(t3_minval), limit=500)
                if not ib.empty:
                    frames.append(ib)
            if "Proximity Echo" in sel_patterns:
                pe = proximity_echo_matches(
                    conn, eff_a, colel=t3_colel,
                    min_value=int(t3_minval), limit=500)
                if not pe.empty:
                    frames.append(pe)
            if "Cross-Method Echo" in sel_patterns:
                wue = whole_unit_echo_matches(
                    conn, eff_a, eff_b, boundary=t3_boundary,
                    min_value=int(t3_minval), limit=300)
                if not wue.empty:
                    frames.append(wue)

        unified = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        if t3_focus.strip() and not unified.empty:
            foc = t3_focus.strip().lower()
            mask = (
                unified.get("Reference A", pd.Series(dtype=str))
                .str.lower().str.contains(foc, na=False)
                | unified.get("Reference B", pd.Series(dtype=str))
                .str.lower().str.contains(foc, na=False)
                | unified.get("Book", pd.Series(dtype=str))
                .str.lower().str.contains(foc, na=False)
            )
            unified = unified[mask]

        # --- Metrics (auto-update with filters) ---
        n_ib  = int((unified["Pattern"] == "Internal Balance").sum())  if not unified.empty else 0
        n_pe  = int((unified["Pattern"] == "Proximity Echo").sum())    if not unified.empty else 0
        n_wue = int((unified["Pattern"] == "Cross-Method Echo").sum()) if not unified.empty else 0
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            st.metric("Internal Balance", n_ib)
            st.caption("1st half ≈ 2nd half of the same verse")
        with mc2:
            st.metric("Proximity Echo", n_pe)
            st.caption("Two consecutive verses share a value")
        with mc3:
            st.metric("Cross-Method Echo", n_wue)
            st.caption("Any two units match across different methods (capped)")

        st.divider()

        if unified.empty:
            if not sel_patterns:
                st.info("Select at least one pattern type above.")
            else:
                st.info(
                    "No patterns found with the current filters. "
                    "Try different methods, lower Min value, or enable Colel.")
        else:
            display_cols = [c for c in
                ["Pattern", "Method A", "Method B", "Value A", "Value B",
                 "Reference A", "Reference B"]
                if c in unified.columns]
            ev3 = st.dataframe(
                unified[display_cols], use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="t3_sel")
            cap_parts = []
            if n_ib:  cap_parts.append(f"{n_ib:,} Internal Balance")
            if n_pe:  cap_parts.append(f"{n_pe:,} Proximity Echo")
            if n_wue: cap_parts.append(f"{n_wue:,} Cross-Method Echo")
            if len(unified) >= 500:
                cap_parts.append("*(list capped — narrow filters to see more)*")
            st.caption(" · ".join(cap_parts))

            if ev3.selection.rows:
                sel_row = unified.iloc[ev3.selection.rows[0]]
                pat_type  = str(sel_row.get("Pattern", ""))
                ref_a_str = str(sel_row.get("Reference A", ""))
                ref_b_str = str(sel_row.get("Reference B", ""))
                active_ma = str(sel_row.get("Method A", "Standard"))
                active_mb = str(sel_row.get("Method B", active_ma))

                with st.expander("📜 Referenced verses", expanded=True):
                    if "Internal Balance" in pat_type:
                        pairs = [
                            ("First half (before Asnachta)", ref_a_str, active_ma),
                            ("Second half (after Asnachta)", ref_b_str, active_mb),
                        ]
                    elif "Proximity Echo" in pat_type:
                        pairs = [
                            ("Verse A", ref_a_str, active_ma),
                            ("Verse B", ref_b_str, active_mb),
                        ]
                    else:
                        pairs = [
                            ("Unit A", ref_a_str, active_ma),
                            ("Unit B", ref_b_str, active_mb),
                        ]
                    for label, ref_str, meth in pairs:
                        parsed = parse_pattern_ref(ref_str)
                        if parsed:
                            book, chap, vs, boundary = parsed
                            st.markdown(f"**{label}**")
                            render_verse_detail(book, chap, vs, boundary,
                                                active_method=meth)

    # ===================== TAB 4: STATISTICS DASHBOARD ===================
    with tab4:
        st.subheader("Macro Statistical Dashboard")

        st.markdown("#### Highs & lows by structure — Standard method")
        ext = extremes_table(conn, ["Verse", "Perek", "Parsha",
                                    "Petucha", "Setuma", "Word"])
        if not ext.empty:
            st.dataframe(ext, use_container_width=True, hide_index=True)
            st.caption("All statistics use the **Standard** (Mispar Hechrachi) gematria method.")

        st.markdown("#### Value distributions across verses")
        # Each verse appears exactly once. The per-verse Petucha/Setuma rows are
        # the SAME verses re-tagged, so including them would double-count any
        # marker-bearing verse and skew the distribution; paragraph-level stats
        # live in the extremes table above instead.
        plot_df = structure_frame(conn, "Verse")

        if plot_df.empty:
            st.info("Not enough structural data to plot. Load chapters from Sefaria.")
        else:
            import plotly.express as px
            import plotly.figure_factory as ff
            import plotly.graph_objects as go

            # ---- Distribution histograms (interactive) ----
            hist_cols = ["Standard", "Katan", "Atbash"]
            hist_colors = ["#2c6fbb", "#bb572c", "#3aa66f"]
            for c, color in zip(hist_cols, hist_colors):
                fig_h = px.histogram(
                    plot_df, x=c, nbins=40, color_discrete_sequence=[color],
                    title=f"{c} — value distribution across all verses",
                    labels={c: "Gematria value", "count": "Verses"})
                fig_h.update_layout(bargap=0.05, height=320,
                                    margin=dict(t=40, b=30, l=40, r=20))
                st.plotly_chart(fig_h, use_container_width=True,
                                config={"scrollZoom": False})
            st.caption(f"{len(plot_df)} verse(s), each counted once (כְּתִיב Ksiv track). "
                       "Hover for exact counts; click legend to toggle; drag to zoom.")

            # ---- Method correlation heatmap (interactive) ----
            st.markdown("#### How the methods relate to each other")
            st.caption("Pearson correlation across all verse totals. "
                       "Methods with high correlation produce similar rankings; "
                       "low/negative correlation highlights structurally distinct searches. "
                       "Hover any cell to see the exact value.")
            numeric_cols = [c for c in CIPHER_NAMES if c in plot_df.columns]
            corr = plot_df[numeric_cols].corr().round(2)
            fig_corr = px.imshow(
                corr, text_auto=True, color_continuous_scale="RdBu_r",
                zmin=-1, zmax=1, aspect="auto",
                title="Method correlation (verse totals)")
            fig_corr.update_layout(height=480,
                                   margin=dict(t=50, b=30, l=120, r=20))
            st.plotly_chart(fig_corr, use_container_width=True,
                            config={"scrollZoom": False})

            # ---- Book fingerprint (interactive) ----
            st.markdown("#### Book fingerprint — average Standard value per pasuk")
            st.caption("Mean Standard gematria per book. "
                       "Books with longer or less-common words tend to score higher. "
                       "Hover for exact values.")
            if "book" in plot_df.columns and "Standard" in plot_df.columns:
                book_means = (plot_df.groupby("book")["Standard"]
                              .mean().round(1).sort_values(ascending=True)
                              .reset_index())
                book_means.columns = ["Book", "Mean Standard"]
                fig_bk = px.bar(
                    book_means, x="Mean Standard", y="Book", orientation="h",
                    color="Mean Standard", color_continuous_scale="YlOrBr",
                    title="Average verse (pasuk) value by book — Standard method")
                fig_bk.update_layout(height=max(350, len(book_means) * 18),
                                     showlegend=False, coloraxis_showscale=False,
                                     margin=dict(t=50, b=30, l=140, r=20))
                st.plotly_chart(fig_bk, use_container_width=True,
                                config={"scrollZoom": False})

            st.markdown("#### Unrepresented value ranges (Standard)")
            dz = density_gaps(conn, "Standard", "Verse")
            st.write(f"Observed range **{dz['min']}–{dz['max']}**, "
                     f"**{len(dz['present'])}** distinct values present, "
                     f"**{len(dz['gaps'])}** unrepresented range(s).")
            if dz["gaps"]:
                gap_df = pd.DataFrame(
                    [{"Range start": g[0], "Range end": g[1],
                      "Width": g[1] - g[0] + 1} for g in dz["gaps"][:50]])
                st.dataframe(gap_df, use_container_width=True, hide_index=True)
                st.caption("Integer ranges with no verse in the loaded corpus. "
                           "Values near wide gaps are statistically rarer.")

        st.divider()
        with st.expander("Cross-method half-verse balance — corpus overview",
                         expanded=False):
            import plotly.express as _px_xm

            @st.cache_data(show_spinner="Computing cross-method balance matrix…")
            def _xm_balance_matrix(_conn):
                cols = ", ".join(
                    f'SUM(CASE WHEN ABS(u1.{mx} - u2.{my}) <= 1 THEN 1 ELSE 0 END) '
                    f'AS "{mx}_vs_{my}"'
                    for mx in CIPHER_NAMES for my in CIPHER_NAMES
                )
                sql = (
                    f"SELECT COUNT(*) AS total_verses, {cols} "
                    "FROM units u1 JOIN units u2 "
                    "ON u1.book=u2.book AND u1.chapter=u2.chapter "
                    "AND u1.verse=u2.verse "
                    "WHERE u1.boundary_type='FirstHalf' "
                    "AND u2.boundary_type='SecondHalf' "
                    "AND u1.variant_track='Ksiv' AND u2.variant_track='Ksiv'"
                )
                row = pd.read_sql_query(sql, _conn).iloc[0]
                total = int(row["total_verses"]) or 1
                data = [[int(row[f"{mx}_vs_{my}"]) / total for my in CIPHER_NAMES]
                        for mx in CIPHER_NAMES]
                return pd.DataFrame(data, index=CIPHER_NAMES, columns=CIPHER_NAMES), total

            rate_df, total_verses = _xm_balance_matrix(conn)
            fig_xm = _px_xm.imshow(
                rate_df, text_auto=".1%",
                color_continuous_scale="YlOrRd", aspect="auto",
                title="Cross-method half-verse balance rate (Colel ±1)",
                labels=dict(x="Second half — method",
                            y="First half — method", color="Rate"),
            )
            fig_xm.update_layout(height=560, margin=dict(t=50, b=30, l=120, r=20))
            st.plotly_chart(fig_xm, use_container_width=True,
                            config={"scrollZoom": False})
            st.caption(
                f"Based on {total_verses:,} Tanach verses (Ksiv track) that have both "
                "half-verse units. Each cell = fraction of those verses where the first "
                "half's [row method] value equals the second half's [column method] "
                "value (±1). Diagonal = standard Internal Balance."
            )



# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        run_selftest()
    elif len(sys.argv) > 1 and sys.argv[1] == "builddb":
        # Pre-build tanach.db so Docker cold starts skip cipher computation.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print("Building tanach.db …")
        _verses = load_from_jsonl()
        if not _verses:
            _verses = list(SAMPLE_CORPUS)
            print("  WARNING: tanach_corpus.jsonl not found — building from SAMPLE_CORPUS only")
        _verses = apply_textual_variants(_verses)
        _conn = build_database(_verses)
        _disk = sqlite3.connect(str(PREBUILT_DB))
        _conn.backup(_disk)
        _disk.close()
        _conn.close()
        print(f"tanach.db written ({len(_verses):,} verses, {PREBUILT_DB.stat().st_size // 1024:,} KB)")
    else:
        run_app()
