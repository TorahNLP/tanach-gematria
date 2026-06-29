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

# Reverse ordinal values (Mispar HaAchor): Tav=1, Shin=2 … Alef=22.
REVERSE_ORDINAL: Dict[str, int] = {ALEFBET[i]: 22 - i for i in range(22)}

# Cumulative / prefix-sum values (Mispar Kidmi a.k.a. HaKadmon):
# each letter = sum of Standard values of every letter from א up to and including it.
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

# Reverse Avgad (אבג"ד הפוך): −1 cyclic shift (Bet→Alef, … Alef→Tav).
REVERSE_AVGAD_MAP: Dict[str, str] = {ALEFBET[i]: ALEFBET[(i - 1) % 22] for i in range(22)}

# Achas Beta (אח"ס בט"ע): cyclic rotation across three groups of 7/7/7, with ת fixed.
# Groups: א-ז (7), ח-נ (7), ס-ש (7). Tav stands outside and maps to itself.
_AB_G1, _AB_G2, _AB_G3 = "אבגדהוז", "חטיכלמנ", "סעפצקרש"
ACHAS_BETA_MAP: Dict[str, str] = {}
for _i in range(7):
    ACHAS_BETA_MAP[_AB_G1[_i]] = _AB_G2[_i]   # g1 → g2
    ACHAS_BETA_MAP[_AB_G2[_i]] = _AB_G3[_i]   # g2 → g3
    ACHAS_BETA_MAP[_AB_G3[_i]] = _AB_G1[_i]   # g3 → g1
ACHAS_BETA_MAP["ת"] = "ת"                       # ת unchanged

# Ayak Bachar (אי"ק בכ"ר): 3×9 cyclic rotation across units/tens/hundreds triplets.
# Each column (א,י,ק), (ב,כ,ר) … (ט,צ,ץ) rotates: units→tens→hundreds→units.
# Finals ך,ם,ן,ף,ץ serve as the 500-900 hundreds tier; after substitution their
# value is read as Standard (base letter), giving the 140-form result.
_AYAK_TRIPLETS = [
    ("א","י","ק"), ("ב","כ","ר"), ("ג","ל","ש"), ("ד","מ","ת"),
    ("ה","נ","ך"), ("ו","ס","ם"), ("ז","ע","ן"), ("ח","פ","ף"), ("ט","צ","ץ"),
]
AYAK_MAP: Dict[str, str] = {}
for _a, _b, _c in _AYAK_TRIPLETS:
    AYAK_MAP[_a] = _b; AYAK_MAP[_b] = _c; AYAK_MAP[_c] = _a

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
# Emtzaiyot (middle/inner): the second letter (index 1) of each Milui name spelling.
# Uses 2-letter (standard Lurianic) spellings: כ=כף, מ=מם.
# For those 2-letter names the inner and terminal letters are the same.
EMTZAIYOT_VALS: Dict[str, int] = {
    k: STANDARD.get(FINAL_TO_BASE.get(v[1], v[1]), 0)
    for k, v in LETTER_NAME_SPELLING.items()
}

# Maleh (מלא — full/complete) 3-letter spelling tradition.
# The two letters with 2-letter names gain an Alef: כ=כאף, מ=מאם.
# For Emtzaiyot (index 1), this changes כ and מ from their final-letter value to א=1.
LETTER_NAME_SPELLING_3: Dict[str, str] = {**LETTER_NAME_SPELLING, "כ": "כאף", "מ": "מאם"}
MILUI_MALEH_VALS: Dict[str, int]     = {k: _spelling_val(v)     for k, v in LETTER_NAME_SPELLING_3.items()}
NEELAM_MALEH_VALS: Dict[str, int]    = {k: _spelling_val(v[1:]) for k, v in LETTER_NAME_SPELLING_3.items()}
EMTZAIYOT_MALEH_VALS: Dict[str, int] = {
    k: STANDARD.get(FINAL_TO_BASE.get(v[1], v[1]), 0)
    for k, v in LETTER_NAME_SPELLING_3.items()
}


# Ofanim (אופנים): last letter of each letter's Lurianic name spelling, standard value.
OFANIM_MAP: Dict[str, str] = {
    k: FINAL_TO_BASE.get(v[-1], v[-1]) for k, v in LETTER_NAME_SPELLING.items()
}

# Nikud (vowel-mark) geometric values for Mispar HaNekudot.
# Rule: each dot = 10, each line (stroke) = 6.
# Dagesh/Shuruk included (one dot inside the letter = 10).
# Shin/sin dot, meteg, and all taamim excluded (consonantal or accentual, not vowel marks).
NIKUD_VALS: Dict[str, int] = {
    "ְ": 20,  # Sheva — two dots (10+10)
    "ֱ": 30,  # Hataf Segol — three dots (same geometry as Segol: 10+10+10)
    "ֲ":  6,  # Hataf Patah — one line (same geometry as Patah)
    "ֳ": 16,  # Hataf Kamatz — one line + one dot (same geometry as Kamatz: 6+10)
    "ִ": 10,  # Hiriq — one dot
    "ֵ": 20,  # Tsere — two dots (10+10)
    "ֶ": 30,  # Segol — three dots (10+10+10)
    "ַ":  6,  # Patah — one line
    "ָ": 16,  # Kamatz — one line + one dot (6+10)
    "ֹ": 10,  # Holam — one dot above
    "ֺ": 10,  # Holam haser for vav — one dot
    "ֻ": 30,  # Kubutz — three diagonal dots (10+10+10)
    "ּ": 10,  # Dagesh / Shuruk — one dot inside the letter
}

# Gikatilla (Ginnat Egoz, 13th c.): Standard gematria of the Hebrew NAME of each vowel mark.
# Used by Mispar Milui HaNekudot. Hataf forms use the same name as their base vowel.
NEKUDA_NAME_VALS: Dict[str, int] = {
    # Spellings follow Gikatilla, Ginnat Egoz (1274): שבא (not שוא), צרי (not צירי)
    "ְ": _spelling_val("שבא"),    # שבא   = 303  (Gikatilla's spelling of Sheva)
    "ִ": _spelling_val("חיריק"),  # חיריק = 328
    "ֵ": _spelling_val("צרי"),    # צרי   = 300  (Gikatilla's spelling of Tsere)
    "ֶ": _spelling_val("סגול"),   # סגול  = 99
    "ַ": _spelling_val("פתח"),    # פתח   = 488
    "ָ": _spelling_val("קמץ"),    # קמץ   = 230  (variant קומץ=236 not used here)
    "ֹ": _spelling_val("חולם"),   # חולם  = 84
    "ֺ": _spelling_val("חולם"),   # Holam haser = same name
    "ֻ": _spelling_val("קובוץ"),  # קובוץ = 204
    "ּ": _spelling_val("דגש"),    # דגש   = 307
    "ֱ": _spelling_val("סגול"),   # Hataf Segol  → same name as Segol
    "ֲ": _spelling_val("פתח"),    # Hataf Patah  → same name as Patah
    "ֳ": _spelling_val("קמץ"),    # Hataf Kamatz → same name as Kamatz
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
    """Mispar Kidmi (HaKadmon) - cumulative sum of Standard values up to each letter."""
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


def g_hanekudot(text: str) -> int:
    """HaNekudot — geometric value of vowel marks (dot=10, line=6).
    Operates on raw cantillated text; dagesh included; taamim excluded.
    Returns 0 for consonant-only text.
    """
    return sum(NIKUD_VALS.get(ch, 0) for ch in text)


def g_milui_nekudot(text: str) -> int:
    """Milui HaNekudot (Gikatilla, Ginnat Egoz 13th c.) — sum of the Standard
    gematria of the Hebrew NAME of each vowel mark found in the text.
    Returns 0 for consonant-only text.
    """
    return sum(NEKUDA_NAME_VALS.get(ch, 0) for ch in text)


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


def g_emtzaiyot(s: str) -> int:
    """Emtzaiyot (אמצעיות — Middle) - Standard value of the second letter of each Milui name (2-letter spellings)."""
    return sum(EMTZAIYOT_VALS.get(_normalize_final(c), 0) for c in s)


def g_milui_maleh(s: str) -> int:
    """Milui Maleh (מילוי מלא) - Milui using 3-letter Maleh spellings: כ=כאף, מ=מאם."""
    return sum(MILUI_MALEH_VALS.get(_normalize_final(c), 0) for c in s)


def g_neelam_maleh(s: str) -> int:
    """Neelam Maleh (נעלם מלא) - Neelam using 3-letter Maleh spellings: כ=כאף, מ=מאם."""
    return sum(NEELAM_MALEH_VALS.get(_normalize_final(c), 0) for c in s)


def g_emtzaiyot_maleh(s: str) -> int:
    """Emtzaiyot Maleh (אמצעיות מלא) - Middle letter using 3-letter Maleh spellings; כ and מ yield א=1."""
    return sum(EMTZAIYOT_MALEH_VALS.get(_normalize_final(c), 0) for c in s)


def g_boneeh(s: str) -> int:
    """Mispar Bone'eh (Building) - running prefix sums, reset at each word boundary."""
    total = 0
    for word in s.split():
        running = 0
        for c in word:
            running += STANDARD.get(_normalize_final(c), 0)
            total += running
    return total


def g_haachor(s: str) -> int:
    """Mispar HaAchor - each letter × its ordinal position within its word; resets per word.

    Per Pardes Rimonim (Sha'ar 30, Ch. 8): called 'Achor' because the positional
    weighting mirrors back-stacking of textual formula layouts.
    """
    total = 0
    for word in s.split():
        pos = 0
        for c in word:
            v = STANDARD.get(_normalize_final(c), 0)
            if v:
                pos += 1
                total += pos * v
    return total


def g_mityashev(s: str) -> int:
    """Mispar Mityashev - each letter × letter count of its word; N resets per word."""
    total = 0
    for word in s.split():
        vals = [STANDARD.get(_normalize_final(c), 0) for c in word
                if STANDARD.get(_normalize_final(c), 0)]
        n = len(vals)
        total += sum(v * n for v in vals)
    return total


def g_kolel_ehad(s: str) -> int:
    """Mispar Kolel (Word) - Standard total + 1 (the word counted as a single unit)."""
    return g_absolute(s) + 1


def g_kolel_otiyot(s: str) -> int:
    """Mispar Kolel (Letters) - Standard total + count of letters in the unit."""
    n = sum(1 for c in s if STANDARD.get(_normalize_final(c), 0))
    return g_absolute(s) + n


def g_reverse_ordinal(s: str) -> int:
    """Mispar Achor (Reverse Ordinal) - Tav=1, Shin=2 … Alef=22.

    Chassidei Ashkenaz / Sefer Raziel HaMalach definition: reverse alphabetical index.
    """
    return sum(REVERSE_ORDINAL.get(_normalize_final(c), 0) for c in s)


def g_ha_merubah_klali(s: str) -> int:
    """Mispar HaMerubah HaKlali - the total Standard sum squared as a single block."""
    return g_absolute(s) ** 2


def g_ayak_bachar(s: str) -> int:
    """Ayak Bachar (אי"ק בכ"ר) - 3×9 cyclic rotation: units→tens→hundreds→units.

    Input finals are normalised to base before lookup. The substituted letter
    for the hundreds tier is a final form (ך ם ן ף ץ) valued at 500–900 via
    GADOL_FINALS; other substituted letters use STANDARD.
    Source: Tikunei HaZohar (Tikkun 21).
    """
    total = 0
    for c in s:
        base = _normalize_final(c)
        subst = AYAK_MAP.get(base, base)
        total += GADOL_FINALS.get(subst, STANDARD.get(subst, 0))
    return total


def g_ofanim(s: str) -> int:
    """Ofanim (אופנים / Wheels) - replace each letter with last letter of its Milui name.

    Source: Sefer Raziel HaMalach (angelological cipher).
    """
    return sum(STANDARD.get(OFANIM_MAP.get(_normalize_final(c), _normalize_final(c)), 0)
               for c in s)


def g_achas_beta(s: str) -> int:
    """Achas Beta (אח"ס בט"ע) - 7/7/7 cyclic rotation; ת is invariant.

    Source: Pardes Rimonim (R. Moshe Cordovero), Sha'ar 30.
    """
    return _temurah_value(s, ACHAS_BETA_MAP)


def g_reverse_avgad(s: str) -> int:
    """Reverse Avgad (אבג"ד הפוך) - −1 cyclic shift: Bet→Alef … Alef→Tav.

    Source: R. Eliezer Ashkenazi, Ta'am Zekenim.
    """
    return _temurah_value(s, REVERSE_AVGAD_MAP)


# Ordered registry of every cipher. The order here is the column order used
# throughout the database and the UI.
# NOTE: Nikud ciphers (HaNekudot, ImHaNekudot, MiluiNekudot, ImMiluiNekudot)
# operate on cantillated text; compute_all_ciphers handles dispatch.
CIPHERS: Dict[str, Callable[[str], int]] = {
    # ── Standard value ciphers ────────────────────────────────────────────────
    "Standard":         g_absolute,          # Mispar Hechrachi / Yaschar
    "Katan":            g_katan,             # Mispar Katan (reduced, drop zeros)
    "Gadol":            g_gadol,             # Mispar Gadol (finals 500-900)
    "KatanMispari":     g_katan_mispari,     # Mispar Katan Mispari (digital root)
    # ── Ordinal / positional ciphers ─────────────────────────────────────────
    "Siduri":           g_siduri,            # Mispar Siduri (ordinal 1-22)
    "ReverseOrdinal":   g_reverse_ordinal,   # Reverse ordinal: Tav=1 … Alef=22
    # ── Mathematical transforms ───────────────────────────────────────────────
    "Ribua":            g_ribua,             # Mispar Meruba Prati (Σ v²)
    "HaMerubahKlali":   g_ha_merubah_klali,  # Mispar HaMerubah HaKlali (total²)
    "Kidmi":            g_kidmi,             # Mispar Kidmi / HaKadmon (cumulative Standard sums)
    # ── Name-expansion (2-letter / standard Lurianic) ────────────────────────
    "Milui":            g_milui,             # Mispar Milui (full letter-name)
    "Neelam":           g_neelam,            # Mispar Neelam (hidden portion)
    "Emtzaiyot":        g_emtzaiyot,         # Emtzaiyot (middle/inner letter of name)
    "Ofanim":           g_ofanim,            # Ofanim (last letter of name)
    # ── Vowel-mark (nikud) ciphers — dispatched to cantillated text ──────────
    "HaNekudot":        g_hanekudot,         # Geometric vowel values (dot=10, line=6)
    "ImHaNekudot":      g_hanekudot,         # Standard(letters) + geometric vowel values
    "MiluiNekudot":     g_milui_nekudot,     # Gematria of Hebrew names of vowel marks
    "ImMiluiNekudot":   g_milui_nekudot,     # Standard(letters) + vowel-mark name values
    # ── Name-expansion (3-letter / Maleh tradition: כ=כאף, מ=מאם) ───────────
    "MiluiMaleh":       g_milui_maleh,       # Milui with Maleh spellings
    "NeelAmMaleh":      g_neelam_maleh,      # Neelam with Maleh spellings
    "EmtzaiyotMaleh":   g_emtzaiyot_maleh,   # Emtzaiyot with Maleh spellings (כ,מ → א=1)
    # ── Temurah / substitution ciphers ───────────────────────────────────────
    "Atbash":          g_atbash,            # א"ת ב"ש mirror swap
    "Albam":           g_albam,             # א"ל ב"ם ROT-11
    "Achbi":           g_achbi,             # א"כ ב"י reversed-half swap
    "Atbach":          g_atbah,             # א"ט ב"ח sum-to-10/100/1000
    "Avgad":           g_avgad,             # א"ב ג"ד +1 shift
    "Agdat":           g_agdat,             # אגד"ת +2 shift
    "ReverseAvgad":    g_reverse_avgad,     # Reverse Avgad −1 shift
    "AyakBachar":      g_ayak_bachar,       # אי"ק בכ"ר 3×9 cyclic rotation
    "AchasBeta":       g_achas_beta,        # אח"ס בט"ע 7/7/7 rotation (ת fixed)
    # ── Word-structure ciphers ────────────────────────────────────────────────
    "Boneeh":          g_boneeh,            # Mispar Bone'eh (building / prefix sums)
    "HaAchor":         g_haachor,           # Mispar HaAchor (value × position in word)
    "Mityashev":       g_mityashev,         # Mispar Mityashev (value × word letter count)
    # ── Kolel / additive ciphers ─────────────────────────────────────────────
    "KololEhad":       g_kolel_ehad,        # Kolel +1 (word as single unit)
    "KololOtiyot":     g_kolel_otiyot,      # Kolel +N (letter count)
}
CIPHER_NAMES: List[str] = list(CIPHERS.keys())

# Ciphers excluded from correlation/balance heatmaps: KatanMispari saturates
# (only 9 distinct values → always ~100% balance), HaMerubahKlali produces
# hyperscale squared totals that break Pearson correlation and always show 0% balance.
_HEATMAP_EXCLUDE: frozenset = frozenset({"KatanMispari", "HaMerubahKlali"})

# Display labels for cipher selector widgets. Internal names stay as short
# CIPHER_NAMES keys (Python dicts, SQL columns); these labels are used only
# in interactive selectors via format_func, never as DB column names.
CIPHER_DISPLAY_NAMES: Dict[str, str] = {
    "Standard":        "Standard — מספר הכרחי",
    "Katan":           "Katan — מספר קטן",
    "Gadol":           "Gadol — מספר גדול",
    "KatanMispari":    "Katan Mispari — קטן מספרי",
    "Siduri":          "Siduri — מספר סידורי",
    "ReverseOrdinal":  "Reverse Ordinal — מספר אחור סידורי",
    "Ribua":           "Ribua — מספר מרובע",
    "HaMerubahKlali":  "HaMerubah HaKlali — מספר המרובע הכללי",
    "Kidmi":           "Kidmi — מספר קדמי",
    "Milui":           "Milui — מספר שמי / מילוי",
    "Neelam":          "Neelam — מספר נעלם",
    "Emtzaiyot":       "Emtzaiyot — אמצעיות",
    "Ofanim":          "Ofanim — אופנים",
    "HaNekudot":        "HaNekudot — מספר הנקודות",
    "ImHaNekudot":      "Im HaNekudot — עם הנקודות",
    "MiluiNekudot":     "Milui HaNekudot — מילוי הנקודות",
    "ImMiluiNekudot":   "Im Milui HaNekudot — עם מילוי הנקודות",
    "MiluiMaleh":       "Milui Maleh — מילוי מלא",
    "NeelAmMaleh":     "Neelam Maleh — נעלם מלא",
    "EmtzaiyotMaleh":  "Emtzaiyot Maleh — אמצעיות מלא",
    "Atbash":          "Atbash — אתב\"ש",
    "Albam":           "Albam — אלב\"ם",
    "Achbi":           "Achbi — אכב\"י",
    "Atbach":          "Atbach — אטב\"ח",
    "Avgad":           "Avgad — אבג\"ד",
    "Agdat":           "Agdat — אגד\"ת",
    "ReverseAvgad":    "Reverse Avgad — אבג\"ד הפוך",
    "AyakBachar":      "Ayak Bachar — אי\"ק בכ\"ר",
    "AchasBeta":       "Achas Beta — אח\"ס בט\"ע",
    "Boneeh":          "Bone'eh — מספר בונה",
    "HaAchor":         "HaAchor — מספר האחור",
    "Mityashev":       "Mityashev — מספר מיושב",
    "KololEhad":       "Kolel (Word) — כולל",
    "KololOtiyot":     "Kolel (Letters) — כולל אותיות",
}

# Human-readable one-liners shown next to each cipher selector in the UI.
CIPHER_BLURB: Dict[str, str] = {
    "Standard":        "Standard values — א=1, ב=2 … י=10, כ=20 … ת=400. Summed.",
    "Katan":           "Reduced values — drop trailing zeros (ק→1, מ→4), then sum.",
    "Gadol":           "Like Standard but finals count higher: ך=500 … ץ=900.",
    "Siduri":          "Ordinal position: א=1, ב=2 … ת=22. Sequence, not Standard value.",
    "ReverseOrdinal":  "Reverse ordinal: ת=1, ש=2 … א=22. Chassidei Ashkenaz / Sefer Raziel.",
    "Ribua":           "Sum of squared values per letter: Σ v².",
    "HaMerubahKlali":  "The total Standard sum squared as one block: (Σv)². Pardes Rimonim Sha'ar 30.",
    "Kidmi":           "Cumulative sum of Standard values: each letter = Σ Standard values from א up to it. א=1, ב=3, ג=6 … ת=1495.",
    "KatanMispari":    "Sum all Standard values first; then reduce to a single digital root.",
    "Milui":           "Spell each letter's full name (Lurianic: א=אלף=111 …); sum all spelling letters.",
    "Neelam":          "Like Milui but drop the first letter of each name — only the hidden remainder.",
    "Emtzaiyot":       "Middle letter: Standard value of the second letter of each Milui name (2-letter spellings). אלף→ל=30, בית→י=10 …",
    "Ofanim":          "Replace each letter with the last letter of its Milui name, take Standard value.",
    "HaNekudot":        "Geometric value of each vowel mark: each dot=10, each line=6. Dagesh=10. Sheva=20, Kamatz=16, Patah=6, Tsere=20, Segol=30, Hiriq=10, Holam=10, Kubutz=30. Consonants and taamim contribute 0.",
    "ImHaNekudot":      "Standard gematria of the consonants plus HaNekudot of the vowel marks: letters + vowel-mark geometric values combined.",
    "MiluiNekudot":     "Standard gematria of the Hebrew NAME of each vowel mark (Gikatilla spellings). שבא=303, חיריק=328, צרי=300, סגול=99, פתח=488, קמץ=230, חולם=84, קובוץ=204, דגש=307. Returns 0 for consonant-only text.",
    "ImMiluiNekudot":   "Standard gematria of the consonants plus Milui HaNekudot (vowel-mark name values). Combines letter totals with the spelled-out vowel marks.",
    "MiluiMaleh":       "Milui using Maleh (מלא) 3-letter spellings: כ=כאף=101, מ=מאם=81. Other letters unchanged.",
    "NeelAmMaleh":      "Neelam using Maleh 3-letter spellings: כ→אף=81, מ→אם=41. Other letters unchanged.",
    "EmtzaiyotMaleh":   "Middle letter using Maleh 3-letter spellings. כ and מ both yield א=1 as their inner letter.",
    "Atbash":          "Mirror swap: א↔ת, ב↔ש … then Standard values.",
    "Albam":           "ROT-11 swap: א↔ל, ב↔מ … then Standard values.",
    "Achbi":           "Reverse each half of the alphabet: א↔כ, ב↔י … ל↔ת, מ↔ש … Then Standard.",
    "Atbach":          "Pairs summing to 10/100/1000: א↔ט, ב↔ח … ק↔ץ. Finals carry 600–900.",
    "Avgad":           "+1 cyclic shift: א→ב … ת→א. Then Standard values.",
    "Agdat":           "+2 cyclic shift: א→ג … ת→ב. Then Standard values.",
    "ReverseAvgad":    "−1 cyclic shift: ב→א … א→ת. Then Standard values. R. Eliezer Ashkenazi.",
    "AyakBachar":      "3×9 cyclic rotation: units→tens→hundreds→units (א↔י↔ק, ב↔כ↔ר …). Tikunei HaZohar 21.",
    "AchasBeta":       "7/7/7 cyclic rotation across groups א-ז / ח-נ / ס-ש; ת is invariant. Pardes Rimonim.",
    "Boneeh":          "Building value: stacked prefix sums per word (ח=8, ח+ב=10, ח+ב+ד=14 → 32). Resets per word.",
    "HaAchor":         "Each Standard value × its ordinal position within the word; position resets per word. Pardes Rimonim Sha'ar 30.",
    "Mityashev":       "Each Standard value × total letter count of its word: Σ(vᵢ × N). N resets per word.",
    "KololEhad":       "Standard total + 1 (the word counted as one collective unit).",
    "KololOtiyot":     "Standard total + number of letters in the unit (one per letter). Also called Mispar Musafi.",
}

# Friendly display labels for variant tracks and boundary types in the UI.
TRACK_LABELS: Dict[str, str] = {
    "Ksiv":        "Written (כְּתִיב)",
    "Kri":         "Read (קְרֵי)",
    "TextVariant": "Textual variant",
    "Aggregate":   "Chapter / Parsha total",
}
BOUNDARY_LABELS: Dict[str, str] = {
    "Word":          "Word (תיבה)",
    "ZakefPhrase":   "Zakef phrase (זָקֵף — finest cantillation unit)",
    "TiphchaPhrase": "Tipcha phrase (טִפְחָא — sub-half phrase unit)",
    "FirstHalf":     "First half-verse (before Asnachta)",
    "SecondHalf":    "Second half-verse (after Asnachta)",
    "Verse":         "Verse (פסוק)",
    "Perek":         "Chapter (פרק)",
    "Parsha":     "Torah portion (פרשה)",
    "Petucha":    "Open paragraph (Pesucha פ)",
    "Setuma":     "Closed paragraph (Setuma ס)",
}


def compute_all_ciphers(consonants: str, cantillated: str = "",
                        word_consonants: str = "") -> Dict[str, int]:
    """Return {cipher_name: value} for a cleaned consonant string.

    Nikud ciphers are dispatched to `cantillated` (raw vocalised text).
    Im* variants add Standard(consonants) to the nikud total.
    HaAchor, Mityashev, Boneeh are dispatched to `word_consonants` so they
    reset counters at each word boundary; falls back to `consonants` when empty.
    """
    word_src = word_consonants if word_consonants else consonants
    std_val = g_absolute(consonants)
    result = {}
    for name, fn in CIPHERS.items():
        if name in ("HaNekudot", "MiluiNekudot"):
            result[name] = fn(cantillated)
        elif name == "ImHaNekudot":
            result[name] = std_val + g_hanekudot(cantillated)
        elif name == "ImMiluiNekudot":
            result[name] = std_val + g_milui_nekudot(cantillated)
        elif name in ("HaAchor", "Mityashev", "Boneeh"):
            result[name] = fn(word_src)
        else:
            result[name] = fn(consonants)
    return result


# ---------------------------------------------------------------------------
# SECTION 2.  TEXT CLEANING & STRUCTURAL PARSING
# ---------------------------------------------------------------------------

ATNACH      = "\u0591"     # HEBREW ACCENT ETNAHTA \u2014 major half-verse pause
TIPCHA      = "\u0596"     # HEBREW ACCENT TIPEHA \u2014 sub-unit before ATNACH or SILLUQ
ZAKEF_KATON = "\u0594"     # HEBREW ACCENT ZAQEF QATAN \u2014 second-tier disjunctive
MAQAF       = "\u05BE"     # HEBREW PUNCTUATION MAQAF (word joiner)
SOF_PASUQ   = "\u05C3"     # HEBREW PUNCTUATION SOF PASUQ

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


def split_halves_word_cons(text: str) -> Tuple[str, str]:
    """Like split_halves_by_atnach but returns space-separated word consonants for each half.

    Used to feed word-boundary-aware ciphers (HaAchor, Mityashev, Boneeh) the
    correct word structure for half-verse units.
    """
    idx = text.find(ATNACH)
    if idx == -1:
        return " ".join(tokenize_words(text)), ""
    end = idx
    while end < len(text) and text[end] not in (" ", "\t", MAQAF, SOF_PASUQ):
        end += 1
    return " ".join(tokenize_words(text[:end])), " ".join(tokenize_words(text[end:]))


def split_halves_cantillated(text: str) -> Tuple[str, str]:
    """Split at ATNACH; return the raw cantillated halves (not stripped).
    Used to thread vowel data to FirstHalf/SecondHalf rows.
    """
    idx = text.find(ATNACH)
    if idx == -1:
        return text, ""
    end = idx
    while end < len(text) and text[end] not in (" ", "\t", MAQAF, SOF_PASUQ):
        end += 1
    return text[:end], text[end:]


def _tokenize_raw_words(text: str) -> List[str]:
    """Split cantillated text into raw word tokens (nikud/taamim preserved).
    Strips paragraph markers; splits on whitespace and maqaf.
    Drops letter-less tokens (paseq ׀, sof-pasuq, etc.) so the result stays
    index-aligned with tokenize_words(), which also drops them after stripping.
    """
    no_markers = _MARKER_STRIP_RE.sub(" ", text)
    toks = re.split(r"[\s" + re.escape(MAQAF) + r"]+", no_markers)
    return [t for t in toks if strip_to_consonants(t)]


def _accent_phrases(cantillated: str, split_on: str) -> List[Tuple[str, str, str]]:
    """Split cantillated verse text into phrase segments at words bearing any
    character from `split_on`.  Each phrase ends with (and includes) the first
    word carrying one of those accent marks; the final remainder is also returned.

    Returns list of (consonants, word_cons, cantillated) tuples — one per phrase.
    Empty / punctuation-only segments are suppressed.
    """
    raw = re.split(r"[\s" + re.escape(MAQAF) + r"]+", cantillated.strip())
    result: List[Tuple[str, str, str]] = []
    current: List[str] = []
    for tok in raw:
        if not tok:
            continue
        current.append(tok)
        if any(a in tok for a in split_on):
            ph = " ".join(current)
            c = strip_to_consonants(ph)
            if c:
                result.append((c, " ".join(tokenize_words(ph)), ph))
            current = []
    if current:
        ph = " ".join(current)
        c = strip_to_consonants(ph)
        if c:
            result.append((c, " ".join(tokenize_words(ph)), ph))
    return result


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
    cantillated_text: str = ""  # full cantillated verse (for nikud ciphers)


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


def _cipher_tuple(consonants: str, cantillated: str = "",
                  word_consonants: str = "") -> Tuple[int, ...]:
    vals = compute_all_ciphers(consonants, cantillated, word_consonants)
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
               disp=None, cantillated="", word_cons=""):
        if not cons:
            return
        cur.execute(
            f"""INSERT INTO units
                (sub_id, book, chapter, verse, parsha, boundary_type,
                 variant_track, consonants, text_display, {CIPHER_INSERT_COLS})
                VALUES (?,?,?,?,?,?,?,?,?,{CIPHER_PLACEHOLDERS})""",
            (sub_id, book, chapter, verse, parsha, boundary, track, cons,
             disp or cons, *_cipher_tuple(cons, cantillated, word_cons)),
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
        verse_wc = " ".join(f.words)
        fh_wc, sh_wc = split_halves_word_cons(f.cantillated_text)
        fh_cant, sh_cant = split_halves_cantillated(f.cantillated_text)
        raw_cant_words = _tokenize_raw_words(f.cantillated_text)
        insert(f.sub_id, f.book, f.chapter, f.verse, f.parsha,
               "Verse", f.variant_track, f.full_consonants,
               cantillated=f.cantillated_text, word_cons=verse_wc)
        insert(f"{f.sub_id}_FH", f.book, f.chapter, f.verse, f.parsha,
               "FirstHalf", f.variant_track, f.first_half,
               cantillated=fh_cant, word_cons=fh_wc)
        insert(f"{f.sub_id}_SH", f.book, f.chapter, f.verse, f.parsha,
               "SecondHalf", f.variant_track, f.second_half,
               cantillated=sh_cant, word_cons=sh_wc)
        # TiphchaPhrase: split at TIPCHA and ATNACH — natural sub-half phrase units
        for pi, (ph_c, ph_wc, ph_cant) in enumerate(
                _accent_phrases(f.cantillated_text, TIPCHA + ATNACH), 1):
            insert(f"{f.sub_id}_TP{pi}", f.book, f.chapter, f.verse, f.parsha,
                   "TiphchaPhrase", f.variant_track, ph_c,
                   cantillated=ph_cant, word_cons=ph_wc)
        # ZakefPhrase: split at ZAKEF_KATON, TIPCHA and ATNACH — finest phrase unit
        for pi, (ph_c, ph_wc, ph_cant) in enumerate(
                _accent_phrases(f.cantillated_text, ZAKEF_KATON + TIPCHA + ATNACH), 1):
            insert(f"{f.sub_id}_ZK{pi}", f.book, f.chapter, f.verse, f.parsha,
                   "ZakefPhrase", f.variant_track, ph_c,
                   cantillated=ph_cant, word_cons=ph_wc)
        for wi, w in enumerate(f.words, start=1):
            cw = raw_cant_words[wi - 1] if wi - 1 < len(raw_cant_words) else ""
            insert(f"{f.sub_id}_W{wi}", f.book, f.chapter, f.verse, f.parsha,
                   "Word", f.variant_track, w, cantillated=cw)
        if f.paragraph_marker:
            insert(f"{f.sub_id}_{f.paragraph_marker}", f.book, f.chapter,
                   f.verse, f.parsha, f.paragraph_marker, f.variant_track,
                   f.full_consonants, cantillated=f.cantillated_text, word_cons=verse_wc)

    # ---- Macro structures: Perek, Parsha (Ksiv track aggregation) ----
    ksiv = [f for f in all_forks if f.variant_track == "Ksiv"]

    def aggregate(group_key_fn, boundary_name, id_fn):
        buckets: Dict[Tuple, List[VerseFork]] = {}
        for f in ksiv:
            buckets.setdefault(group_key_fn(f), []).append(f)
        for key, members in buckets.items():
            members.sort(key=lambda m: (m.chapter, m.verse))
            cons = "".join(m.full_consonants for m in members)
            word_cons_agg = " ".join(w for m in members for w in m.words)
            sample = members[0]
            insert(id_fn(key, sample), sample.book,
                   sample.chapter if boundary_name == "Perek" else 0,
                   0, sample.parsha, boundary_name, "Aggregate", cons,
                   word_cons=word_cons_agg)

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
            word_cons_block = " ".join(w for m in block for w in m.words)
            insert(f"BLOCK_{f.paragraph_marker}_{block_n}", block[0].book,
                   block[0].chapter, block[0].verse, block[0].parsha,
                   f.paragraph_marker, "Aggregate", cons, word_cons=word_cons_block)
            block = []
    if block:  # flush verses after the last paragraph marker
        block_n += 1
        cons = "".join(m.full_consonants for m in block)
        word_cons_block = " ".join(w for m in block for w in m.words)
        insert(f"BLOCK_Open_{block_n}", block[0].book,
               block[0].chapter, block[0].verse, block[0].parsha,
               "Open", "Aggregate", cons, word_cons=word_cons_block)

    conn.commit()

    # ---- Indices on every cipher column + boundary/variant/structure keys ----
    cur.execute("CREATE INDEX idx_boundary ON units(boundary_type)")
    cur.execute("CREATE INDEX idx_variant ON units(variant_track)")
    cur.execute("CREATE INDEX idx_bcv ON units(book, chapter, verse)")
    cur.execute("CREATE INDEX idx_parsha ON units(parsha)")
    for c in CIPHER_NAMES:
        cur.execute(f"CREATE INDEX idx_{c} ON units({c})")
    conn.commit()

    return conn


# ---------------------------------------------------------------------------
# SECTION 6.  PATTERN RECOGNITION & ECHO-MATCHING
# ---------------------------------------------------------------------------

# build_pattern_log and the patterns table were removed — Tab 3 rebuilds
# every pattern live via internal_balance_matches / proximity_echo_matches /
# whole_unit_echo_matches, making the prebuilt table dead code.


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
                f"AND u1.{ma} >= ? AND u2.{mb} >= ? "
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
            f"AND u1.{m} >= ? AND ABS(u1.{m} - u2.{m}) <= ? "
            f"ORDER BY u1.book, u1.chapter, u1.verse LIMIT ?",
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
                f"AND u1.{ma} >= ? AND u2.{mb} >= ? "
                "AND u1.rowid != u2.rowid "
                "ORDER BY u1.book, u1.chapter, u1.verse, u2.rowid "
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
                  cantillated: str = "",
                  word_consonants: str = "",
                  colel: bool = False, tracks: Optional[List[str]] = None,
                  boundaries: Optional[List[str]] = None) -> Dict[str, object]:
    """Compute every cipher value for the input phrase and search each one."""
    values = compute_all_ciphers(phrase_consonants, cantillated, word_consonants)
    results = {c: search_value(conn, c, values[c], colel, tracks, boundaries)
               for c in CIPHER_NAMES}
    return {"values": values, "results": results}


def search_value_all_methods(
    conn: sqlite3.Connection, value: int, limit_per_method: int = 50,
    colel: bool = False, tracks: Optional[List[str]] = None,
    boundaries: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Search `value` across all ciphers in a single UNION ALL query.

    Returns a DataFrame with a leading 'Method' column so the caller can see
    which cipher produced each match. Supports Colel (±1), track, and boundary
    filters. When tracks is None, defaults to Ksiv-only (preserves old behavior).
    """
    unions, params = [], []
    for c in CIPHER_NAMES:
        where, branch_params = [], []
        if colel:
            where.append(f"{c} BETWEEN ? AND ?")
            branch_params += [value - 1, value + 1]
        else:
            where.append(f"{c}=?")
            branch_params.append(value)
        if tracks:
            where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
            branch_params += list(tracks)
        else:
            where.append("variant_track='Ksiv'")
        if boundaries:
            where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
            branch_params += list(boundaries)
        unions.append(
            f"SELECT * FROM ("
            f"SELECT '{c}' AS Method, book AS Book, chapter AS Chapter, "
            f"verse AS Verse, boundary_type AS Boundary, variant_track AS Track, "
            f"consonants AS Text, {c} AS Value, sub_id AS SubID "
            f"FROM units WHERE " + " AND ".join(where) +
            f" LIMIT {int(limit_per_method)})"
        )
        params += branch_params
    method_order = (
        "CASE Method " +
        " ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(CIPHER_NAMES)) +
        " ELSE 9999 END"
    )
    sql = ("SELECT * FROM (" + " UNION ALL ".join(unions) +
           f") ORDER BY {method_order}, ABS(Value - ?), Book, Chapter, Verse")
    params += list(CIPHER_NAMES)
    params.append(value)
    return pd.read_sql_query(sql, conn, params=params)


def normalize_query(raw: str) -> str:
    """Clean a Hebrew query string down to its 22-letter consonant skeleton."""
    return strip_to_consonants(raw)


def span_search(
    conn: sqlite3.Connection,
    target: int,
    cipher: str,
    max_span: int = 10,
    colel: bool = False,
    tracks: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Find every contiguous multi-word span (2..max_span words) whose `cipher`
    value equals `target` (or target±1 if colel).  Works for all 34 ciphers
    because every cipher composes additively across words.

    Returns a DataFrame with columns: Book, Ch, Vs, Track, Words, <cipher>.
    """
    import numpy as _np

    track_cond = ""
    params: list = []
    if tracks:
        track_cond = "AND variant_track IN (%s)" % ",".join("?" * len(tracks))
        params = list(tracks)

    sql = (
        f"SELECT book, chapter, verse, variant_track, {cipher} "
        f"FROM units WHERE boundary_type='Word' {track_cond} "
        f"ORDER BY book, chapter, verse, variant_track, rowid"
    )
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return pd.DataFrame()

    target_set = _np.array(
        [target - 1, target, target + 1] if colel else [target], dtype=_np.int64
    )

    rows = []
    for (book, ch, vs, track), grp in df.groupby(
        ["book", "chapter", "verse", "variant_track"], sort=False
    ):
        vals = grp[cipher].to_numpy(dtype=_np.int64)
        n = len(vals)
        if n < 2:
            continue
        prefix = _np.concatenate([[0], _np.cumsum(vals)])
        for span_len in range(2, min(max_span + 1, n + 1)):
            span_vals = prefix[span_len:] - prefix[: n - span_len + 1]
            hits = _np.where(_np.isin(span_vals, target_set))[0]
            for i in hits:
                rows.append({
                    "Book":  book,
                    "Ch":    int(ch),
                    "Vs":    int(vs),
                    "Track": track,
                    "Words": f"{int(i)+1}–{int(i)+span_len}",
                    cipher:  int(span_vals[i]),
                })

    return pd.DataFrame(rows)


def _xm_count_matrix(
    conn: sqlite3.Connection,
    a_vals: Dict[str, int],
    colel: bool,
    tracks: Optional[List[str]],
    boundaries: Optional[List[str]],
) -> pd.DataFrame:
    """Build the N×N cross-method count matrix in a single SQL pass.

    One query with N² CASE WHEN expressions (N = len(CIPHER_NAMES)),
    scanning the units table once instead of issuing N² individual queries.
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
            "Median": round(float(col.median()), 1),
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

def cipher_breakdown(cipher: str, consonants: str,
                     word_consonants: str = "") -> Optional[List[Tuple[str, int]]]:
    """Return [(display_label, letter_value)] for equation display in the UI.

    Returns None for ciphers with no letter-level breakdown (nikud ciphers,
    KatanMispari, HaMerubahKlali, KololEhad, KololOtiyot) or empty input.
    word_consonants (space-separated) drives word-boundary-aware ciphers.
    """
    _NO_BREAKDOWN = {"HaNekudot", "ImHaNekudot", "MiluiNekudot", "ImMiluiNekudot",
                     "KatanMispari", "HaMerubahKlali", "KololEhad", "KololOtiyot"}
    if cipher in _NO_BREAKDOWN or not consonants:
        return None
    result: List[Tuple[str, int]] = []

    # Word-boundary-aware ciphers reset counters at each word boundary.
    if cipher in ("HaAchor", "Mityashev", "Boneeh"):
        src = word_consonants if word_consonants else consonants
        for word in src.split():
            if cipher == "Boneeh":
                running = 0
                for c in word:
                    base = _normalize_final(c)
                    running += STANDARD.get(base, 0)
                    result.append((c, running))
            elif cipher == "HaAchor":
                pos = 0
                for c in word:
                    base = _normalize_final(c)
                    v = STANDARD.get(base, 0)
                    if v:
                        pos += 1
                    result.append((f"{c}×{pos}", v * pos))
            elif cipher == "Mityashev":
                letters = [STANDARD.get(_normalize_final(c), 0) for c in word
                           if STANDARD.get(_normalize_final(c), 0)]
                n = len(letters)
                for c in word:
                    base = _normalize_final(c)
                    v = STANDARD.get(base, 0)
                    if not v:
                        continue
                    result.append((f"{c}×{n}", v * n))
        return result

    # All other ciphers iterate letter by letter over the space-free consonant string.
    for ch in consonants:
        base = _normalize_final(ch)
        v_std = STANDARD.get(base, 0)
        if cipher == "Standard":
            result.append((ch, v_std))
        elif cipher == "Katan":
            result.append((ch, _katan_digit(v_std)))
        elif cipher == "Gadol":
            result.append((ch, GADOL_FINALS.get(ch, v_std)))
        elif cipher == "Siduri":
            result.append((ch, ORDINAL.get(base, 0)))
        elif cipher == "ReverseOrdinal":
            result.append((ch, REVERSE_ORDINAL.get(base, 0)))
        elif cipher == "Ribua":
            result.append((f"{ch}²", v_std * v_std))
        elif cipher == "Kidmi":
            result.append((ch, KIDMI.get(base, 0)))
        elif cipher == "Milui":
            spelling = LETTER_NAME_SPELLING.get(base, "")
            result.append((f"{ch}={spelling}", MILUI_VALS.get(base, 0)))
        elif cipher == "Neelam":
            spelling = LETTER_NAME_SPELLING.get(base, "")
            result.append((f"{ch}→{spelling[1:]}", NEELAM_VALS.get(base, 0)))
        elif cipher == "Emtzaiyot":
            spelling = LETTER_NAME_SPELLING.get(base, base)
            mid_raw = spelling[1] if len(spelling) > 1 else spelling[0]
            mid = FINAL_TO_BASE.get(mid_raw, mid_raw)
            result.append((f"{ch}→{mid}", EMTZAIYOT_VALS.get(base, 0)))
        elif cipher == "MiluiMaleh":
            spelling = LETTER_NAME_SPELLING_3.get(base, base)
            result.append((f"{ch}→{spelling}", MILUI_MALEH_VALS.get(base, 0)))
        elif cipher == "NeelAmMaleh":
            spelling = LETTER_NAME_SPELLING_3.get(base, base)
            result.append((f"{ch}→{spelling[1:]}", NEELAM_MALEH_VALS.get(base, 0)))
        elif cipher == "EmtzaiyotMaleh":
            spelling = LETTER_NAME_SPELLING_3.get(base, base)
            mid_raw = spelling[1] if len(spelling) > 1 else spelling[0]
            mid = FINAL_TO_BASE.get(mid_raw, mid_raw)
            result.append((f"{ch}→{mid}", EMTZAIYOT_MALEH_VALS.get(base, 0)))
        elif cipher == "Ofanim":
            last = OFANIM_MAP.get(base, base)
            result.append((f"{ch}→{last}", STANDARD.get(last, 0)))
        elif cipher == "Atbash":
            sw = ATBASH_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "Albam":
            sw = ALBAM_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "Achbi":
            sw = ACHBI_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "Atbach":
            result.append((f"{ch}↔{ATBAH_MAP.get(base, base)}", ATBAH_VALUE.get(base, 0)))
        elif cipher == "Avgad":
            sw = AVGAD_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "Agdat":
            sw = AGDAT_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "ReverseAvgad":
            sw = REVERSE_AVGAD_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "AyakBachar":
            sw = AYAK_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "AchasBeta":
            sw = ACHAS_BETA_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
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
    # HaNekudot: consonant-only → 0; cantillated בְּרֵאשִׁ֖ית:
    #   dagesh(10) + sheva(20) + tsere(20) + hiriq(10) = 60 (taam excluded)
    assert g_hanekudot("שלום") == 0, g_hanekudot("שלום")
    assert g_hanekudot("בְּרֵאשִׁ֖ית") == 60, g_hanekudot("בְּרֵאשִׁ֖ית")
    assert g_hanekudot(SAMPLE_CORPUS[0].text) > 0
    # MiluiNekudot (Gikatilla spellings): בְּרֵאשִׁ֖ית — dagesh(דגש=307)+sheva(שבא=303)+tsere(צרי=300)+hiriq(חיריק=328) = 1238
    assert g_milui_nekudot("שלום") == 0, g_milui_nekudot("שלום")
    assert g_milui_nekudot("בְּרֵאשִׁ֖ית") == 1238, g_milui_nekudot("בְּרֵאשִׁ֖ית")
    # All ciphers — spot-checks using אמת (א=1, מ=40, ת=400; Standard=441)
    emet = "אמת"
    assert g_agdat(emet) == 65,               g_agdat(emet)            # א→ג(3)+מ→ס(60)+ת→ב(2)
    assert g_katan_mispari(emet) == 9,        g_katan_mispari(emet)    # 441→4+4+1=9
    assert g_milui(emet) == 607,              g_milui(emet)            # אלף(111)+מם(80)+תיו(416)
    assert g_neelam(emet) == 166,             g_neelam(emet)           # לף(110)+ם(40)+יו(16)
    assert g_kolel_ehad(emet) == 442,         g_kolel_ehad(emet)       # 441+1
    assert g_kolel_otiyot(emet) == 444,       g_kolel_otiyot(emet)     # 441+3
    assert g_boneeh(emet) == 483,             g_boneeh(emet)           # 1+(1+40)+(1+40+400)
    assert g_haachor(emet) == 1281,           g_haachor(emet)          # 1×1+40×2+400×3
    assert g_mityashev(emet) == 1323,         g_mityashev(emet)        # 441×3
    assert g_reverse_ordinal("ת") == 1
    assert g_reverse_ordinal("א") == 22
    assert g_reverse_ordinal(emet) == 33,     g_reverse_ordinal(emet)  # א=22+מ=10+ת=1
    assert g_ha_merubah_klali(emet) == 194481, g_ha_merubah_klali(emet)  # 441²
    assert g_ayak_bachar(emet) == 414,        g_ayak_bachar(emet)      # א→י(10)+מ→ת(400)+ת→ד(4)
    assert g_ofanim(emet) == 126,             g_ofanim(emet)           # א→פ(80)+מ→מ(40)+ת→ו(6)
    assert g_achas_beta(emet) == 608,         g_achas_beta(emet)       # א→ח(8)+מ→ר(200)+ת→ת(400)
    assert g_reverse_avgad(emet) == 730,      g_reverse_avgad(emet)    # א→ת(400)+מ→ל(30)+ת→ש(300)
    # Word-boundary reset: HaAchor/Mityashev/Boneeh must reset counters between words.
    two_words = "אמת שלום"
    assert g_haachor(two_words) == 1819,      g_haachor(two_words)     # אמת:1281 + שלום:538
    assert g_mityashev(two_words) == 2827,    g_mityashev(two_words)   # אמת:1323 + שלום:1504
    assert g_boneeh(two_words) == 1825,       g_boneeh(two_words)      # אמת:483 + שלום:1342
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
    print(f"  DB built: {n_units} units  OK")

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

    with st.sidebar:
        _extra_refs = st.text_input(
            "Extra Sefaria refs (semicolon-separated)", "",
            key="sefaria_refs",
            help="e.g. Genesis 1; Psalms 23 — appended to the bundled corpus. "
                 "Adding refs triggers a full rebuild (~20–30 s).")

    conn, n_loaded, verse_index = get_connection(_extra_refs)

    with st.sidebar:
        st.header("⚙️ Corpus")
        st.caption(f"{n_loaded:,} Masoretic verses — loaded from bundled corpus.")
        st.divider()
        st.subheader(f"Active methods ({len(CIPHER_NAMES)})")
        st.write(", ".join(CIPHER_NAMES))
        st.caption("Traditional: Standard, Katan, Gadol, Atbash, Albam, Atbach, Avgad, Siduri. "
                   "Value: Ribua, HaMerubahKlali, Kidmi, KatanMispari, ReverseOrdinal. "
                   "Name-expansion (2-letter): Milui, Neelam, Emtzaiyot, Ofanim. "
                   "Vowel-mark (nikud): HaNekudot, ImHaNekudot, MiluiNekudot, ImMiluiNekudot. "
                   "Name-expansion (Maleh): MiluiMaleh, NeelAmMaleh, EmtzaiyotMaleh. "
                   "Temurah: Achbi, Agdat, ReverseAvgad, AyakBachar, AchasBeta. "
                   "Word-structure: HaAchor, Mityashev, Boneeh. "
                   "Kolel: KololEhad, KololOtiyot. "
                   "Text units: Word, ZakefPhrase, TiphchaPhrase, FirstHalf, SecondHalf, Verse, Perek, Parsha.")

    DETAIL_BOUNDARIES = {"Word", "ZakefPhrase", "TiphchaPhrase",
                         "FirstHalf", "SecondHalf", "Verse", "Petucha", "Setuma"}

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
            m_split = _re.search(r"[\s־׃]", cantillated[idx:])
            split = idx + m_split.start() if m_split else len(cantillated)
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
        if boundary in ("TiphchaPhrase", "ZakefPhrase") and matched_cons:
            split_acc = (TIPCHA + ATNACH) if boundary == "TiphchaPhrase" else (ZAKEF_KATON + TIPCHA + ATNACH)
            # Split preserving separators for in-place reconstruction
            toks = _re.split(r"([\s" + _re.escape(MAQAF) + r"]+)", cantillated)
            content = [(i, t) for i, t in enumerate(toks) if t and strip_to_consonants(t)]
            if content:
                phrases, start = [], 0
                for j, (_, t) in enumerate(content):
                    if any(a in t for a in split_acc):
                        c = strip_to_consonants("".join(content[k][1] for k in range(start, j + 1)))
                        phrases.append((start, j, c))
                        start = j + 1
                if start < len(content):
                    c = strip_to_consonants("".join(content[k][1] for k in range(start, len(content))))
                    phrases.append((start, len(content) - 1, c))
                hit = next(((s, e) for s, e, c in phrases if c == matched_cons), None)
                if hit:
                    first_tok = content[hit[0]][0]
                    last_tok  = content[hit[1]][0]
                    result = []
                    for i, tok in enumerate(toks):
                        if i == first_tok == last_tok:
                            result.append(f"<mark>{tok}</mark>")
                        elif i == first_tok:
                            result.append(f"<mark>{tok}")
                        elif i == last_tok:
                            result.append(f"{tok}</mark>")
                        else:
                            result.append(tok)
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
        sub_unit = boundary in ("Word", "ZakefPhrase", "TiphchaPhrase", "FirstHalf", "SecondHalf")
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
        # Derive word-boundary-aware consonants for Kaful/Mityashev/Meshulash
        if boundary == "FirstHalf":
            w_cons, _ = split_halves_word_cons(v.text)
        elif boundary == "SecondHalf":
            _, w_cons = split_halves_word_cons(v.text)
        elif boundary == "Word":
            w_cons = cons
        else:
            w_cons = " ".join(tokenize_words(v.text))
        cantillated_src = matched_text if (sub_unit and matched_text) else v.text
        vals = compute_all_ciphers(cons, cantillated_src, word_consonants=w_cons)
        st.dataframe(pd.DataFrame([vals]), use_container_width=True, hide_index=True)
        # Letter-by-letter breakdown for the active method
        if active_method and active_method in CIPHERS:
            breakdown = cipher_breakdown(active_method, cons, w_cons)
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
            "statistical fingerprint of the Tanach across 34 gematria methods."
        )
        st.divider()

        with st.expander("How to use this app", expanded=True):
            st.caption(
                "📖 **Guide & Sources** (this tab) — Start here. "
                "Explains all 34 gematria methods with earliest Talmudic or medieval sources,"
                "reading tracks, boundary types, and the Rule of the Colel. "
                "Also contains the full Masoretic variant registry."
            )

            st.markdown("**1 · Phrase & Name Matcher**")
            st.markdown(
                "Type any Hebrew word, name, or phrase. The engine strips vowel marks and "
                "cantillation down to the 22 consonants and computes values across all 34 methods "
                "simultaneously. Select a method to see every matching structural unit in the "
                "Tanach — word, half-verse, verse, paragraph, or chapter. Click any result row "
                "to open the full cantillated verse with the matched portion highlighted and a "
                "letter-by-letter breakdown for the chosen method. "
                "Toggle **Rule of the Colel (±1)** to also match values one above or below — "
                "a standard leniency in traditional gematria practice. "
                "Open **🔀 Cross-method coincidences** below the results to see a 34×34 matrix"
                "showing how every cipher value of your input matches every corpus method — "
                "rare coincidences are highlighted, and you can drill into any pair."
            )

            st.markdown("**2 · Scriptural Structural Explorer**")
            st.markdown(
                "Browse the entire Tanach by structural unit: Chapter (פרק Perek), "
                "Torah portion (פרשה Parsha), open paragraph (Pesucha פ), "
                "closed paragraph (Setuma ס), or individual Verse (פסוק). "
                "Every row shows gematria totals under all 34 methods for that block."
                "Click a row to open the verse detail panel."
            )

            st.markdown("**3 · Textual Echoes & Anomalies**")
            st.markdown(
                "The engine automatically scans the corpus for three structural patterns:\n"
                "- **Internal Balance** — a verse whose two halves (split at the Asnachta mark) "
                "share the same gematria value, or differ by only 1 (Colel).\n"
                "- **Proximity Echo** — two consecutive verses sharing the same value under a given method.\n"
                "- **Cross-Method Echo** — two units anywhere in Tanach whose value under one method "
                "equals another unit's value under a different method.\n\n"
                "A **Cross-Method Half-Verse Balance** section below the pattern table lets you "
                "pick any two methods and find verses where the first half under method X equals "
                "the second half under method Y — a cross-method extension of Internal Balance.\n\n"
                "Filter by pattern type or method, then click a row to see the referenced verses."
            )

            st.markdown("**4 · Macro Statistical Dashboard**")
            st.markdown(
                "High-level statistics across the full corpus: highest and lowest values by structure, "
                "value-distribution histograms, a 32-method correlation heatmap, a per-book fingerprint "
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

        with st.expander("The 34 gematria methods", expanded=True):
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
                {"Method": "KatanMispari",
                 "Hebrew": "קטן מספרי (Mispar Katan Mispari)",
                 "Rule": "Sum all Standard values first; then iteratively reduce the grand total to a single digit (digital root). Differs from Katan, which reduces each letter before summing.",
                 "Earliest Source": "Cataloged by early Renaissance Jewish scholars; referenced in the 1906 Jewish Encyclopedia under gematria variants. Treated in Pardes Rimonim (Gate 30)."},
                {"Method": "Siduri",
                 "Hebrew": "מספר סידורי (Mispar Siduri)",
                 "Rule": "Ordinal position: א=1, ב=2 … ת=22. Sequence, not standard value.",
                 "Earliest Source": "Formally categorized as a gematria method in Pardes Rimonim (Sha'ar HaGematria, Gate 30) by R. Moshe Cordovero (1548). Ordinal counting is implicit in earlier Talmudic letter-position discussions (e.g. BT Shabbat 104a)."},
                {"Method": "ReverseOrdinal",
                 "Hebrew": "מספר אחור סידורי (Reverse Ordinal)",
                 "Rule": "Reverse alphabetical index: ת=1, ש=2, ר=3 … א=22. The inverse of Siduri.",
                 "Earliest Source": "Chassidei Ashkenaz (12th–13th c.); referenced in Sefer Raziel HaMalach."},
                {"Method": "Ribua",
                 "Hebrew": "מספר מרובע / פרטי (Mispar Meruba Prati)",
                 "Rule": "Square each individual letter's Standard value, then sum all squares (Σ vᵢ² — per letter, not the total squared).",
                 "Earliest Source": "Mainstreamed by the Ba'al HaTurim (R. Jacob ben Asher, 14th c.) in his Torah commentary. Also documented in Pardes Rimonim (Gate 30)."},
                {"Method": "HaMerubahKlali",
                 "Hebrew": "מספר המרובע הכללי (Mispar HaMerubah HaKlali)",
                 "Rule": "The entire Standard sum squared as one integer: (Σv)². Unlike Ribua which squares per letter.",
                 "Earliest Source": "Pardes Rimonim (R. Moshe Cordovero, Sha'ar 30)."},
                {"Method": "Kidmi",
                 "Hebrew": "מספר קדמי (Mispar Kidmi / HaKadmon)",
                 "Rule": "Cumulative prefix sum of Standard values: each letter's value = Σ Standard values from א up to and including it. א=1, ב=3, ג=6, ד=10 … ת=1495.",
                 "Earliest Source": "Mapped in Pardes Rimonim (Gate 30, Ch. 8) by R. Moshe Cordovero (1548)."},
                {"Method": "Milui",
                 "Hebrew": "מילוי / מספר שמי (Mispar Milui)",
                 "Rule": "Spell each letter's full name as a Hebrew word, then sum Standard values of all spelling letters. א=אלף=111, ב=בית=412, ח=חית=418 …",
                 "Earliest Source": "Deployed in the Zoharic Sifra diTzni'uta (Book of Concealment). Pardes Rimonim, Gate 30 (R. Moshe Cordovero, 1548)."},
                {"Method": "Neelam",
                 "Hebrew": "נעלם (Mispar Neelam — Hidden)",
                 "Rule": "Like Milui, but drop the first letter of each spelling — only the hidden remainder counts. א→לף=110, ח→ית=410 …",
                 "Earliest Source": "Pardes Rimonim (Sha'ar HaGematria, Gate 30, R. Moshe Cordovero, 1548)."},
                {"Method": "Emtzaiyot",
                 "Hebrew": "אמצעיות (Emtzaiyot — Middle Letters)",
                 "Rule": "Standard value of the second (inner) letter of each letter's Milui name spelling. Uses 2-letter (standard Lurianic) spellings: אלף→ל=30, בית→י=10, חית→י=10. For 2-letter names (כף, מם, הא, פא) the inner and terminal letters are the same.",
                 "Earliest Source": "Pardes Rimonim (Sha'ar HaTziruf / Sha'ar 30, R. Moshe Cordovero, 1548). Referenced in Sefer Raziel HaMalach."},
                {"Method": "Ofanim",
                 "Hebrew": "אופנים (Ofanim — Wheels)",
                 "Rule": "Replace each letter with the final letter of its Milui name spelling, take Standard value.",
                 "Earliest Source": "Sefer Raziel HaMalach."},
                {"Method": "HaNekudot",
                 "Hebrew": "מספר הנקודות (Mispar HaNekudot)",
                 "Rule": "Geometric value of each vowel mark: each dot=10, each line=6. Dagesh/Shuruk=10, Sheva=20, Patah=6, Kamatz=16, Hiriq=10, Tsere=20, Segol=30, Holam=10, Kubutz=30. Taamim and shin/sin dot excluded. Returns 0 for consonant-only text.",
                 "Earliest Source": "Conceptual roots in Tikunei HaZohar (Tikun 5 and 70, late 13th c.), which analyses vowel shapes as Yod (dot) and Vav (line). The explicit mathematical gematria system belongs to R. Isaac Luria (Arizal, 16th c.), recorded by R. Chaim Vital in Sha'ar HaKavanot and Etz Chaim (Sha'ar TaNTA — Ta'amim, Nekudot, Tagin, Otiot)."},
                {"Method": "ImHaNekudot",
                 "Hebrew": "עם הנקודות (Im HaNekudot — With the Vowels)",
                 "Rule": "Standard gematria of the consonants plus HaNekudot value of the vowel marks. Combines consonant totals with vowel-mark geometric values in a single sum.",
                 "Earliest Source": "Pardes Rimonim (R. Moshe Cordovero, 1548), Sha'ar HaGematriot (Gate 30), Chapter 8."},
                {"Method": "MiluiNekudot",
                 "Hebrew": "מילוי הנקודות (Mispar Milui HaNekudot)",
                 "Rule": "Standard gematria of the Hebrew NAME of each vowel mark, using Gikatilla's spellings. שבא=303, חיריק=328, צרי=300, סגול=99, פתח=488, קמץ=230, חולם=84, קובוץ=204, דגש=307. Returns 0 for consonant-only text.",
                 "Earliest Source": "R. Yosef Gikatilla, Ginnat Egoz (1274). In the section on the mystery of the nekudot, Gikatilla computes Standard gematria of each vowel mark's spelled-out name (פתח=488, קמץ=230, צרי=300, שבא=303 …) ."},
                {"Method": "ImMiluiNekudot",
                 "Hebrew": "עם מילוי הנקודות (Im Milui HaNekudot)",
                 "Rule": "Standard gematria of the consonants plus Milui HaNekudot (vowel-mark name values). Combines the two layers: consonant totals + gematria of each vowel mark's name.",
                 "Earliest Source": "Extension combining Gikatilla's Milui HaNekudot system (Ginnat Egoz, 1274) with Cordovero's Im HaNekudot framework (Pardes Rimonim, 1548). No single classical source specifies this exact combination."},
                {"Method": "MiluiMaleh",
                 "Hebrew": "מילוי מלא (Milui Maleh — Full Filling)",
                 "Rule": "Like Milui, but uses the Maleh (מלא) 3-letter spellings for כ and מ: כ=כאף=101, מ=מאם=81. All other letter spellings are identical to standard Milui.",
                 "Earliest Source": "The Maleh/Chaser spelling distinction parallels the scribal tradition of כתיב מלא (full spelling) vs. כתיב חסר (deficient spelling). Various Lurianic and Sephardic sources employ the 3-letter forms; cf. Sha'ar HaKavanot and related Ari texts."},
                {"Method": "NeelAmMaleh",
                 "Hebrew": "נעלם מלא (Neelam Maleh — Full Hidden)",
                 "Rule": "Like Neelam, but with Maleh 3-letter spellings: כ→אף=81, מ→אם=41. Reveals an additional Alef hidden inside each of these letters.",
                 "Earliest Source": "Parallel to Milui Maleh; the Maleh spelling tradition applied to the Neelam (hidden remainder) system."},
                {"Method": "EmtzaiyotMaleh",
                 "Hebrew": "אמצעיות מלא (Emtzaiyot Maleh — Full Middle)",
                 "Rule": "Like Emtzaiyot, but with Maleh 3-letter spellings. Both כ (כאף) and מ (מאם) now yield א=1 as their inner letter, fully distinct from their Ofanim value. אלף→ל=30, בית→י=10, כאף→א=1, מאם→א=1.",
                 "Earliest Source": "Maleh spelling tradition applied to the Emtzaiyot system; follows from the same Pardes Rimonim framework."},
                {"Method": "Atbash",
                 "Hebrew": "אתב\"ש (At-Bash)",
                 "Rule": "Mirror the alphabet: א↔ת, ב↔ש, ג↔ר … then Standard values of the swapped letters.",
                 "Earliest Source": "'Sheshach' (שֵׁשַׁךְ) in Jeremiah 25:26 and 51:41 is Babel (בָּבֶל) by Atbash. Recognized explicitly in BT Sanhedrin 22b. Classified as a temurah system in Sefer Yetzirah ch. 2."},
                {"Method": "Albam",
                 "Hebrew": "אלב\"ם (Al-Bam)",
                 "Rule": "Split 22 letters into two groups of 11; swap across groups: א↔ל, ב↔מ, ג↔נ … (ROT-11).",
                 "Earliest Source": "Explicitly detailed in Yalkut Shimoni (Yisro, Remez 271). Classical temurah in Sefer Yetzirah ch. 2 (3rd–6th c. CE)."},
                {"Method": "Achbi",
                 "Hebrew": "אכב\"י (Ach-Bi)",
                 "Rule": "Split into two 11-letter groups, reverse each internally: א↔כ, ב↔י … ל↔ת, מ↔ש …",
                 "Earliest Source": "Outlined as a structural matrix in Sefer Raziel HaMalach. Part of the temurah permutation tradition in Sefer Yetzirah ch. 2 (3rd–6th c. CE)."},
                {"Method": "Atbach",
                 "Hebrew": "אטב\"ח (At-Bach)",
                 "Rule": "Pairs whose values sum to 10/100/1000: א↔ט, ב↔ח; י↔צ, כ↔פ; ק↔ץ … Finals carry 600–900.",
                 "Earliest Source": "Attributed to Rabbi Chiya (late 2nd/early 3rd c. CE). The phrase 'in the Atbah of Rabbi Chiya' (בְּאַטְבַּ״ח שֶׁל רַבִּי חִיָּיא) appears explicitly in BT Sukkah 52b. Also classified in the Baraita of 32 Hermeneutical Rules of R. Eliezer ben Yose ha-Gelili."},
                {"Method": "Avgad",
                 "Hebrew": "אבג\"ד (Av-Gad / Abgad)",
                 "Rule": "+1 cyclic shift: א→ב, ב→ג … ת→א. Then Standard values of the shifted letters. Also known as Mispar Ha'Ahari (next-letter value).",
                 "Earliest Source": "Codified in Ta'am Zekenim (R. Eliezer Ashkenazi). Cyclic letter-shifting tradition rooted in Sefer Yetzirah (3rd–6th c. CE). R. Abraham Abulafia (13th c.) employs the next-letter method in his prophetic Kabbalah texts."},
                {"Method": "Agdat",
                 "Hebrew": "אגד\"ת (Ag-Dat)",
                 "Rule": "+2 cyclic shift: א→ג, ב→ד … ש→א, ת→ב. Then Standard values of the shifted letters.",
                 "Earliest Source": "Pardes Rimonim, Gate 22 (R. Moshe Cordovero, 1548)."},
                {"Method": "ReverseAvgad",
                 "Hebrew": "אבג\"ד הפוך (Reverse Avgad)",
                 "Rule": "−1 cyclic shift: Bet→Alef, Gimel→Bet … Alef wraps to Tav. Opposite of Avgad.",
                 "Earliest Source": "R. Eliezer Ashkenazi, Ta'am Zekenim."},
                {"Method": "AyakBachar",
                 "Hebrew": "אי\"ק בכ\"ר (Ayak Bachar)",
                 "Rule": "3×9 cyclic rotation across units/tens/hundreds columns: א→י→ק→א, ב→כ→ר→ב … ט→צ→ץ→ט.",
                 "Earliest Source": "Tikunei HaZohar (Tikkun 21)."},
                {"Method": "AchasBeta",
                 "Hebrew": "אח\"ס בט\"ע (Achas Beta)",
                 "Rule": "22 letters in three blocks of 7/7/7 cycle positionally; ת stands outside and is invariant.",
                 "Earliest Source": "Pardes Rimonim (R. Moshe Cordovero, Sha'ar 30)."},
                {"Method": "Boneeh",
                 "Hebrew": "מספר בונה (Mispar Bone'eh — Building)",
                 "Rule": "Cumulative prefix sums per word: letter 1 alone, then 1+2, then 1+2+3 … Resets at each word boundary.",
                 "Earliest Source": "The stacking/accumulation method has classical roots in Chasidei Ashkenaz (12th–13th c.), cited in Zohar II 270a, and catalogued in Pardes Rimonim (Gate 30, Ch. 8) by R. Moshe Cordovero. The classical label is Mispar HaAchorayim (מספר האחוריים) or Mispar HaAkhor. The name 'Bone'eh' (Building) is a modern label not found in classical sources."},
                {"Method": "HaAchor",
                 "Hebrew": "מספר האחור (Mispar HaAchor)",
                 "Rule": "Each letter × its ordinal position within the word (1st×v₁ + 2nd×v₂ + …). Position resets per word.",
                 "Earliest Source": "Pardes Rimonim (R. Moshe Cordovero, Sha'ar 30, Ch. 8)."},
                {"Method": "Mityashev",
                 "Hebrew": "מספר מיושב (Mispar Mityashev)",
                 "Rule": "Each letter × total letter count of its word: Σ(vᵢ × N). N resets per word. 3-letter word: every value × 3.",
                 "Earliest Source": "Traced to early Italian Kabbalistic manuscripts; documented in operational gematria manuals."},
                {"Method": "KololEhad",
                 "Hebrew": "כולל (Kolel — Word)",
                 "Rule": "Standard total + 1. The word counted as one additional unit. Standard ±1 adjustment to link words differing by one.",
                 "Earliest Source": "Ba'al HaTurim (R. Jacob ben Asher, 14th c.)."},
                {"Method": "KololOtiyot",
                 "Hebrew": "כולל אותיות (Kolel — Letters / Mispar Musafi)",
                 "Rule": "Standard total + letter count. Each letter adds 1 beyond its gematria value. Also called Mispar Musafi.",
                 "Earliest Source": "Kabbalistic practice; parallel to the general Kolel tradition. Also called Mispar Musafi in later sources."}
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
        mode = st.radio("Search by", ["Hebrew text", "Gematria value"],
                        horizontal=True, key="t1_mode")

        if mode == "Hebrew text":
            # ── Simple text input (keyboard widget removed — see commented block below) ──
            c1, c2 = st.columns([4, 2])
            with c1:
                raw = st.text_input(
                    "Hebrew phrase or name", key="t1_hebrew",
                    placeholder="e.g. שלום",
                    help="Nikud and ta'amim are ignored for most ciphers. "
                    "For HaNekudot / ImHaNekudot / MiluiNekudot / ImMiluiNekudot, include nikud for accurate results.")
            with c2:
                colel = st.toggle("Rule of the Colel (±1)", value=False,
                                  key="t1_text_colel",
                                  help="Also match Value−1 and Value+1.")

            # ── ON-SCREEN HEBREW KEYBOARD (disabled — kept for potential revival) ──────
            # Removed because Streamlit's widget-key lifecycle wiped the accumulation
            # buffer on every rerun when the text_input was unmounted, and the mobile
            # UX gains weren't worth the complexity.  Full implementation below.
            #
            # _KBD_KEY  = "t1_hebrew"
            # _KBD_BUF  = "t1_hebrew_buf"   # durable buffer, never a widget key
            # _KBD_OPEN = "t1_kbd_open"
            # if _KBD_BUF not in st.session_state:
            #     st.session_state[_KBD_BUF] = ""
            # def _kbd_add(ch):
            #     st.session_state[_KBD_BUF] = st.session_state.get(_KBD_BUF, "") + ch
            # def _kbd_bksp():
            #     v = st.session_state.get(_KBD_BUF, "")
            #     st.session_state[_KBD_BUF] = v[:-1] if v else ""
            # def _kbd_clear():  st.session_state[_KBD_BUF] = ""
            # def _kbd_toggle(): st.session_state[_KBD_OPEN] = not st.session_state.get(_KBD_OPEN, False)
            # kbd_open = st.session_state.get(_KBD_OPEN, False)
            # c1, c2, c3 = st.columns([4, 2, 1])
            # with c1:
            #     if kbd_open:
            #         _txt = st.session_state.get(_KBD_BUF, "")
            #         st.markdown(f'<div style="border:1px solid #ccc;border-radius:6px;'
            #             f'padding:8px 12px;min-height:2.6em;font-size:1.15em;'
            #             f'direction:rtl;text-align:right;background:#fff;'
            #             f'color:#222;line-height:1.9;user-select:none;">'
            #             f'{_txt}<span style="border-right:2px solid #555;'
            #             f'margin-right:2px;animation:t1blink 1s step-end infinite;">'
            #             f'&nbsp;</span></div>'
            #             f'<style>@keyframes t1blink{{50%{{opacity:0}}}}</style>',
            #             unsafe_allow_html=True)
            #         raw = _txt
            #     else:
            #         if _KBD_KEY not in st.session_state:
            #             st.session_state[_KBD_KEY] = st.session_state.get(_KBD_BUF, "")
            #         raw = st.text_input("Hebrew phrase or name", key=_KBD_KEY,
            #             placeholder="e.g. שלום",
            #             help="Type or paste Hebrew. Nikud and ta'amim are stripped.")
            #         st.session_state[_KBD_BUF] = raw
            # with c2:
            #     colel = st.toggle("Rule of the Colel (±1)", value=False, key="t1_text_colel")
            # with c3:
            #     st.markdown("<div style='padding-top:1.65em'>", unsafe_allow_html=True)
            #     st.button("⌨", key="t1_kbd_toggle", on_click=_kbd_toggle,
            #               help="Toggle Hebrew on-screen keyboard", use_container_width=True)
            #     st.markdown("</div>", unsafe_allow_html=True)
            # _kbd_slot = st.empty()
            # if kbd_open:
            #     with _kbd_slot.container(border=True):
            #         for _row in ["יטחזוהדגבא", "רקצפעסנמלכ", "ץףןםךתש"]:
            #             _cols = st.columns(len(_row))
            #             for _col, _ch in zip(_cols, _row):
            #                 _col.button(_ch, key=f"hk_{_ch}", on_click=_kbd_add,
            #                             args=(_ch,), use_container_width=True)
            #         st.caption("Nikud — click after the consonant")
            #         _NIKUD = [
            #             ("פַּתָּח","ַ"),("קָמַץ","ָ"),("צֵירֵי","ֵ"),
            #             ("סְגוֹל","ֶ"),("חִירִיק","ִ"),("חוֹלָם","ֹ"),
            #             ("קֻבּוּץ","ֻ"),("דָּגֵשׁ","ּ"),("שְׁוָא","ְ"),
            #         ]
            #         for _ni in range(0, len(_NIKUD), 3):
            #             _nc = st.columns(3)
            #             for _col, (_name, _mark) in zip(_nc, _NIKUD[_ni:_ni+3]):
            #                 _col.button(f"◌{_mark}\n{_name}", key=f"hk_{_mark}",
            #                             on_click=_kbd_add, args=(_mark,), use_container_width=True)
            #         _ctl1, _ctl2, _ctl3 = st.columns(3)
            #         _ctl1.button("Space", key="hk_space", on_click=_kbd_add,
            #                      args=(" ",), use_container_width=True)
            #         _ctl2.button("⌫ Delete", key="hk_bksp",
            #                      on_click=_kbd_bksp, use_container_width=True)
            #         _ctl3.button("✕ Clear", key="hk_clear",
            #                      on_click=_kbd_clear, use_container_width=True)
            # ── END ON-SCREEN KEYBOARD ────────────────────────────────────────────────

            cons = normalize_query(raw)
            word_cons = " ".join(tokenize_words(raw))
            _sc1, _sc2 = st.columns([4, 1])
            with _sc1:
                st.markdown(f"**Cleaned consonants:** `{cons or '—'}`")
            with _sc2:
                if st.button("🔍 Search", key="t1_search_btn", type="primary",
                             use_container_width=True, disabled=not cons):
                    st.session_state["t1_committed"] = {
                        "cons": cons, "raw": raw, "wcons": word_cons}
        else:
            nc1, nc2 = st.columns([3, 2])
            with nc1:
                num_raw = st.number_input(
                    "Gematria value", min_value=1, max_value=10_000_000,
                    value=2701, step=1, key="t1_num",
                    help="Search every method for corpus units equal to this value.")
            with nc2:
                colel = st.toggle("Rule of the Colel (±1)", value=False,
                                  key="t1_num_colel",
                                  help="Also match Value−1 and Value+1.")
            target = int(num_raw)
            cons = ""
            word_cons = ""

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
                ["Perek", "Parsha", "Verse", "Petucha", "Setuma",
                 "FirstHalf", "SecondHalf",
                 "TiphchaPhrase", "ZakefPhrase", "Word"],
                default=["Verse", "FirstHalf", "SecondHalf", "Word"],
                format_func=lambda b: BOUNDARY_LABELS.get(b, b))

        # Perek/Parsha rows are stored under the "Aggregate" track (a DB tag,
        # not a reading tradition). Auto-include it when those boundaries are selected.
        effective_tracks = list(tracks)
        if any(b in (bounds or []) for b in ("Perek", "Parsha")) and "Aggregate" not in effective_tracks:
            effective_tracks.append("Aggregate")

        if mode == "Gematria value":
            res_num = search_value_all_methods(
                conn, target, limit_per_method=50, colel=colel,
                tracks=effective_tracks or None, boundaries=bounds or None)
            st.markdown(
                f"#### Corpus units equal to **{target}**"
                + (f" (Colel window {target-1}–{target+1})" if colel else "")
                + f" — {len(res_num)} match(es) across all methods")
            if res_num.empty:
                st.info(
                    f"No corpus unit equals {target} under any of the {len(CIPHER_NAMES)} "
                    "methods at the current filters. Try enabling Colel, widening the "
                    "Text units filter, or checking a different track.")
            else:
                res_num_disp = res_num.copy()
                res_num_disp["Method"] = res_num_disp["Method"].map(
                    lambda c: CIPHER_DISPLAY_NAMES.get(c, c))
                event_num = st.dataframe(
                    res_num_disp, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row", key="t1_num_sel")
                sel_num = event_num.selection.rows
                if sel_num:
                    row_num = res_num.iloc[sel_num[0]]
                    with st.expander("📜 Verse detail", expanded=True):
                        render_verse_detail(
                            row_num["Book"], row_num["Chapter"], row_num["Verse"],
                            row_num["Boundary"], matched_text=row_num.get("Text"),
                            active_method=row_num["Method"])
        elif _committed := st.session_state.get("t1_committed"):
            _c_cons  = _committed["cons"]
            _c_raw   = _committed["raw"]
            _c_wcons = _committed["wcons"]
            payload = search_phrase(conn, _c_cons, cantillated=_c_raw,
                                    word_consonants=_c_wcons, colel=colel,
                                    tracks=effective_tracks or None, boundaries=bounds or None)
            vals = payload["values"]
            st.markdown(f"#### Results for `{_c_cons}`")
            st.markdown("**Computed values across all methods**")
            st.dataframe(pd.DataFrame([vals]), use_container_width=True,
                         hide_index=True)

            ciphers_sel = st.multiselect(
                "Show matches for method(s)", CIPHER_NAMES, default=[CIPHER_NAMES[0]],
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c))
            active_ciphers = ciphers_sel or [CIPHER_NAMES[0]]
            _NIKUD_CIPHERS = {"HaNekudot", "ImHaNekudot", "MiluiNekudot", "ImMiluiNekudot"}
            _has_nikud = any("ְ" <= ch <= "ׇ" for ch in _c_raw)
            if any(c in _NIKUD_CIPHERS for c in active_ciphers) and not _has_nikud:
                st.warning(
                    "One or more selected methods (HaNekudot / ImHaNekudot / "
                    "MiluiNekudot / ImMiluiNekudot) count vowel marks. "
                    "Your input has no nikud, so their values will be 0 or equal to Standard. "
                    "Add nikud for accurate results.")
            for cipher in active_ciphers:
                st.caption(CIPHER_BLURB.get(cipher, ""))
                res = payload["results"][cipher]
                tgt = vals[cipher]
                st.markdown(
                    f"#### {CIPHER_DISPLAY_NAMES.get(cipher, cipher)} = {tgt}"
                    + (f" (Colel window {tgt-1}–{tgt+1})" if colel else "")
                    + f" — {len(res)} result(s)")
                if res.empty:
                    st.info("No structural unit in the loaded corpus matches this value.")
                else:
                    event = st.dataframe(
                        res, use_container_width=True, hide_index=True,
                        on_select="rerun", selection_mode="single-row",
                        key=f"t1_sel_{cipher}")
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
                st.markdown("**Drill into a pair/s**")
                dc1, dc2 = st.columns(2)
                with dc1:
                    drill_a = st.selectbox(
                        "Method A", CIPHER_NAMES, key="xm_drill_a",
                        format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c)
                    )
                with dc2:
                    drill_b_list = st.multiselect(
                        "Method B (one or more)", CIPHER_NAMES,
                        default=[CIPHER_NAMES[0]], key="xm_drill_b",
                        format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c)
                    )
                drill_val = a_vals[drill_a]
                for drill_b in (drill_b_list or [CIPHER_NAMES[0]]):
                    st.markdown(
                        f"**{drill_a}({_c_raw.strip()}) = {drill_val}** "
                        f"→ corpus units with **{drill_b} = {drill_val}**"
                        + (" ± 1" if colel else "")
                    )
                    drill_res = search_value(
                        conn, drill_b, drill_val, colel, effective_tracks or None, bounds or None
                    )
                    if drill_res.empty:
                        st.info(f"No corpus unit matches {drill_a}/{drill_b} at the current filters.")
                    else:
                        ev_drill = st.dataframe(
                            drill_res, use_container_width=True, hide_index=True,
                            on_select="rerun", selection_mode="single-row",
                            key=f"xm_drill_sel_{drill_b}",
                        )
                        if ev_drill.selection.rows:
                            rd = drill_res.iloc[ev_drill.selection.rows[0]]
                            with st.expander("📜 Verse detail", expanded=True):
                                render_verse_detail(
                                    rd["Book"], rd["Chapter"], rd["Verse"],
                                    rd["Boundary"], matched_text=rd.get("Text"),
                                    active_method=drill_b,
                                )

            with st.expander("🔍 All word-span matches", expanded=False):
                st.caption(
                    "Scans every contiguous sequence of 2–N words in the corpus "
                    "for matches to the same gematria value. Finds patterns that "
                    "cross structural boundaries (e.g., last word of one phrase + "
                    "first words of the next)."
                )
                sc1, sc2 = st.columns([2, 1])
                with sc1:
                    _span_default = active_ciphers[0] if active_ciphers else CIPHER_NAMES[0]
                    span_cipher = st.selectbox(
                        "Cipher",
                        CIPHER_NAMES,
                        index=CIPHER_NAMES.index(_span_default) if _span_default in CIPHER_NAMES else 0,
                        format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                        key="span_cipher",
                    )
                with sc2:
                    span_max = st.slider("Max words in span", 2, 15, 7, key="span_max")
                span_tgt = vals[span_cipher]
                st.markdown(
                    f"Searching **{span_cipher} = {span_tgt}**"
                    + (f" (colel ±1: {span_tgt-1}–{span_tgt+1})" if colel else "")
                )
                span_df = span_search(
                    conn, span_tgt, span_cipher,
                    max_span=span_max, colel=colel,
                    tracks=effective_tracks or None,
                )
                if span_df.empty:
                    st.info("No multi-word span matches this value with the current settings.")
                else:
                    st.markdown(f"**{len(span_df)} span match(es)**")
                    span_event = st.dataframe(
                        span_df, use_container_width=True, hide_index=True,
                        on_select="rerun", selection_mode="single-row", key="span_sel")
                    span_sel = span_event.selection.rows
                    if span_sel:
                        sr = span_df.iloc[span_sel[0]]
                        with st.expander("📜 Verse detail", expanded=True):
                            render_verse_detail(
                                sr["Book"], int(sr["Ch"]), int(sr["Vs"]),
                                "Verse", active_method=span_cipher)
        else:
            st.warning("Enter a Hebrew phrase to search.")

    # ===================== TAB 2: STRUCTURAL EXPLORER =====================
    with tab2:
        st.subheader("Scriptural Structural Explorer")
        kind = st.radio(
            "Browse by",
            ["Perek", "Parsha", "Petucha", "Setuma", "Verse",
             "FirstHalf", "SecondHalf", "TiphchaPhrase", "ZakefPhrase"],
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
                       "that shares that number, across all 34 methods.")
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
                     "any of the 34 methods.")

            sel_rows = event2.selection.rows
            if sel_rows:
                row2 = show.iloc[sel_rows[0]]

                # Show this row's values across all 34 methods.
                summary = {c: int(row2[c]) for c in CIPHER_NAMES if c in row2.index}
                st.markdown("**Selected unit — values across all 34 methods:**")
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

        _low_cardinality = {"Katan", "KatanMispari"}
        if any(c in eff_a or c in eff_b for c in _low_cardinality) and int(t3_minval) < 41:
            triggered = [c for c in _low_cardinality if c in eff_a or c in eff_b]
            st.warning(
                f"⚠️ **{' / '.join(triggered)}** collapse to very few distinct values "
                "(Katan: 1–40; KatanMispari: 1–9), producing artificially high match rates. "
                "Set **Min value ≥ 41** or deselect these methods to reduce noise.")

        _hyperscale = {"HaMerubahKlali"}
        if any(c in eff_a or c in eff_b for c in _hyperscale):
            triggered_h = [c for c in _hyperscale if c in eff_a or c in eff_b]
            st.info(
                f"ℹ️ **{' / '.join(triggered_h)}** produces very large values "
                "(squared totals). Matches will be rare to nonexistent; "
                "Kolel ±1 has no practical effect at this scale.")

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
            numeric_cols = [c for c in CIPHER_NAMES
                           if c in plot_df.columns and c not in _HEATMAP_EXCLUDE]
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

            _BALANCE_COLS = [c for c in CIPHER_NAMES if c not in _HEATMAP_EXCLUDE]

            @st.cache_data(show_spinner="Computing cross-method balance matrix…")
            def _xm_balance_matrix(_conn):
                cols = ", ".join(
                    f'SUM(CASE WHEN ABS(u1.{mx} - u2.{my}) <= 1 THEN 1 ELSE 0 END) '
                    f'AS "{mx}_vs_{my}"'
                    for mx in _BALANCE_COLS for my in _BALANCE_COLS
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
                data = [[int(row[f"{mx}_vs_{my}"]) / total for my in _BALANCE_COLS]
                        for mx in _BALANCE_COLS]
                return pd.DataFrame(data, index=_BALANCE_COLS, columns=_BALANCE_COLS), total

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
