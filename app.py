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
import threading
import uuid
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
# value is read via GADOL_FINALS (500-900); other substituted letters use STANDARD.
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
#
# Rule: each dot = a yud = 10, each line = a vav = 6. The dot/line identity is
# Tikunei Zohar Tikkun 70 ("נקודה איהי י', וקוא איהו ו'"); the COMPONENT COUNTS
# below are the Ramak's own shape descriptions in Pardes Rimonim שער כ"ח ch.1,
# quoted per row. The arithmetic itself (multiplying and summing) is printed in
# no classical text — Chabadpedia calls it "השיטה הנפוצה", the common method,
# and notes there are two. Do not credit the Arizal or the Ramak for the sums.
#
# ⚠️ THE DAGESH IS NOT HERE, DELIBERATELY. It scores nothing: Etz Chaim Sha'ar 5
# says דגש ורפה "אינם לא טעמים ולא נקודות ולא תגין", and שער כ"ח — the Ramak's
# whole gate on the nekudot — never mentions it. Only the SHURUK form of U+05BC
# counts, and since Unicode gives shuruk and dagesh the same codepoint it cannot
# live in this table at all; see is_shuruk() and SHURUK_VAL.
#
# Shin/sin dot, meteg and all taamim are likewise excluded as consonantal or
# accentual rather than vowel marks.
NIKUD_VALS: Dict[str, int] = {
    "ְ": 20,  # Sheva  — "ב' נקודות זו על גב זו"                = 2 dots
    "ִ": 10,  # Hiriq  — "נקודה תחת האות"                        = 1 dot
    "ֵ": 20,  # Tsere  — "שני נקודות זו בצד זו"                  = 2 dots
    "ֶ": 30,  # Segol  — "שלש נקודות השתים זו בצד זו וא' תחתי'"  = 3 dots
    "ַ":  6,  # Patah  — "קו משוכה מן הימין אל השמאל"            = 1 line
    "ָ": 16,  # Kamatz — "קו מתוח מן הימין אל השמאל ונקודה תחתיה" = line + dot
    "ֹ": 10,  # Holam  — "נקודה למעלה מן האות"                   = 1 dot
    "ֺ": 10,  # Holam haser for vav — one dot
    "ֻ": 30,  # Kubutz — "ג' נקודות זה תחת זה ... באלכסון"        = 3 dots
    # The three compound vowels. The Ramak names them as sheva PLUS a base
    # vowel — "וג' מורכבות, שבא קמץ, שבא פתח, שבא סגול, וקוראים אותו חטף קמץ
    # חטף פתח חטף סגול" — so the tally is the sheva's two dots plus the base's
    # own components. These used to be scored as the bare base vowel, i.e. as
    # if the sheva on the page were not there, which contradicted both the
    # source and this table's own dot-and-line rule.
    "ֲ": 26,  # Hataf Patah  = sheva(20) + patah(6)
    "ֳ": 36,  # Hataf Kamatz = sheva(20) + kamatz(16)
    "ֱ": 50,  # Hataf Segol  = sheva(20) + segol(30)
}

# U+05BC is BOTH the shuruk dot and the dagesh — see is_shuruk() for the split.
DAGESH_OR_SHURUK = "ּ"
SHURUK_VAL = 10          # "נקודה בתוך הו'" — one dot; the vav is a consonant

# Standard gematria of the Hebrew NAME of each vowel mark, for Mispar Milui
# HaNekudot. The METHOD is Gikatilla's (Ginnat Egoz, 1274); the SPELLINGS below
# are the Remak's, because his are the ones that can actually be read: Ginnat
# Egoz is not available to check, and the only "quote" ever offered for its
# orthography turned out to be fabricated. Pardes Rimonim שער כ"ח is on Sefaria
# and its usage is countable, so it is the verifiable baseline.
#
# Frequencies across שער כ"ח, which is why these spellings and not others:
#   צירי 26x / צרי 3x  ·  חירק 16x / חיריק 0x  ·  חולם 19x / חלם 4x
#   שורק 24x / שרק 12x  ·  סגול 23x  ·  שבא 13x  ·  קמץ 43x  ·  פתח 16x
# ⚠️ No classical text PRINTS these sums (nobody writes "פתח = 488"); the
# arithmetic is ours, applied to the Remak's spelling. Same footing as the
# dot=10/line=6 sums in NIKUD_VALS.
#
# ⚠️ No דגש entry, for the same reason it is absent from NIKUD_VALS: a dagesh is
# not a nekuda, so it has no vowel-name to sum. The shuruk IS one, named שורק —
# see SHURUK_NAME_VAL, kept out of this table because Unicode shares its codepoint.
NEKUDA_NAME_VALS: Dict[str, int] = {
    # Each spelling carries its own count from שער כ"ח, so the basis for every
    # number is visible at the line that sets it. "0x" means the gate never
    # writes that form — see the two flagged below.
    "\u05B0": _spelling_val("שבא"),    # שבא   = 303  (שבא 13x / שוא 2x)
    "\u05B4": _spelling_val("חירק"),   # חירק  = 318  (חירק 16x / חיריק 0x)
    "\u05B5": _spelling_val("צירי"),   # צירי  = 310  (צירי 26x / צרי 3x)
    "\u05B6": _spelling_val("סגול"),   # סגול  = 99   (סגול 23x / סגל 0x)
    "\u05B7": _spelling_val("פתח"),    # פתח   = 488  (פתח 16x, no variant)
    "\u05B8": _spelling_val("קמץ"),    # קמץ   = 230  (קמץ 43x / קומץ 3x)
    "\u05B9": _spelling_val("חולם"),   # חולם  = 84   (חולם 19x / חלם 4x)
    "\u05BA": _spelling_val("חולם"),   # Holam haser — same name as holam
    # ⚠️ KUBUTZ IS NOT THE REMAK'S SPELLING. שער כ"ח never writes קובוץ (0x);
    # it has קבוץ once, and calls the mark קבוץ שפתים. But he treats it as a
    # form of shuruk ("שורק של ג' נקודות"), so he never gives it a standalone
    # name to copy. קובוץ is the conventional modern spelling, kept for
    # recognisability. This is the one entry NOT grounded in his usage —
    # קבוץ would be 198.
    "\u05BB": _spelling_val("קובוץ"),  # קובוץ = 204  (קובוץ 0x / קבוץ 1x)
    # The chatafim are SHEVA + BASE here, matching the geometric method.
    #
    # No classical text computes milui on a chataf at all — milui is only ever
    # done on the discrete primary vowel names. But no classical text prints
    # ANY of these sums either, so "unsourced" does not distinguish this case:
    # the arithmetic in both nikud methods is a consistent rule applied to
    # sourced spellings and shapes. Given that, naming a chataf for its base
    # vowel alone would be the inconsistent choice — the geometric method counts
    # the sheva's two dots, so the naming method names the sheva. The Remak's
    # own "שבא קמץ, שבא פתח, שבא סגול" (שער כ"ח פרק א׳) is exactly that reading.
    #
    # Changed 2026-08-05. Previously these were the bare base vowel (488/230/99).
    "\u05B2": _spelling_val("שבא") + _spelling_val("פתח"),   # שבא+פתח  = 791
    "\u05B3": _spelling_val("שבא") + _spelling_val("קמץ"),   # שבא+קמץ  = 533
    "\u05B1": _spelling_val("שבא") + _spelling_val("סגול"),  # שבא+סגול = 402
}

SHURUK_NAME_VAL = _spelling_val("שורק")   # שורק = 606 (defective שרק = 600)


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


def is_shuruk(text: str, i: int) -> bool:
    """True when text[i] (U+05BC) is a SHURUK rather than a dagesh.

    Unicode gives the shuruk dot and the dagesh the same codepoint, so they can
    only be told apart by position: a shuruk is the dot inside a vav that
    carries no vowel of its own (וּ), while the same mark anywhere else — or on
    a vav that does have a vowel — is a consonantal dagesh.

    The distinction matters because the dagesh is NOT one of the nekudot and
    scores nothing, whereas the shuruk is one of the nine and scores as its dot.
    Pardes Rimonim שער כ"ח ch.1 describes the shuruk as "נקודה בתוך הו'" — a dot
    inside the vav; the vav itself is a consonant and takes no part.
    """
    if i <= 0 or i >= len(text) or text[i] != DAGESH_OR_SHURUK:
        return False
    if text[i - 1] != "ו":
        return False
    nxt = text[i + 1] if i + 1 < len(text) else ""
    return not ("ְ" <= nxt <= "ֻ")


def g_hanekudot(text: str) -> int:
    """HaNekudot — geometric value of vowel marks (dot=10, line=6).

    Operates on raw cantillated text; taamim excluded. The dagesh scores 0 (it
    is not a nekuda — see NIKUD_VALS); a shuruk scores as its dot.
    Returns 0 for consonant-only text.
    """
    total = 0
    for i, ch in enumerate(text):
        if ch == DAGESH_OR_SHURUK:
            total += SHURUK_VAL if is_shuruk(text, i) else 0
        else:
            total += NIKUD_VALS.get(ch, 0)
    return total


def g_milui_nekudot(text: str) -> int:
    """Milui HaNekudot (Gikatilla, Ginnat Egoz 13th c.) — sum of the Standard
    gematria of the Hebrew NAME of each vowel mark found in the text.

    Same shuruk/dagesh split as g_hanekudot: a dagesh has no name to sum here
    because it is not a nekuda, while a shuruk is named שורק.
    Returns 0 for consonant-only text.
    """
    total = 0
    for i, ch in enumerate(text):
        if ch == DAGESH_OR_SHURUK:
            total += SHURUK_NAME_VAL if is_shuruk(text, i) else 0
        else:
            total += NEKUDA_NAME_VALS.get(ch, 0)
    return total


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
    """Mispar Mityashev - each letter × letter count of its word; N resets per word.

    RETAINED BUT NOT OFFERED. This is not in CIPHERS, so the app never computes
    or indexes it. No classical source could be found for the method under this
    name: מספר מיושב appears nowhere in Pardes Rimonim's Sha'ar HaGematriaot
    (Gate 30 or 22) and returns zero hits across Sefaria's corpus, and the
    earlier citation ("early Italian Kabbalistic manuscripts") named nothing a
    reader could check. It was swapped out for Mispar HaMispari, which Cordovero
    defines explicitly. Kept here — with its self-tests and its word-boundary
    plumbing — so reinstating it is a one-line change if a source turns up.
    Beware when researching: some sources use 'mispar meyushav' for Mispar Katan
    (truncating zeros), which is a different calculation from this one.
    """
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


# Hebrew number-names, in the spellings the Remak uses in Pardes Rimonim Gate 30.
# His own worked values fix the orthography and are the reason this table is
# masculine/classical rather than the feminine/modern forms (עשר, ארבע, שלושים)
# that online calculators use: he states yud -> עשרה = 575 (תקע"ה) and
# heh -> חמשה = 353 (שנ"ג), and only these spellings reproduce those totals.
# Following him means values here differ from those calculators on 13 of 22
# letters — a deliberate choice of the primary source over the popular table.
#
# ⚠️ The hundreds rows deliberately break the masculine rule the units follow.
# Hebrew number-gender is inverted, and with the feminine noun מאות the attested
# form is שלש מאות / ארבע מאות, never שלשה מאות. So this table follows the
# Remak's own anchors for 1-90 and biblical idiom for the hundreds, where he is
# silent — two rules, and the difference is 5 points per hundreds row. Recorded
# rather than left looking like an oversight; do not "fix" it to שלשה מאות.
# (Written spaced rather than closed: a space contributes nothing to a total,
# so שלש מאות and שלשמאות are numerically identical.)
NUMBER_NAMES: Dict[int, str] = {
    1: "אחד", 2: "שנים", 3: "שלשה", 4: "ארבעה", 5: "חמשה",
    6: "ששה", 7: "שבעה", 8: "שמונה", 9: "תשעה", 10: "עשרה",
    20: "עשרים", 30: "שלשים", 40: "ארבעים", 50: "חמשים",
    60: "ששים", 70: "שבעים", 80: "שמונים", 90: "תשעים",
    100: "מאה", 200: "מאתים", 300: "שלש מאות", 400: "ארבע מאות",
    500: "חמש מאות",
}

# Teens, built as unit + עשר. The masculine-form עשר here agrees both with the
# Remak's units rule and with the corpus (שנים עשר, שמנה עשר are the attested
# forms), so unlike the hundreds there is no conflict between the two guides.
TEEN_NAMES: Dict[int, str] = {
    11: "אחד עשר", 12: "שנים עשר", 13: "שלשה עשר", 14: "ארבעה עשר",
    15: "חמשה עשר", 16: "ששה עשר", 17: "שבעה עשר", 18: "שמונה עשר",
    19: "תשעה עשר",
}


def compose_number_name(n: int) -> str:
    """Spell an arbitrary number 1..999 as a Hebrew number-word phrase.

    Used by Mispar HaMispari HaGadol (Gate 30 §9), where the number to be named
    is a letter's MILUI total and is therefore usually a compound (alef 111,
    bet 412 …) rather than one of the 22 single-letter values §8 needs.

    Composition rules, and why each is what it is:
      * hundreds first, then remainder — the order is idiom only, since gematria
        is a sum and addition commutes, so this cannot change any value;
      * the parts are joined by a conjunctive vav (מאה ועשרים), the dominant
        biblical form (מאה ועשרים Gen 6:3, מאה ועשר Gen 50:22, 27 attestations);
      * 11-19 use the אחד עשר series rather than the frozen archaism עשתי עשר,
        which is confined to priestly/architectural contexts and is not the
        living Rabbinic form.

    The vav and the choice of eleven-form are the only genuinely open decisions
    here; both are worth a few points and neither is fixed by the source. See
    the note above g_mispari_hagadol.
    """
    if n <= 0:
        return ""
    parts: List[str] = []
    hundreds = (n // 100) * 100
    if hundreds:
        if hundreds not in NUMBER_NAMES:
            return ""          # >= 600: not reachable from any milui total
        parts.append(NUMBER_NAMES[hundreds])
    rest = n % 100
    if 11 <= rest <= 19:
        parts.append(TEEN_NAMES[rest])
    else:
        tens, units = (rest // 10) * 10, rest % 10
        if tens:
            parts.append(NUMBER_NAMES[tens])
        if units:
            parts.append(NUMBER_NAMES[units])
    if not parts:
        return ""
    # Conjunctive vav on every part after the first: מאה ועשרים ואחד.
    return parts[0] + "".join(" ו" + p for p in parts[1:])


def _number_name_value(n: int) -> int:
    """Gematria of the Hebrew name of the number `n`, or 0 if unnamed."""
    return g_absolute(NUMBER_NAMES.get(n, ""))


def g_mispari(s: str) -> int:
    """Mispar HaMispari - spell each letter's VALUE as a Hebrew number-word.

    Pardes Rimonim, Gate 30 §8: "י עשרה, ועשרה עולה תקע\"ה" — yud is 'ten',
    and 'asarah' totals 575. Final forms take their base letter's value, since
    the number named is the same.
    """
    return sum(_number_name_value(STANDARD.get(_normalize_final(c), 0))
               for c in s)


def g_mispari_hagadol(s: str) -> int:
    """Mispar HaMispari HaGadol - name each letter's MILUI total.

    Pardes Rimonim, Gate 30 §9 ("ט מספריי הגדול"): "יו\"ד במילואו עשרים,
    ועשרים בגימט' כתר" — yud's milui (יוד) is 20, and עשרים is 620, which is
    כתר. Distinct from §8 Mispar HaMispari, which names the letter's STANDARD
    value; this one names its milui total.

    The Remak's example is reproduced exactly by this implementation
    (yud -> 20 -> עשרים -> 620 = כתר), and it is the only checksum the method
    has: he spells one number, and it is not a compound.

    PARTLY RECONSTRUCTED, and deliberately so. 15 of the 22 milui totals are
    compounds (alef 111, bet 412 …) which he never spells, so their orthography
    is set by `compose_number_name` rather than by him. What carries over from
    him is the §8 NUMBER_NAMES table; what does not is the joining convention
    (conjunctive vav) and the eleven-form (אחד עשר). Both are argued at
    `compose_number_name`. Precedent for shipping a reconstruction is
    ImMiluiNekudot, which likewise declares no single classical source.

    A note on scope, since it is the honest objection to this method: the Remak
    demonstrates §9 on ONE letter as an exegetical observation, not as a cipher
    for summing running text. Using it that way extends his rule further than he
    took it. The Guide says so on the method's own row.
    """
    return sum(g_absolute(compose_number_name(MILUI_VALS.get(_normalize_final(c), 0)))
               for c in s)


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
    "Mispari":          g_mispari,           # Mispar HaMispari (name each letter's value)
    "MispariHaGadol":   g_mispari_hagadol,   # Gate 30 §9 (name each letter's milui total)
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
    # Mityashev is deliberately ABSENT from this table — see g_mityashev. The
    # function, its self-tests and its word-boundary plumbing are all retained
    # so it can be reinstated the moment a source turns up; it simply is not
    # offered as one of the app's methods while it has none.
    # ── Kolel / additive ciphers ─────────────────────────────────────────────
    "KololEhad":       g_kolel_ehad,        # Kolel +1 (word as single unit)
    "KololOtiyot":     g_kolel_otiyot,      # Kolel +N (letter count)
}
CIPHER_NAMES: List[str] = list(CIPHERS.keys())

# The four methods that score vowel marks rather than letters. Defined here,
# beside CIPHER_NAMES, because the search layer needs it long before the
# breakdown helpers further down do.
NIKUD_CIPHERS = ("HaNekudot", "ImHaNekudot", "MiluiNekudot", "ImMiluiNekudot")


def nikud_partial_clause(cipher: str) -> str:
    """SQL predicate excluding units with a knowably incomplete vowel total.

    A unit whose text contains a Ksiv word the source prints unpointed has a
    vowel-mark total that is short by that word's contribution — every other
    word counted, one could not. Such a value must not appear in results at
    all: unlike a missing value it looks entirely ordinary, so a reader has no
    way to tell a genuine match from an artefact of the shortfall.

    Returns an empty string for the other methods, which are unaffected —
    they score letters, which are fully present in the Ksiv.

    Callers append this to their WHERE clause; it introduces no parameters, so
    it never disturbs the caller's parameter ordering.
    """
    return " AND nikud_partial = 0" if cipher in NIKUD_CIPHERS else ""

# ⚠️ Attested in Tanach, Chazal or ספר יצירה — the genuinely early methods, and
# the app's own Guide sources are the test. Standard is the 29th middah
# (סנהדרין ל״ח); Atbash is שֵׁשַׁךְ=בָּבֶל in ירמיהו, explicit in סנהדרין כ״ב;
# Atbach is R' Chiya in סוכה נ״ב; Albam is in ילקוט שמעוני and ספר יצירה; the
# 27-letter sequence Gadol needs is ספר יצירה ב׳:ב׳.
#
# Siduri and AchasBeta used to sit in this group and do NOT belong: both are
# פרדס רימונים (1548). Siduri's own Guide row concedes that letter-position
# counting is only "implicit" in Chazal, which is plausibility, not attestation.
TALMUD_CIPHERS: List[str] = [
    "Standard", "Gadol", "Atbash", "Albam", "Atbach",
]

# Later than Chazal but in common use, so they still lead the rest. Katan is
# ספר גימטריאות / חסידי אשכנז, 12th-13th c.; Siduri is the Remak, 1548.
# Familiarity and attestation genuinely diverge for both, and this group is
# where that shows.
COMMON_CIPHERS: List[str] = ["Katan", "Siduri"]

# Kept for anything that wants "the methods shown first".
BASIC_CIPHERS: List[str] = TALMUD_CIPHERS + COMMON_CIPHERS

# ⚠️ THE display order for every list, dropdown, chart and table in the app.
#
# CIPHER_NAMES cannot be used for this: it is the DB COLUMN order. Rows are
# inserted with a positional tuple (`tuple(vals[c] for c in CIPHER_NAMES)`)
# against `CIPHER_INSERT_COLS`, so reordering it would silently write every
# cipher value into the wrong column — a prebuilt DB would keep loading and
# every number would be wrong. It is a storage contract, not a display choice.
#
# The order: the Talmud-attested methods first, then the common-but-later ones,
# then the rest grouped by what they operate on and simplest-first within each
# group. Anything not listed falls in at the end, so a newly added cipher shows
# up rather than vanishing.
_DISPLAY_GROUPS: List[str] = TALMUD_CIPHERS + COMMON_CIPHERS + [
    # Core values — the remaining whole-number methods.
    "KatanMispari", "Mispari", "MispariHaGadol",
    # Substitution — the rest of the letter-swap ciphers. AchasBeta sits here
    # rather than in the lead group: it is פרדס רימונים (1548), the same
    # provenance as Avgad's neighbours, not Talmudic.
    "Achbi", "Avgad", "Agdat", "ReverseAvgad", "AyakBachar", "AchasBeta",
    # Positional — value depends on where the letter sits.
    "ReverseOrdinal", "Ribua", "Kidmi", "Boneeh", "HaAchor", "HaMerubahKlali",
    # Letter-name — the Milui family and its Maleh variants.
    "Milui", "Neelam", "Emtzaiyot",
    "MiluiMaleh", "NeelAmMaleh", "EmtzaiyotMaleh", "Ofanim",
    # Vowel-mark — undefined without nikud.
    "HaNekudot", "ImHaNekudot", "MiluiNekudot", "ImMiluiNekudot",
    # Kolel — the total plus a collective term.
    "KololEhad", "KololOtiyot",
]

CIPHER_DISPLAY_ORDER: List[str] = _DISPLAY_GROUPS + [
    c for c in CIPHER_NAMES if c not in _DISPLAY_GROUPS
]

# Kept as the app-view name; both views now use the same order.
APP_CIPHER_ORDER: List[str] = CIPHER_DISPLAY_ORDER


def in_display_order(ciphers) -> List[str]:
    """Sort any cipher collection into CIPHER_DISPLAY_ORDER.

    For the places that build a list from a set, a DB result or a user's
    multiselect, where the incoming order is arbitrary or selection-ordered.
    """
    rank = {c: i for i, c in enumerate(CIPHER_DISPLAY_ORDER)}
    return sorted(ciphers, key=lambda c: rank.get(c, len(rank)))

# Methods the ±1 Colel search tolerance is NOT applied to, because ±1 is
# either already built in, incoherent, or mathematically dead there:
#  - KololEhad / KololOtiyot — the kolel adjustment is the method's own
#    definition; applying the toggle too would stack the same leniency twice.
#  - KatanMispari — digital root, only 9 possible values; a ±1 window spans a
#    third of the entire value space and a "match" stops meaning anything.
#  - HaMerubahKlali — the squared grand total is not an additive sum, so
#    "count the word itself as one" has no referent: (S+1)² ≠ S² + 1.
#  - HaNekudot — every mark value is even (dot=10, line=6), so every total is
#    even and target±1 (odd) can never match another HaNekudot value.
# All other methods are additive letter-sums on which the traditional kolel
# logic operates, so the tolerance stays available for them.
COLEL_EXEMPT: frozenset = frozenset({
    "KololEhad", "KololOtiyot", "KatanMispari", "HaMerubahKlali", "HaNekudot",
})

# Ciphers excluded from correlation/balance heatmaps: KatanMispari saturates
# (only 9 distinct values → always ~100% balance), HaMerubahKlali produces
# hyperscale squared totals that break Pearson correlation and always show 0% balance.
_HEATMAP_EXCLUDE: frozenset = frozenset({"KatanMispari", "HaMerubahKlali"})

# Method counts are DERIVED, never written as literals. Two distinct numbers
# are in play and a find-and-replace on one silently corrupts the other:
#   N_CIPHERS         — the headline "N methods" figure used across the UI.
#   N_HEATMAP_CIPHERS — the correlation/balance heatmaps only, which drop
#                       _HEATMAP_EXCLUDE. Whether a new cipher belongs in that
#                       set is a judgement call per method, so this must stay
#                       derived from the frozenset rather than hardcoded.
N_CIPHERS: int = len(CIPHERS)
N_HEATMAP_CIPHERS: int = len(CIPHERS) - len(_HEATMAP_EXCLUDE)

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
    # ⚠️ Keeps the modern label deliberately. The classical name for this method
    # is מספר האחוריים, but מספר האחור is ALREADY shipped as HaAchor — a
    # different calculation (each letter × its position; חבד is 24 there and 32
    # here) sitting two rows away in the same list. Using the classical name
    # would put מספר האחור and מספר האחוריים side by side for two different
    # things. The Guide's Hebrew column records the classical name.
    "Boneeh":          "Bone'eh — מספר בונה",
    "HaAchor":         "HaAchor — מספר האחור",
    "Mispari":         "Mispari — מספר המספריי",
    "MispariHaGadol":  "Mispari HaGadol — מספריי הגדול",
    "KololEhad":       "Kolel (Word) — כולל",
    "KololOtiyot":     "Kolel (Letters) — כולל אותיות",
}

# Human-readable one-liners shown next to each cipher selector in the UI.
#
# ⚠️ NO SOURCES HERE. Attribution lives in the Guide's Source column, which is
# where a reader goes to ask "says who?". Repeating it in the results list put a
# citation in front of someone who is reading down 35 methods looking for a
# number, and half the methods carried one while half did not.
#
# Keep each to one line that answers "what does this do to my letters?", with a
# worked example only where the rule is hard to picture without one. Every
# number in these strings is verified against the implementation.
CIPHER_BLURB: Dict[str, str] = {
    "Standard":        "Standard values — א=1, ב=2 … י=10, כ=20 … ת=400. Summed.",
    "Katan":           "Reduced values — drop trailing zeros (ק→1, מ→4), then sum.",
    "Gadol":           "Like Standard but finals count higher: ך=500 … ץ=900.",
    "Siduri":          "Ordinal position: א=1, ב=2 … ת=22. Sequence, not Standard value.",
    "ReverseOrdinal":  "Reverse ordinal: ת=1, ש=2 … א=22.",
    "Ribua":           "Square each letter's Standard value, then sum: דוד = 4²+6²+4² = 68.",
    "HaMerubahKlali":  "Sum the Standard values first, then square the total: דוד = 14² = 196. "
                       "It always finds the same matches as Standard — its use is in Cross-method matches.",
    "Kidmi":           "Cumulative sum of Standard values: each letter = Σ Standard values from א up to it. א=1, ב=3, ג=6 … ת=1495.",
    "KatanMispari":    "Sum all Standard values first; then reduce to a single digital root.",
    # "Lurianic" was the last source name left, but it is doing real work here:
    # it says WHICH spelling table, and אלף vs אלפ is a different value. Kept as
    # a plain statement of the spelling used rather than as an attribution.
    "Milui":           "Spell each letter's full name and sum those letters: א=אלף=111, ד=דלת=434.",
    "Neelam":          "Like Milui but drop the first letter of each name — only the hidden remainder.",
    "Emtzaiyot":       "Middle letter: Standard value of the second letter of each Milui name (2-letter spellings). אלף→ל=30, בית→י=10 …",
    "Ofanim":          "Replace each letter with the last letter of its Milui name, take Standard value.",
    # The vowel-mark four. The full value tables are in the Guide; here just say
    # what is counted and what is not — the old versions listed all nine marks
    # and all three chatafim inline, which is a table pretending to be a
    # sentence. The dagesh note stays: it is the one thing readers query.
    "HaNekudot":        "Counts the vowel marks by shape — each dot=10, each line=6. "
                        "Kamatz=16, Patah=6, Segol=30. The dagesh is not a vowel and scores 0; "
                        "consonants and ta'amim count 0 too.",
    "ImHaNekudot":      "Standard values of the letters plus HaNekudot of their vowel marks.",
    "MiluiNekudot":     "Standard value of each vowel mark's Hebrew NAME: קמץ=230, פתח=488, סגול=99.",
    "ImMiluiNekudot":   "Standard values of the letters plus MiluiNekudot of their vowel marks.",
    "MiluiMaleh":       "Milui using Maleh (מלא) 3-letter spellings: כ=כאף=101, מ=מאם=81. Other letters unchanged.",
    "NeelAmMaleh":      "Neelam using Maleh 3-letter spellings: כ→אף=81, מ→אם=41. Other letters unchanged.",
    "EmtzaiyotMaleh":   "Middle letter using Maleh 3-letter spellings. כ and מ both yield א=1 as their inner letter.",
    "Atbash":          "Mirror swap: א↔ת, ב↔ש … then Standard values.",
    "Albam":           "ROT-11 swap: א↔ל, ב↔מ … then Standard values.",
    "Achbi":           "Reverse each half of the alphabet: א↔כ, ב↔י … ל↔ת, מ↔ש … Then Standard.",
    # ה and נ are their own partners (5+5=10, 50+50=100), so they are the only
    # letters this method leaves unchanged. Reads like a bug without the note,
    # which is why it comes before the finals detail rather than after it.
    "Atbach":          "Pairs summing to 10/100/1000: א↔ט, ב↔ח … ק↔ץ. "
                       "נ and ה pair with themselves. Finals carry 600–900.",
    "Avgad":           "+1 cyclic shift: א→ב … ת→א. Then Standard values.",
    "Agdat":           "+2 cyclic shift: א→ג … ת→ב. Then Standard values.",
    "ReverseAvgad":    "−1 cyclic shift: ב→א … א→ת. Then Standard values.",
    "AyakBachar":      "3×9 cyclic rotation: units→tens→hundreds→units (א↔י↔ק, ב↔כ↔ר …).",
    # ⚠️ "ת is unchanged" put an English clause directly after a Hebrew letter,
    # and the RTL run swallowed the boundary — it read as if the clause attached
    # to ס-ש. Naming the letter LAST keeps the sentence ending in LTR text.
    "AchasBeta":       "7/7/7 cyclic rotation across the groups א-ז / ח-נ / ס-ש. The only letter left unchanged is ת.",
    "Boneeh":          "Building value: stacked prefix sums per word (ח=8, ח+ב=10, ח+ב+ד=14 → 32). Resets per word.",
    "HaAchor":         "Each Standard value × its position in the word: דוד = 4×1 + 6×2 + 4×3 = 28. Resets per word.",
    "Mispari":         "Spell each letter's Standard value as a Hebrew number-word, then sum that word: י=10→עשרה=575.",
    "MispariHaGadol":  "Spell each letter's Milui total as a Hebrew number-word, then sum that word: י→יוד=20→עשרים=620.",
    "KololEhad":       "Standard total + 1, counting the word itself as one more unit.",
    "KololOtiyot":     "Standard total + 1 for each letter: דוד = 14 + 3 = 17.",
}

# Canonical Tanach order — Torah, Nevi'im, Ketuvim (Masoretic/Sefaria ordering)
# — not alphabetical. Plain SQL ORDER BY book sorts strings, putting Amos before
# Genesis; verified this matches the corpus's own natural insertion order
# (SELECT DISTINCT book ... ORDER BY rowid), so it is the intended sequence, not
# just a stylistic preference.
BOOK_ORDER: List[str] = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
    "Joshua", "Judges", "I Samuel", "II Samuel", "I Kings", "II Kings",
    "Isaiah", "Jeremiah", "Ezekiel",
    "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah", "Nahum",
    "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    "Psalms", "Proverbs", "Job", "Song of Songs", "Ruth", "Lamentations",
    "Ecclesiastes", "Esther", "Daniel", "Ezra", "Nehemiah",
    "I Chronicles", "II Chronicles",
]


# Display labels for the numbered books. "Kings I" reads the way this audience
# says it; "I Kings" is the academic convention. DISPLAY ONLY — `book` is a
# stored DB value and every query, sub_id and join uses the canonical form, so
# renaming it would need a full rebuild. Anything user-facing should go through
# book_label(); anything touching the DB must not.
BOOK_DISPLAY_NAMES: Dict[str, str] = {
    "I Samuel": "Samuel I", "II Samuel": "Samuel II",
    "I Kings": "Kings I", "II Kings": "Kings II",
    "I Chronicles": "Chronicles I", "II Chronicles": "Chronicles II",
}


def book_label(book: str) -> str:
    """User-facing name for a book. Canonical DB name unless remapped above."""
    return BOOK_DISPLAY_NAMES.get(book, book)


def _book_rank_sql(column: str = "Book") -> str:
    """SQL CASE expression ranking `column` by canonical Tanach order.

    `column` may be a bare column name, a table-qualified one (`u1.book`), or a
    SELECT-list alias (`Book`) — SQLite's ORDER BY may reference aliases from
    the same query. Book names are hardcoded literals defined here, not user
    input, so inlining them into the CASE expression carries no injection risk.

    Falls back to 999 for anything not in BOOK_ORDER — reachable in practice,
    not just defensive: the sidebar's "Extra Sefaria refs" can pull in a book
    outside the 39 canonical ones, and SAMPLE_CORPUS (the offline fallback used
    when tanach_corpus.jsonl is absent) uses transliterated names that don't
    match BOOK_ORDER's English ones at all. Every call site therefore adds the
    raw `column` a second time right after this expression as a tiebreaker
    (`ORDER BY {_book_rank_sql(col)}, {col}, ...`) — known books never reach
    it, since each has a unique rank, but unrecognized ones fall back to
    alphabetical *among themselves* instead of an undifferentiated tie at 999
    that would otherwise interleave them by chapter/verse alone. Follow the
    same pairing at any new call site.
    """
    cases = " ".join(f"WHEN '{b}' THEN {i}" for i, b in enumerate(BOOK_ORDER))
    return f"CASE {column} {cases} ELSE 999 END"


# Friendly display labels for variant tracks and boundary types in the UI.
TRACK_LABELS: Dict[str, str] = {
    "Ksiv":        "Written (כְּתִיב)",
    "Kri":         "Read (קְרֵי)",
    "TextVariant": "Textual variant",
    "Aggregate":   "Chapter / Sefer total",
}
BOUNDARY_LABELS: Dict[str, str] = {
    "Word":          "Word (תיבה)",
    "ZakefPhrase":   "Zakef phrase (זָקֵף — finest cantillation unit)",
    "TiphchaPhrase": "Tipcha phrase (טִפְחָא — sub-half phrase unit)",
    "FirstHalf":     "First half-verse (before Asnachta)",
    "SecondHalf":    "Second half-verse (after Asnachta)",
    "Verse":         "Verse (פסוק)",
    "Perek":         "Chapter (פרק)",
    "Sefer":      "Book (ספר)",
    "Petucha":    "Open paragraph (Pesucha פ)",
    "Setuma":     "Closed paragraph (Setuma ס)",
    # Not a stored boundary_type — a contiguous run of words found by
    # span_search, rendered through the same detail view.
    "WordSpan":   "Word span (contiguous words)",
}

# Tab 2 lists first and second half-verses together instead of offering them as
# two separate choices, so it needs its own label for that combined view. The
# split point is named explicitly (Asnachta / etnachta ֑) because "half-verse"
# alone doesn't say where the halves divide.
T2_BOUNDARY_LABELS: Dict[str, str] = {
    "BothHalves": "Half-verses (split at the Asnachta ֑)",
}
# Short per-row labels for the Half column in that combined listing.
HALF_LABELS: Dict[str, str] = {
    "FirstHalf":  "1st (before ֑)",
    "SecondHalf": "2nd (after ֑)",
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


# Hebrew cantillation marks (ta'amim), U+0591-U+05AF — verified this range is
# disjoint from HE_CONSONANTS, NIKUD_VALS, MAQAF (U+05BE) and SOF_PASUQ
# (U+05C3), so stripping it can't accidentally eat a consonant, a vowel point,
# or a word-joiner. No cipher counts ta'amim, so dropping them from a display
# echo of the query needs no note or warning — unlike nikud, which several
# ciphers genuinely score and which this function deliberately leaves alone.
_TAAMIM = frozenset(chr(cp) for cp in range(0x0591, 0x05AF + 1))


def strip_taamim(text: str) -> str:
    """Drop cantillation marks only — nikud, spaces and consonants are kept.

    For echoing a search query back to the user close to verbatim: with search
    behavior that can be hard to eyeball ("did it actually search what I
    typed?"), showing the literal input including nikud and spacing is the
    useful signal; ta'amim are visual noise no cipher reads, so they're the one
    thing silently removed.
    """
    return "".join(ch for ch in text if ch not in _TAAMIM)


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


def mark_word_span(cantillated: str, i0: int, i1: int) -> str:
    """Wrap words [i0, i1) of `cantillated` in <mark>, leaving all other
    characters (separators, maqaf, paragraph markers, sof pasuq) untouched.

    Word indices are those of tokenize_words(), so the token walk here must
    count exactly what tokenize_words() keeps — letter-less tokens and
    standalone paragraph markers are skipped without advancing the counter, or
    the highlight slides off by one.  `word_span_token_count` asserts that
    correspondence in the test suite.
    """
    marker_spans = [m.span() for m in _MARKER_STRIP_RE.finditer(cantillated)]

    def _is_marker(a: int, b: int) -> bool:
        # Fully-contained only: a marker fused onto a word (הארץ׃פ) is still one
        # counted word for tokenize_words().
        return any(s <= a and b <= e for s, e in marker_spans)

    out: List[str] = []
    wi = pos = 0
    for m in re.finditer(r"[^\s" + re.escape(MAQAF) + r"]+", cantillated):
        s, e = m.span()
        out.append(cantillated[pos:s])
        tok = m.group()
        if strip_to_consonants(tok) and not _is_marker(s, e):
            out.append(f"<mark>{tok}</mark>" if i0 <= wi < i1 else tok)
            wi += 1
        else:
            out.append(tok)
        pos = e
    out.append(cantillated[pos:])
    return "".join(out)


# Tracks that represent an actual alternative reading.  "Aggregate" is a storage
# tag for Perek/Sefer rows, not a reading tradition, so it never counts as one.
VARIANT_TRACKS = frozenset({"Kri", "TextVariant"})


def drop_self_match(df, query_unit, method: str):
    """Remove the searched unit from its own results — the trivial match.

    A unit's value always equals its own value, under every method, so a
    verse-reference search returned the searched verse among its "matches" for
    all 23,206 verses. That row carries no information.

    `query_unit` is (book, chapter, verse, boundary) or None. Suppressed ONLY
    when the row is the same unit AND the comparison is same-method, because:

      * a DIFFERENT method matching the same unit is a real finding (a verse
        whose Atbash equals its own Standard is worth seeing), and
      * a different boundary of the same verse is also real (a FirstHalf
        matching its parent Verse is not trivially true).

    Filtering happens on the frame the caller then both counts and renders, so
    the heading and the table stay in step, and row indices still address the
    frame the selection reads.
    """
    if df is None or df.empty or not query_unit:
        return df
    qb, qc, qv, qbound = query_unit
    if qbound is None or method is None:
        return df
    # A Method column exists only on the all-methods frames; when it does, a
    # row is trivial only if its own method is the query's.
    if "Method" in df.columns:
        same_method = df["Method"] == method
    else:
        same_method = True
    mask = ((df["Book"] == qb) & (df["Chapter"] == qc)
            & (df["Verse"] == qv) & (df["Boundary"] == qbound) & same_method)
    return df[~mask] if mask.any() else df


def drop_uniform_track(df, app_view: bool = False):
    """Drop the Track column unless these rows genuinely contain a variant.

    The reading tracks agree across the overwhelming majority of the corpus, so
    a Track column reading "Ksiv" on every row is noise: it poses a variant
    question where none exists.  The column earns its place only when a row on
    screen actually carries a different reading.  App view is Ksiv-only, so it
    never shows the column at all.
    """
    if "Track" not in getattr(df, "columns", []):
        return df
    if app_view or not set(df["Track"].unique()) & VARIANT_TRACKS:
        return df.drop(columns=["Track"])
    return df


# Result frames name the chapter/verse pair differently (span_search uses the
# short form), so reference-collapsing accepts either spelling.
_REF_COL_SETS = (("Book", "Chapter", "Verse"), ("Book", "Ch", "Vs"))


def _display_form(cons: str, disp: str = None, word_cons: str = "") -> str:
    """Word-spaced text for result tables, guaranteed to describe `cons`.

    Spacing makes a match legible in the table without opening the verse panel.
    `consonants` remains the unspaced form every cipher and lookup runs on, so
    the display is only ever a rendering of it — never a different reading.

    The invariant `display.replace(" ", "") == cons` is enforced here rather than
    assumed. It fails on 11 TextVariant rows whose word list and consonant string
    disagree, because the doublet fork splits half-verses at a *character* offset
    borrowed from the Ksiv text (see fork_verse) and the substitution shifts that
    offset, breaking mid-word. Those rows fall back to the unspaced form so the
    table can never show text that isn't what matched. The fork bug itself is
    still open — see HANDOFF.
    """
    candidate = disp or word_cons or cons
    return candidate if candidate.replace(" ", "") == cons else cons


def shape_result_columns(df, app_view: bool = False, drop_value: bool = False):
    """Trim a result frame for display. Row order and count are never touched,
    so dataframe selection indices still address the source frame.

    - **Parsha is always dropped**, defensively. The corpus never assigned
      parshiyot — the field held the book name on every row (571,521/571,521) —
      so the column was removed from the queries and the boundary it fed was
      renamed `Sefer` (a book total, which is what it always computed). This
      drop only catches frames built elsewhere.
    - **Value** is dropped for a single-method table, where the heading already
      states it and every row matches — but only when colel is off, since ±1
      makes the per-row value meaningful again.
    - **App view** collapses Book/Chapter/Verse into one "Amos 3:5" reference
      and drops SubID, both to save phone width.
    """
    if not hasattr(df, "columns"):
        return df
    out, drop = df, ["Parsha"]
    if drop_value:
        drop.append("Value")
    if app_view:
        drop.append("SubID")
        for book, ch, vs in _REF_COL_SETS:
            if {book, ch, vs} <= set(out.columns):
                out = out.copy()
                out.insert(0, "Reference",
                           out[book].astype(str) + " " + out[ch].astype(str)
                           + ":" + out[vs].astype(str))
                drop += [book, ch, vs]
                break
    drop = [c for c in drop if c in out.columns]
    return out.drop(columns=drop) if drop else out


# Marks dropped from the RESULTS TABLE only — meteg/silluq (05BD), rafe (05BF),
# paseq (05C0), sof pasuq (05C3), and the nun hafukha family (05C4-05C6). With
# the ta'amim these are the scribal apparatus: valuable in Verse Detail, visual
# noise stacked on nikud in a narrow table column. DISPLAY ONLY — nothing here
# touches a stored value or anything a cipher reads.
_DISPLAY_STRIP = frozenset(chr(cp) for cp in
                           (0x05BD, 0x05BF, 0x05C0, 0x05C3,
                            0x05C4, 0x05C5, 0x05C6))


def _clean_for_table(text: str) -> str:
    """Pointed text with the cantillation and scribal marks removed.

    strip_taamim() alone leaves stray metegs (הָאָֽרֶץ, וַֽיְהִי) that read as
    leftover marks rather than as part of the word, so the pointing-only marks
    go too. Verified over 4,000 verses: 0 consonants changed, 0 nikud lost,
    0 ta'amim remaining.
    """
    return "".join(ch for ch in strip_taamim(text) if ch not in _DISPLAY_STRIP)


def vocalize_result_text(df, verse_index):
    """Replace the bare `Text` column with the pointed text from the corpus.

    ⚠️ `tanach.db` stores NO pointed text — `text_display` is bare consonants
    and the nikud lives only in the corpus JSONL, which `verse_index` holds in
    memory (cached by @st.cache_resource, so this is a dict hit per row).

    96% of result rows are a WORD, phrase or half-verse rather than a whole
    verse, so most rows need their fragment located inside the parent verse's
    pointed text. Measured before building this: 3,000 samples across Word,
    ZakefPhrase, TiphchaPhrase, FirstHalf and SecondHalf located at 100%, on
    both the Ksiv and Kri tracks.

    Falls back to the bare text whenever the lookup or the location fails, so a
    miss degrades to today's behaviour rather than blanking the column. Row
    order and count are untouched — dataframe selection indices still address
    the source frame.
    """
    if not hasattr(df, "columns") or df.empty:
        return df
    if not {"Book", "Chapter", "Verse", "Text"} <= set(df.columns):
        return df

    def _point(row):
        bare = row["Text"]
        v = verse_index.get((row["Book"], int(row["Chapter"]), int(row["Verse"])))
        if v is None:
            return bare
        # A Kri match must be rendered against the Kri reading, not the Ksiv
        # text verse_index holds by default — otherwise the located fragment
        # belongs to a different reading than the one that was scored.
        src = v.text
        if row.get("Track") == "Kri" and getattr(v, "kri_text", None):
            src = v.kri_text
        cons = strip_to_consonants(bare)
        if not cons:
            return bare
        if strip_to_consonants(src) == cons:      # whole verse: use it directly
            return _clean_for_table(src)
        located = locate_vocalized(src, cons)
        # Verify before trusting: a located run whose consonants differ is the
        # wrong stretch of verse, and showing it would misattribute the text.
        if located and strip_to_consonants(located) == cons:
            return _clean_for_table(located)
        return bare

    out = df.copy()
    out["Text"] = out.apply(_point, axis=1)
    return out


def word_span_token_count(cantillated: str) -> int:
    """Number of words mark_word_span() counts — must equal len(tokenize_words())."""
    marker_spans = [m.span() for m in _MARKER_STRIP_RE.finditer(cantillated)]
    n = 0
    for m in re.finditer(r"[^\s" + re.escape(MAQAF) + r"]+", cantillated):
        s, e = m.span()
        if strip_to_consonants(m.group()) and not any(
                a <= s and e <= b for a, b in marker_spans):
            n += 1
    return n


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
    # Number of `words` belonging to the first half. Carried on the fork so the
    # half-verse word spacing is derived from this fork's own word list rather
    # than re-tokenising cantillated_text -- which for a TextVariant fork is the
    # unsubstituted text and therefore disagrees with its consonants.
    fh_word_count: int = 0


class ThreadLocalConnection:
    """Per-thread sqlite3 connections onto one shared in-memory database.

    A single connection cached with `@st.cache_resource` is shared by every
    Streamlit session *and* every script-runner thread. sqlite3 connections are
    not safe for concurrent use: overlapping queries raise
    `sqlite3.InterfaceError: bad parameter or other API misuse` and can take the
    whole process down with SIGSEGV. That is exactly how the Space died —
    `RUNTIME_ERROR`, exit code 139 — as soon as two people (or two tabs) searched
    at the same time.

    Each thread now opens its own connection. They all attach to the same
    `cache=shared` in-memory database, so the ~370 MB corpus is still built and
    held once, not per session. Attribute access proxies to this thread's
    connection, which is enough for pandas' DBAPI2 path (`cursor`, `execute`,
    `commit`, `rollback`).
    """

    def __init__(self, opener):
        self._opener = opener
        self._local = threading.local()
        self._keeper = None  # holds the shared memory DB alive; see below

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._opener()
            self._local.conn = conn
        return conn

    def __getattr__(self, name):
        # Only called for names not found normally, so _opener/_local/_keeper
        # resolve from the instance dict without recursing.
        return getattr(self._conn, name)

    def cursor(self, *a, **kw):
        return self._conn.cursor(*a, **kw)

    def execute(self, *a, **kw):
        return self._conn.execute(*a, **kw)


def raw_conn(conn):
    """Unwrap a ThreadLocalConnection to this thread's real sqlite3.Connection.

    pandas dispatches on `isinstance(con, sqlite3.Connection)`; handed a proxy it
    warns ("Other DBAPI2 objects are not tested") and takes an untested path on
    every query. Unwrapping keeps pandas on its supported path while callers
    still hold the thread-safe proxy.
    """
    return getattr(conn, "_conn", conn)


def share_in_memory(source: sqlite3.Connection) -> ThreadLocalConnection:
    """Copy `source` into a fresh shared-cache in-memory DB and hand back a
    thread-local handle onto it.

    The `keeper` connection must outlive the proxy: a `mode=memory` database is
    destroyed when its last connection closes, so dropping it would empty the
    corpus the moment a worker thread finished.
    """
    uri = f"file:gematria_{uuid.uuid4().hex[:12]}?mode=memory&cache=shared"

    def _open() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True, check_same_thread=False)

    keeper = _open()
    source.backup(keeper)
    proxy = ThreadLocalConnection(_open)
    proxy._keeper = keeper
    return proxy


def apply_doublet_to_words(words: List[str], frm: str, to: str):
    """Substitute a doublet in the first word containing it.

    Returns `(new_words, index)`, or `(words, None)` when it does not apply.

    Word-level and single-occurrence on purpose. Substituting on the
    *concatenated* consonant string can match across a word boundary or land
    inside the wrong word, while replacing in *every* matching word disagrees
    with that in turn — the two used to be done separately and drift apart.
    Deriving the fork's consonants, halves and word list from this one result
    keeps them consistent by construction.
    """
    for i, w in enumerate(words):
        if frm in w:
            return words[:i] + [w.replace(frm, to, 1)] + words[i + 1:], i
    return list(words), None


def book_slug(book: str) -> str:
    """Collision-free tag for a book name, used to build sub_id.

    The previous scheme took the first letter of each word, capped at 4 chars,
    which collapsed the 39 books into 18 tags — `J` alone covered Jeremiah, Job,
    Joel, Jonah, Joshua and Judges, so `J_4_9_Ksiv_W5` named six different rows.
    That produced 142,635 duplicate sub_ids, made the displayed SubID useless as
    an identifier, and silently merged unrelated books in anything keyed on it.
    """
    return re.sub(r"[^A-Za-z0-9]", "", book) or "Unknown"


def _base_id(v: VerseInput) -> str:
    return f"{book_slug(v.book)}_{v.chapter}_{v.verse}"


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
    fh_wc_ksiv, _ = split_halves_word_cons(v.text)
    forks.append(VerseFork(
        sub_id=f"{bid}_Ksiv", book=v.book, chapter=v.chapter, verse=v.verse,
        parsha=v.parsha, variant_track="Ksiv",
        full_consonants=strip_to_consonants(v.text),
        first_half=fh, second_half=sh, paragraph_marker=marker,
        words=tokenize_words(v.text),
        cantillated_text=v.text,
        fh_word_count=len(fh_wc_ksiv.split()),
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
                fh_word_count=len(split_halves_word_cons(v.kri_text)[0].split()),
            ))

    # --- Doublet: textual-variant alternative reading ---
    if v.doublet_from and v.doublet_to:
        doub_words, hit = apply_doublet_to_words(
            forks[0].words, v.doublet_from, v.doublet_to)
        if hit is not None:
            doub_cons = "".join(doub_words)
            # Split at the same *word index* as the Ksiv half-verse split. The
            # previous code split the substituted consonant string at the Ksiv
            # first-half **character** length; the substitution changes the
            # string's length, so that offset landed mid-word (Genesis 18:5
            # FirstHalf ended mid-word, its SecondHalf opened on the orphaned
            # final letter). A word index cannot drift, and the two halves
            # concatenate back to doub_cons by construction.
            fh_wc, _ = split_halves_word_cons(v.text)
            k = len(fh_wc.split())
            dfh = "".join(doub_words[:k])
            dsh = "".join(doub_words[k:])
            doub_text = v.text.replace(v.doublet_from, v.doublet_to, 1)
            forks.append(VerseFork(
                sub_id=f"{bid}_Variant", book=v.book, chapter=v.chapter,
                verse=v.verse, parsha=v.parsha, variant_track="TextVariant",
                full_consonants=doub_cons, first_half=dfh, second_half=dsh,
                paragraph_marker=marker,
                words=doub_words,
                cantillated_text=doub_text,
                fh_word_count=k,
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


# Sefaria writes a Ksiv/Kri divergence inline as `ksiv [kri]`: the written form
# bare, the read form in square brackets. Left as-is, tokenize_words sees TWO
# words and every cipher counts BOTH readings — Deuteronomy 7:9 scored מצותו
# (542) *and* מצותיו (552), inflating the verse and every unit containing it.
# 1,104 verses (1,279 occurrences) carry this notation.
_KRI_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
# Hebrew points and cantillation marks. Used to detect Ksiv words that Sefaria
# supplies unpointed — see KSIV_UNPOINTED_NOTE below.
_NIKUD_RANGE_RE = re.compile(r"[֑-ׇ]")

# Sefaria prints the Ksiv side of a Ksiv/Kri pair as BARE CONSONANTS: `מצותו`
# against a fully pointed `[מִצְוֺתָ֖יו]`. 1,082 of the 1,102 divergences are
# like this. That is a limitation of the source text, not of this app — the
# Masoretic Ksiv has no independent vocalisation of its own to print, since the
# vowels belong to the Kri reading.
#
# Consequence for the four vowel-mark ciphers (HaNekudot, ImHaNekudot,
# MiluiNekudot, ImMiluiNekudot): they score that word from marks that simply
# are not in the data, so it contributes 0 (or, for the Im* pair, only its
# consonantal part). Deuteronomy 7:9's Ksiv מצותו gives HaNekudot 0 while its
# Kri מצותיו gives 56. Every Ksiv verse in the pair set scores lower than its
# Kri twin on these ciphers — all 1,101 of them.
#
# This CANNOT be computed away: inventing vowels for the Ksiv would be
# fabricating text. It is surfaced instead, wherever such a unit is displayed,
# so a 0 is never read as a meaningful result.
#
# The note deliberately does NOT offer the Kri track as an alternative. The Kri
# is a different word, often with different letters (מלכם / מה־לכם), so its
# vowel-mark total answers a different question rather than supplying the
# missing value — and app view is Ksiv-only anyway, so that advice would point
# at something the reader cannot reach. The honest statement is that no
# vocalised value exists for this word, not that one lives elsewhere.
# Verses present in this corpus (tanach.us) but absent from the primary
# Masoretic witnesses, and therefore from the editions most readers hold —
# ArtScroll footnotes them as "not part of the original Masoretic text of
# Joshua", while Koren and Miqra according to the Masorah omit them outright.
# They are KEPT and scored, because the corpus includes them and silently
# dropping verses is worse than showing them, but the panel says plainly what
# they are so a total that includes them is never mistaken for undisputed.
DISPUTED_VERSES: Dict[Tuple[str, int, int], str] = {
    ("Joshua", 21, 36): "joshua-21-36-37",
    ("Joshua", 21, 37): "joshua-21-36-37",
}
DISPUTED_VERSE_NOTES: Dict[str, str] = {
    "joshua-21-36-37": (
        "**Disputed verse.** Absent from most Masoretic manuscripts (cf. "
        "I Chronicles 6:63–64); ArtScroll and Koren omit it. It is counted "
        "in the values here."
    ),
}


# Book-name aliases for parse_verse_ref. Keys are lowercased and stripped of
# spaces/punctuation by _norm_book_key, so "Song of Songs", "songofsongs" and
# "shir hashirim" all collapse to the same lookup. Hebrew names are given in
# the forms a reader would actually type, and the yeshivish transliterations
# alongside the academic ones — this audience writes "Bereishis", not "Genesis".
_BOOK_ALIASES: Dict[str, str] = {
    # Torah
    "genesis": "Genesis", "gen": "Genesis", "bereishis": "Genesis",
    "bereishit": "Genesis", "bereshit": "Genesis", "בראשית": "Genesis",
    "exodus": "Exodus", "exod": "Exodus", "ex": "Exodus", "shemos": "Exodus",
    "shemot": "Exodus", "שמות": "Exodus",
    "leviticus": "Leviticus", "lev": "Leviticus", "vayikra": "Leviticus",
    "ויקרא": "Leviticus",
    "numbers": "Numbers", "num": "Numbers", "bamidbar": "Numbers",
    "במדבר": "Numbers",
    "deuteronomy": "Deuteronomy", "deut": "Deuteronomy", "devarim": "Deuteronomy",
    "דברים": "Deuteronomy",
    # Nevi'im
    "joshua": "Joshua", "josh": "Joshua", "yehoshua": "Joshua", "יהושע": "Joshua",
    "judges": "Judges", "shoftim": "Judges", "שופטים": "Judges",
    "1samuel": "I Samuel", "1sam": "I Samuel", "1shmuel": "I Samuel",
    "1שמואל": "I Samuel",
    "2samuel": "II Samuel", "2sam": "II Samuel", "2shmuel": "II Samuel",
    "2שמואל": "II Samuel",
    "1kings": "I Kings", "1melachim": "I Kings", "1מלכים": "I Kings",
    "2kings": "II Kings", "2melachim": "II Kings", "2מלכים": "II Kings",
    "isaiah": "Isaiah", "yeshayahu": "Isaiah", "ישעיהו": "Isaiah", "ישעיה": "Isaiah",
    "jeremiah": "Jeremiah", "yirmiyahu": "Jeremiah", "ירמיהו": "Jeremiah",
    "ירמיה": "Jeremiah",
    "ezekiel": "Ezekiel", "yechezkel": "Ezekiel", "יחזקאל": "Ezekiel",
    "hosea": "Hosea", "hoshea": "Hosea", "הושע": "Hosea",
    "joel": "Joel", "yoel": "Joel", "יואל": "Joel",
    "amos": "Amos", "עמוס": "Amos",
    "obadiah": "Obadiah", "ovadiah": "Obadiah", "עובדיה": "Obadiah",
    "jonah": "Jonah", "yonah": "Jonah", "יונה": "Jonah",
    "micah": "Micah", "michah": "Micah", "מיכה": "Micah",
    "nahum": "Nahum", "nachum": "Nahum", "נחום": "Nahum",
    "habakkuk": "Habakkuk", "chavakuk": "Habakkuk", "חבקוק": "Habakkuk",
    "zephaniah": "Zephaniah", "tzefaniah": "Zephaniah", "צפניה": "Zephaniah",
    "haggai": "Haggai", "chagai": "Haggai", "חגי": "Haggai",
    "zechariah": "Zechariah", "zecharia": "Zechariah", "זכריה": "Zechariah",
    "malachi": "Malachi", "מלאכי": "Malachi",
    # Ketuvim
    "psalms": "Psalms", "psalm": "Psalms", "ps": "Psalms", "tehillim": "Psalms",
    "תהלים": "Psalms", "תהילים": "Psalms",
    "proverbs": "Proverbs", "prov": "Proverbs", "mishlei": "Proverbs",
    "משלי": "Proverbs",
    "job": "Job", "iyov": "Job", "איוב": "Job",
    "songofsongs": "Song of Songs", "shirhashirim": "Song of Songs",
    "song": "Song of Songs", "שירהשירים": "Song of Songs",
    "ruth": "Ruth", "rus": "Ruth", "רות": "Ruth",
    "lamentations": "Lamentations", "eichah": "Lamentations",
    "eicha": "Lamentations", "איכה": "Lamentations",
    "ecclesiastes": "Ecclesiastes", "koheles": "Ecclesiastes",
    "kohelet": "Ecclesiastes", "קהלת": "Ecclesiastes",
    "esther": "Esther", "esteir": "Esther", "אסתר": "Esther",
    "daniel": "Daniel", "דניאל": "Daniel",
    "ezra": "Ezra", "עזרא": "Ezra",
    "nehemiah": "Nehemiah", "nechemiah": "Nehemiah", "נחמיה": "Nehemiah",
    "1chronicles": "I Chronicles", "1chron": "I Chronicles",
    "1divreihayamim": "I Chronicles", "1דבריהימים": "I Chronicles",
    "2chronicles": "II Chronicles", "2chron": "II Chronicles",
    "2divreihayamim": "II Chronicles", "2דבריהימים": "II Chronicles",
}


def _norm_book_key(s: str) -> str:
    """Normalise a book name for alias lookup.

    Lowercases, drops spacing and punctuation, and folds the several ways a
    numbered book gets written into one key: a leading "1"/"2", "i"/"ii" or a
    trailing Hebrew א/ב all become an arabic digit, so "II Kings", "2 Kings",
    "2kings" and "מלכים ב" all reach the same entry. Digits must be KEPT (an
    earlier version stripped them, which silently broke every "2 Kings" form).
    """
    t = s.strip().lower()
    # Leading roman numerals -> digits, before punctuation is stripped.
    t = re.sub(r"^(i{1,3})[\s.]+", lambda mo: str(len(mo.group(1))) + " ", t)
    # TRAILING roman numerals too: "Kings II" is how this audience writes it
    # (and what BOOK_DISPLAY_NAMES shows), so it must parse as readily as
    # "II Kings".
    t = re.sub(r"[\s.]+(i{1,3})$", lambda mo: " " + str(len(mo.group(1))), t)
    # Trailing Hebrew alef/bet marker ("מלכים ב") -> digit.
    t = re.sub(r"[\s]*א$", " 1", t)
    t = re.sub(r"[\s]*ב$", " 2", t)
    kept = "".join(ch for ch in t
                   if ch.isalpha() or ch.isdigit() or "א" <= ch <= "ת")
    # Alias keys spell the number first ("2kings"); normalise "kings2" too.
    mo = re.match(r"^([a-zא-ת]+)([12])$", kept)
    if mo:
        kept = mo.group(2) + mo.group(1)
    return kept


def _hebrew_numeral(s: str) -> Optional[int]:
    """Read a Hebrew-letter numeral (א, טו, קכא) as an int, or None.

    Uses STANDARD letter values, so טו = 15 and טז = 16 come out right without
    special-casing — they are spelled that way precisely to avoid spelling a
    divine name, and their values already sum correctly.
    """
    s = s.strip().replace("׳", "").replace("'", "").replace("״", "").replace('"', "")
    if not s or not all("א" <= c <= "ת" for c in s):
        return None
    total = sum(STANDARD.get(FINAL_TO_BASE.get(c, c), 0) for c in s)
    return total or None


def parse_verse_ref(text: str) -> Optional[Tuple[str, int, int]]:
    """Parse a free-text reference into (canonical_book, chapter, verse).

    Accepts English, yeshivish and Hebrew book names, arabic or Hebrew-letter
    numbers, and ':' '.' or whitespace as the chapter/verse separator:

        "Genesis 1:1"  "Gen 1.1"  "bereishis 1 1"  "בראשית א:א"
        "II Kings 2:1" "2 Kings 2:1"  "מלכים ב ב:א"

    Returns None if the reference cannot be resolved. Callers must treat None
    as "not a reference" rather than an error — Tab 2's filter box, for
    example, falls back to a book-substring match.
    """
    if not text or not text.strip():
        return None
    raw = text.strip().replace("־", " ")
    # Split off the trailing "<chapter><sep><verse>" and treat the rest as the
    # book name. Scanning from the right keeps multi-word book names intact.
    mt = re.search(r"^(.*?)[\s]*([0-9]+|[א-ת׳'\"]+)\s*[:.\s]\s*"
                   r"([0-9]+|[א-ת׳'\"]+)\s*$", raw)
    if not mt:
        return None
    book_part, ch_part, vs_part = mt.group(1), mt.group(2), mt.group(3)
    if not book_part.strip():
        return None

    def _num(part: str) -> Optional[int]:
        if part.isdigit():
            return int(part) or None
        return _hebrew_numeral(part)

    chapter, verse = _num(ch_part), _num(vs_part)
    if not chapter or not verse:
        return None
    key = _norm_book_key(book_part)
    book = _BOOK_ALIASES.get(key)
    if not book:
        # A Hebrew two-part name ("מלכים ב") normalises to "מלכיםב", which the
        # alias table already holds; anything else is unresolvable.
        return None
    return (book, chapter, verse)


def disputed_verse_note(book, chapter, verse) -> str:
    """Editorial note for a verse whose presence in the text is disputed."""
    try:
        key = DISPUTED_VERSES.get((book, int(chapter), int(verse)))
    except (TypeError, ValueError):
        return ""
    return DISPUTED_VERSE_NOTES.get(key, "") if key else ""


KSIV_UNPOINTED_NOTE = (
    "Contains a Ksiv word printed without nikud (the nikud belongs to the Kri), "
    "so the four vowel-mark methods are undefined here. The other methods are "
    "unaffected.")


def has_unpointed_word(text: str) -> bool:
    """True when any word in `text` carries no vowel point or accent at all.

    Used to flag Ksiv units whose value under the vowel-mark ciphers is
    depressed because the source prints that word bare (see
    KSIV_UNPOINTED_NOTE). Words with no letters (paseq, sof pasuq, paragraph
    markers) are skipped — they are not words and are never pointed.
    """
    if not text:
        return False
    for tok in re.split(r"[\s" + re.escape(MAQAF) + r"]+", text):
        if strip_to_consonants(tok) and not _NIKUD_RANGE_RE.search(tok):
            return True
    return False
# Liturgical repetition notes: two verses (Lamentations 5:22, Ecclesiastes
# 12:14) append the preceding verse again inside `<br><small>[...]</small>` so
# the reader does not end a book on a sombre line. That is apparatus, not the
# verse, and it was being scored — and worse, the tags fused words across the
# boundary (מאד + השיבנו -> מאדהשיבנו). Removed whole, before kri parsing, so
# the bracket inside is never mistaken for a kri reading.
_LITURGICAL_NOTE_RE = re.compile(r"<br\s*/?>\s*<small>.*?</small>",
                                 re.IGNORECASE | re.DOTALL)


def split_ksiv_kri(text: str) -> Tuple[str, Optional[str]]:
    """Split Sefaria's inline `ksiv [kri]` notation into two readings.

    Returns (ksiv_text, kri_text); kri_text is None when the verse has no
    divergence, which is the overwhelming majority.

    The Ksiv reading keeps the bare word and drops the bracket; the Kri reading
    does the reverse. Both are returned as full cantillated verses so the
    existing fork engine can treat them as it treats any other variant pair —
    this function only untangles the notation, it does not decide anything about
    how the two readings are scored.

    Shapes in the real corpus, all handled here:
      * one bare word + one bracket — the normal case;
      * consecutive brackets sharing a run of bare words (Job 38:1
        `מנ הסערה [מִ֥ן ׀] [הַסְּעָרָ֗ה]`), where N ksiv words precede N
        brackets and must be matched up positionally;
      * maqaf-joined words, where a whitespace token holds two words
        (`אֶת־יעיש [יְע֥וּשׁ]` — only `יעיש` is the ksiv, `את` must survive) and
        a bracket can be fused into the token (`לך־[לְכָה־]`). Splitting on
        whitespace alone gets both of these wrong, so the scan is over
        maqaf-aware units;
      * a bracket containing a paseq `׀`, a separator rather than a word; it
        contributes no letters and is carried through harmlessly.
    """
    text = _LITURGICAL_NOTE_RE.sub("", text)
    if "[" not in text:
        return text, None

    # Split into units on whitespace AND maqaf, keeping the separators so both
    # readings can be reassembled with the original spacing/punctuation intact.
    parts = re.split(r"([\s" + re.escape(MAQAF) + r"]+)", text)
    ksiv_parts: List[str] = []
    kri_parts: List[str] = []
    # Indices into ksiv_parts/kri_parts of the word units emitted so far, so a
    # bracket run can reach back over separators to the words it replaces.
    word_slots: List[int] = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if "[" not in p:
            ksiv_parts.append(p)
            kri_parts.append(p)
            if p.strip() and strip_to_consonants(p):
                word_slots.append(len(kri_parts) - 1)
            i += 1
            continue
        # Collect one maximal run of brackets, tolerating a bracket that spans
        # parts because its content contains a space or maqaf.
        brackets: List[str] = []
        while i < len(parts) and "[" in parts[i]:
            buf = parts[i]
            i += 1
            while buf.count("[") > buf.count("]") and i < len(parts):
                buf += parts[i]
                i += 1
            brackets.append(buf)
            # Skip a separator sitting between two brackets of the same run.
            if (i < len(parts) and not parts[i].strip()
                    and i + 1 < len(parts) and "[" in parts[i + 1]):
                i += 1
        # A leading fragment before "[" belongs to the Ksiv side of this same
        # token (`לך־[לְכָה־]` → ksiv `לך־`, kri `לְכָה־`).
        lead = brackets[0][:brackets[0].index("[")]
        if lead:
            ksiv_parts.append(lead)
        # Replace this run's Ksiv word units with the bracket contents.
        take = min(len(brackets), len(word_slots))
        if take:
            for slot in word_slots[-take:]:
                kri_parts[slot] = ""
            del word_slots[-take:]
        kri_parts.append(" ".join(
            _KRI_BRACKET_RE.sub(r"\1", b[b.index("["):]) for b in brackets))

    ksiv_text = re.sub(r"\s+", " ", "".join(ksiv_parts)).strip()
    kri_text = re.sub(r"\s+", " ", "".join(kri_parts)).strip()
    return ksiv_text, (kri_text if kri_text != ksiv_text else None)


def merge_ksiv_kri_display(ksiv: str, kri: str) -> str:
    """Recombine the two readings into one line, Kri bracketed after its Ksiv.

    The inverse of split_ksiv_kri, for DISPLAY ONLY — it restores the notation
    the source uses (`ksiv [kri]`) so the panel can show one verse instead of
    repeating the whole thing to highlight one or two differing words. Nothing
    here feeds a calculation: the bracketed word never reaches `cons` or
    `w_cons`.

    Words are compared on consonants, so a Ksiv printed bare still matches its
    pointed Kri counterpart.

    The two readings need not have the same word count: one Ksiv word can be
    read as several (Isaiah 3:15 מלכם → מַה־לָּכֶם, Psalms 55:16 ישימות →
    יַשִּׁי מָוֶת) and vice versa. Alignment therefore walks both sequences and,
    at a divergence, brackets the whole run of Kri words that stands in for the
    run of Ksiv words — `ישימות [יַשִּׁי מָוֶת]` — rather than requiring a
    one-to-one match. Resynchronisation is by finding the next word the two
    readings agree on.
    """
    if not ksiv or not kri:
        return ""
    k_toks = [t for t in ksiv.split(" ") if t]
    q_toks = [t for t in kri.split(" ") if t]
    kc = [strip_to_consonants(t) for t in k_toks]
    qc = [strip_to_consonants(t) for t in q_toks]

    out: List[str] = []
    i = j = 0
    while i < len(k_toks) and j < len(q_toks):
        if kc[i] == qc[j]:
            out.append(k_toks[i])
            i += 1
            j += 1
            continue
        # Divergence: find the next point where the readings agree again, so a
        # 1→2 (or 2→1) substitution is bracketed as a single run.
        anchor = None
        for di in range(i, min(i + 4, len(k_toks))):
            for dj in range(j, min(j + 4, len(q_toks))):
                if kc[di] and kc[di] == qc[dj] and (di, dj) != (i, j):
                    anchor = (di, dj)
                    break
            if anchor:
                break
        ni, nj = anchor if anchor else (len(k_toks), len(q_toks))
        k_run = " ".join(k_toks[i:ni]) or ""
        q_run = " ".join(q_toks[j:nj]) or ""
        if k_run:
            out.append(k_run)
        if q_run:
            out.append(f"[{q_run}]")
        i, j = ni, nj
    # Trailing remainder on either side (rare; keeps the line complete).
    if i < len(k_toks):
        out.append(" ".join(k_toks[i:]))
    if j < len(q_toks):
        out.append(f"[{' '.join(q_toks[j:])}]")
    return " ".join(x for x in out if x)


def load_from_jsonl(path: pathlib.Path = CORPUS_FILE) -> List[VerseInput]:
    """Load the pre-fetched full Tanach corpus from a local JSONL file.

    Each line: {"book": str, "chapter": int, "verse": int, "text": str}
    Returns an empty list if the file does not exist.

    Verses carrying Sefaria's inline `ksiv [kri]` notation are split here, so
    `text` is the written reading alone and `kri_text` carries the read one.
    The fork engine then emits a proper Kri track for them, instead of the
    ciphers silently summing both readings into one inflated Ksiv value.
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
            ksiv_text, kri_text = split_ksiv_kri(row["text"])
            verses.append(VerseInput(
                book=row["book"],
                chapter=int(row["chapter"]),
                verse=int(row["verse"]),
                parsha=row["book"],
                text=ksiv_text,
                kri_text=kri_text,
            ))
    return verses


ENGLISH_FILE = pathlib.Path(__file__).parent / "tanach_english.jsonl"

# Attribution for the bundled translation. The 1985 JPS is licensed CC-BY-NC,
# and attribution is a *condition* of that licence, not a courtesy — so
# Attribution is rendered wherever the English appears (detail panel,
# print-out, Guide), including in exported documents that leave the site.
# The edition name is deliberately NOT repeated in the "English" headings above
# those blocks: the attribution line sits directly beneath and already carries
# it. Kept as a named constant because it identifies which text is bundled and
# is the thing to change alongside VERSION in fetch_english.py on a swap.
ENGLISH_VERSION_LABEL = "Koren Jerusalem Bible"
ENGLISH_ATTRIBUTION = ("English: The Koren Jerusalem Bible, © Koren "
                       "Publishers Jerusalem, via Sefaria. "
                       "Licensed CC BY-NC 4.0.")
# Short form for the on-screen panel. The full notice above stays on the
# EXPORT: a printed or downloaded document travels away from the site, so the
# licence has to travel with it, whereas on screen the Guide's "Texts &
# licences" section and the checkbox tooltip are a click away and the full
# string was crowding a caption that appears under every affected verse.
ENGLISH_ATTRIBUTION_SHORT = "© Koren Publishers Jerusalem · CC BY-NC"


def load_english(path: pathlib.Path = ENGLISH_FILE) -> Dict[Tuple[str, int, int], str]:
    """Load the bundled English translation as {(book, chapter, verse): text}.

    Deliberately a plain dict rather than a table in tanach.db: the translation
    plays no part in any gematria computation, so it has no business in the
    unit index. Keeping it beside the DB means a translation refresh never
    forces a rebuild of the 23,206-verse cipher table.

    Returns {} when the file is absent, which is a supported state — every
    caller degrades to "translation unavailable" rather than failing.
    """
    if not path.exists():
        return {}
    out: Dict[Tuple[str, int, int], str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                out[(row["book"], int(row["chapter"]), int(row["verse"]))] = row["en"]
    except (OSError, ValueError, KeyError):
        # A truncated or malformed sidecar must not take the app down; the
        # feature is additive, so partial data is better than a hard failure.
        return out
    return out


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
            -- 1 when this unit's own text contains a word the source prints
            -- without vowel points (the Ksiv side of a Ksiv/Kri pair). Such a
            -- unit's four vowel-mark totals are not merely unknown, they are
            -- knowably short: every other word contributed and that one did
            -- not. Recorded once here so all thirteen query paths can exclude
            -- them with a single predicate instead of each re-deriving it.
            nikud_partial  INTEGER DEFAULT 0,
            {CIPHER_COLS}
        )
    """)

    def insert(sub_id, book, chapter, verse, parsha, boundary, track, cons,
               disp=None, cantillated="", word_cons=""):
        if not cons:
            return
        # Judged on the unit's OWN cantillated text, never the parent verse's:
        # a pointed word sitting beside a bare one is itself perfectly valid,
        # and flagging by verse would condemn ~16,000 sound Word units to
        # protect ~1,300.
        partial = 1 if has_unpointed_word(cantillated or "") else 0
        cur.execute(
            f"""INSERT INTO units
                (sub_id, book, chapter, verse, parsha, boundary_type,
                 variant_track, consonants, text_display, nikud_partial,
                 {CIPHER_INSERT_COLS})
                VALUES (?,?,?,?,?,?,?,?,?,?,{CIPHER_PLACEHOLDERS})""",
            (sub_id, book, chapter, verse, parsha, boundary, track, cons,
             _display_form(cons, disp, word_cons), partial,
             *_cipher_tuple(cons, cantillated, word_cons)),
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
        fh_wc = " ".join(f.words[:f.fh_word_count])
        sh_wc = " ".join(f.words[f.fh_word_count:])
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

    # ---- Macro structures: Perek, Sefer (Ksiv track aggregation) ----
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
            # Aggregates carried no cantillated text, so their vowel-mark
            # totals were 0 for every chapter and book in the corpus — not a
            # partial value, an absent one. Joining the members' cantillated
            # text gives these units real vowel-mark totals, and lets the same
            # unpointed-word test apply to them as to everything else.
            cant_agg = " ".join(m.cantillated_text for m in members)
            insert(id_fn(key, sample), sample.book,
                   sample.chapter if boundary_name == "Perek" else 0,
                   0, sample.parsha, boundary_name, "Aggregate", cons,
                   cantillated=cant_agg, word_cons=word_cons_agg)

    aggregate(lambda f: (f.book, f.chapter), "Perek",
              lambda k, s: f"PEREK_{book_slug(k[0])}_{k[1]}")
    aggregate(lambda f: (f.book,), "Sefer",
              lambda k, s: f"SEFER_{book_slug(k[0])}")

    # ---- Paragraph blocks: accumulate verses until a marker closes a block ----
    block: List[VerseFork] = []
    block_n = 0
    for f in sorted(ksiv, key=lambda m: (m.book, m.chapter, m.verse)):
        block.append(f)
        if f.paragraph_marker:
            block_n += 1
            cons = "".join(m.full_consonants for m in block)
            word_cons_block = " ".join(w for m in block for w in m.words)
            # Same fix as the Perek/Sefer aggregates above: without this a
            # paragraph block's vowel-mark totals were all 0.
            cant_block = " ".join(m.cantillated_text for m in block)
            insert(f"BLOCK_{f.paragraph_marker}_{block_n}", block[0].book,
                   block[0].chapter, block[0].verse, block[0].parsha,
                   f.paragraph_marker, "Aggregate", cons,
                   cantillated=cant_block, word_cons=word_cons_block)
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
                # Each side is guarded by its OWN method: a pattern is only as
                # sound as both halves of it, so an incomplete vowel total on
                # either side would make the "balance" an artefact.
                + nikud_partial_clause(ma).replace("nikud_partial", "u1.nikud_partial")
                + nikud_partial_clause(mb).replace("nikud_partial", "u2.nikud_partial") +
                f" AND u1.{ma} >= ? AND u2.{mb} >= ? "
                f"AND ABS(u1.{ma} - u2.{mb}) <= ? "
                f"ORDER BY {_book_rank_sql('u1.book')}, u1.book, u1.chapter, u1.verse LIMIT ?",
                raw_conn(conn), params=[min_value, min_value, tol, limit],
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
            # Both verses of the pair, same method on each side.
            + nikud_partial_clause(m).replace("nikud_partial", "u1.nikud_partial")
            + nikud_partial_clause(m).replace("nikud_partial", "u2.nikud_partial") +
            f" AND u1.{m} >= ? AND ABS(u1.{m} - u2.{m}) <= ? "
            f"ORDER BY {_book_rank_sql('u1.book')}, u1.book, u1.chapter, u1.verse LIMIT ?",
            raw_conn(conn), params=[min_value, tol, limit],
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
                + nikud_partial_clause(ma).replace("nikud_partial", "u1.nikud_partial")
                + nikud_partial_clause(mb).replace("nikud_partial", "u2.nikud_partial") +
                f" AND u1.{ma} >= ? AND u2.{mb} >= ? "
                "AND u1.rowid != u2.rowid "
                "ORDER BY " + _book_rank_sql("u1.book") + ", u1.book, u1.chapter, u1.verse, u2.rowid "
                "LIMIT ?",
                raw_conn(conn), params=[boundary, boundary, min_value, min_value, limit],
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
    if colel and cipher not in COLEL_EXEMPT:
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
    # Units with an incomplete vowel total never enter a vowel-method result.
    if cipher in NIKUD_CIPHERS:
        where.append("nikud_partial = 0")
    sql = (f"SELECT book AS Book, chapter AS Chapter, verse AS Verse, "
           f"boundary_type AS Boundary, variant_track AS Track, "
           f"text_display AS Text, {cipher} AS Value, sub_id AS SubID "
           f"FROM units WHERE " + " AND ".join(where) +
           f" ORDER BY ABS({cipher} - ?), {_book_rank_sql('Book')}, Book, Chapter, Verse LIMIT ?")
    params += [value, limit]
    return pd.read_sql_query(sql, raw_conn(conn), params=params)


def count_value(conn: sqlite3.Connection, cipher: str, value: int,
                colel: bool = False,
                tracks: Optional[List[str]] = None,
                boundaries: Optional[List[str]] = None) -> int:
    """Exact match count (no LIMIT) — used for coincidence-rate denominators."""
    where, params = [], []
    if colel and cipher not in COLEL_EXEMPT:
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
    # Must mirror search_value's exclusion exactly: this count is the numerator
    # for coincidence rates, so counting units the search itself will never
    # return would report a rate for results the reader cannot see.
    if cipher in NIKUD_CIPHERS:
        where.append("nikud_partial = 0")
    sql = "SELECT COUNT(*) FROM units WHERE " + " AND ".join(where)
    return int(pd.read_sql_query(sql, raw_conn(conn), params=params).iloc[0, 0])


def boundary_population(conn: sqlite3.Connection,
                        tracks: Optional[List[str]] = None,
                        boundaries: Optional[List[str]] = None,
                        cipher: Optional[str] = None) -> int:
    """Total units matching the given track/boundary filters — the denominator.

    `cipher` narrows the population to the units that method can actually
    return. It matters only for the four vowel-mark methods, where the excluded
    units would otherwise inflate the denominator and depress every rarity
    figure computed against it.
    """
    where, params = [], []
    if tracks:
        where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
        params += tracks
    if boundaries:
        where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
        params += boundaries
    if cipher in NIKUD_CIPHERS:
        where.append("nikud_partial = 0")
    sql = "SELECT COUNT(*) FROM units" + (" WHERE " + " AND ".join(where) if where else "")
    return int(pd.read_sql_query(sql, raw_conn(conn), params=params).iloc[0, 0])


def search_phrase(conn: sqlite3.Connection, phrase_consonants: str,
                  cantillated: str = "",
                  word_consonants: str = "",
                  colel: bool = False, tracks: Optional[List[str]] = None,
                  boundaries: Optional[List[str]] = None) -> Dict[str, object]:
    """Compute every cipher value for the input phrase and search each one."""
    values = compute_all_ciphers(phrase_consonants, cantillated, word_consonants)
    results = {c: search_value(conn, c, values[c], colel, tracks, boundaries)
               for c in CIPHER_DISPLAY_ORDER}
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
        if colel and c not in COLEL_EXEMPT:
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
        # Per-branch, so the four vowel-mark branches drop incomplete units
        # while the other branches are untouched.
        if c in NIKUD_CIPHERS:
            where.append("nikud_partial = 0")
        unions.append(
            f"SELECT * FROM ("
            f"SELECT '{c}' AS Method, book AS Book, chapter AS Chapter, "
            f"verse AS Verse, boundary_type AS Boundary, variant_track AS Track, "
            f"text_display AS Text, {c} AS Value, sub_id AS SubID "
            f"FROM units WHERE " + " AND ".join(where) +
            f" LIMIT {int(limit_per_method)})"
        )
        params += branch_params
    # Rows are grouped by method in the output, so this ORDER BY is a
    # user-visible sequence, not an internal one.
    method_order = (
        "CASE Method " +
        " ".join(f"WHEN ? THEN {i}" for i, _ in enumerate(CIPHER_DISPLAY_ORDER)) +
        " ELSE 9999 END"
    )
    sql = ("SELECT * FROM (" + " UNION ALL ".join(unions) +
           f") ORDER BY {method_order}, ABS(Value - ?), {_book_rank_sql('Book')}, Book, Chapter, Verse")
    params += list(CIPHER_DISPLAY_ORDER)
    params.append(value)
    return pd.read_sql_query(sql, raw_conn(conn), params=params)


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
    cross_verse: bool = False,
) -> pd.DataFrame:
    """Find every contiguous multi-word span (2..max_span words) whose `cipher`
    value equals `target` (or target±1 if colel).  Works for every cipher
    because they all compose additively across words.

    Returns a DataFrame with columns: Book, Ch, Vs, Track, Words, <cipher>,
    plus half-open word offsets `_w0`/`_w1` (0-indexed, `_w1` exclusive) for the
    detail renderer.  The offsets index into tokenize_words() of the track's
    source text — Word units are built from it (see verse_forks) — so they stay
    aligned with the DB row order this scan walks.  Underscore-prefixed columns
    are internal and dropped before display.

    `cross_verse=False` (the default) confines every span to one verse, which is
    what the sof-pasuq means: the verse is a real boundary in the text, not an
    artefact of how it is stored.  `cross_verse=True` additionally returns spans
    that straddle a verse boundary, marked `_cross=True` and carrying `_end_ch`/
    `_end_vs` for the verse they end in.  Those rows are a superset — the
    within-verse spans are still present and still marked `_cross=False` — so
    the caller can label rather than re-run.

    Two things the cross-verse walk must not do, both load-bearing:
      * It may only bridge verses that are genuinely consecutive in the corpus.
        The TextVariant track holds ~7 scattered verses (Genesis 18:5, Genesis
        24:55, Numbers 31:2 …); streaming it naively would invent adjacencies
        between verses that are nowhere near each other.  Bridges are therefore
        allowed only between verses adjacent in the same chapter, or across a
        chapter seam within one book.
      * It never bridges books.  The end of Deuteronomy is not adjacent to the
        start of Joshua in any sense a reader would accept.
    """
    import numpy as _np

    track_cond = ""
    params: list = []
    if tracks:
        track_cond = "AND variant_track IN (%s)" % ",".join("?" * len(tracks))
        params = list(tracks)

    # A span is a sum over Word units, so one unpointed word poisons every span
    # that covers it. Excluding those words here removes them from BOTH the
    # within-verse and cross-verse walks — and, because a dropped word would
    # otherwise silently close the gap between its neighbours, the surviving
    # words are no longer treated as adjacent either (see the guard below).
    sql = (
        f"SELECT book, chapter, verse, variant_track, {cipher}, nikud_partial "
        f"FROM units WHERE boundary_type='Word' {track_cond} "
        f"ORDER BY {_book_rank_sql('book')}, book, chapter, verse, variant_track, rowid"
    )
    df = pd.read_sql_query(sql, raw_conn(conn), params=params)
    if df.empty:
        return pd.DataFrame()

    target_set = _np.array(
        [target - 1, target, target + 1] if colel else [target], dtype=_np.int64
    )

    # Rows are NOT deleted for an unpointed word: `_w0`/`_w1` are indices into
    # tokenize_words(), so removing a row would shift every later index and the
    # detail panel would highlight the wrong words. Instead the word is left in
    # place and any window covering it is rejected below, via a prefix sum over
    # the flag — a window is admissible only when it contains zero flagged
    # words. For the 30 letter-based methods nothing is ever rejected.
    _guard = cipher in NIKUD_CIPHERS
    rows = []
    for (book, ch, vs, track), grp in df.groupby(
        ["book", "chapter", "verse", "variant_track"], sort=False
    ):
        vals = grp[cipher].to_numpy(dtype=_np.int64)
        bad = grp["nikud_partial"].to_numpy(dtype=_np.int64)
        bad_prefix = _np.concatenate([[0], _np.cumsum(bad)])
        n = len(vals)
        if n < 2:
            continue
        prefix = _np.concatenate([[0], _np.cumsum(vals)])
        for span_len in range(2, min(max_span + 1, n + 1)):
            span_vals = prefix[span_len:] - prefix[: n - span_len + 1]
            hits = _np.where(_np.isin(span_vals, target_set))[0]
            if _guard and len(hits):
                # Keep only windows containing no unpointed word.
                covered = bad_prefix[hits + span_len] - bad_prefix[hits]
                hits = hits[covered == 0]
            for i in hits:
                rows.append({
                    "Book":  book,
                    "Ch":    int(ch),
                    "Vs":    int(vs),
                    "Track": track,
                    "Words": f"{int(i)+1}–{int(i)+span_len}",
                    cipher:  int(span_vals[i]),
                    "_w0":   int(i),
                    "_w1":   int(i) + span_len,
                    "_cross":  False,
                    "_end_ch": int(ch),
                    "_end_vs": int(vs),
                })

    if cross_verse:
        rows.extend(_cross_verse_spans(df, cipher, max_span, target_set,
                                       guard_nikud=_guard))

    return pd.DataFrame(rows)


def _verses_are_consecutive(prev, nxt) -> bool:
    """True when verse `nxt` directly follows `prev` in the same book.

    Accepts the next verse in the same chapter, or the first verse of the next
    chapter. Deliberately strict: any gap means the two verses are not adjacent
    in the text and must not be bridged, which is what keeps the sparse
    TextVariant track (7 scattered verses) from producing invented adjacencies.
    Never bridges books.
    """
    (p_book, p_ch, p_vs), (n_book, n_ch, n_vs) = prev, nxt
    if p_book != n_book:
        return False
    if p_ch == n_ch:
        return n_vs == p_vs + 1
    return n_ch == p_ch + 1 and n_vs == 1


def _cross_verse_spans(df, cipher: str, max_span: int, target_set,
                       guard_nikud: bool = False):
    """Spans that straddle a verse boundary, for span_search(cross_verse=True).

    Only spans that actually cross are returned; the within-verse ones are
    produced by the caller's own walk. Windows are built over a run of
    consecutive verses, then a window is kept only when it spans more than one
    verse — so a span sitting wholly inside one verse of the run is skipped
    rather than duplicated.
    """
    import numpy as _np

    out = []
    # One stream per track: the tracks are alternative readings of the text, so
    # a span may never mix words from Ksiv and TextVariant.
    for track, tgrp in df.groupby("variant_track", sort=False):
        verses = list(tgrp.groupby(["book", "chapter", "verse"], sort=False))

        # Split the track into maximal runs of genuinely consecutive verses,
        # then scan each run once, end to end. An earlier version walked
        # bounded windows with an overlapping tail and double-emitted every
        # span that fell inside the overlap; the whole-run scan has no overlap
        # to get wrong, so each window is visited exactly once by construction.
        # Cost is bounded by max_span, not by run length: the inner loop is
        # O(run_words x max_span) either way, and prefix sums make each window
        # O(1). A whole book is ~30K words, so this stays well inside a second.
        runs: List[list] = []
        for key, grp in verses:
            if runs and _verses_are_consecutive(runs[-1][-1][0], key):
                runs[-1].append((key, grp))
            else:
                runs.append([(key, grp)])

        for run in runs:
            if len(run) < 2:
                continue            # nothing to cross into
            vals = _np.concatenate([g[cipher].to_numpy(dtype=_np.int64)
                                    for _, g in run])
            # Word index -> which verse of the run it belongs to, so a hit is
            # reported against the verse it starts in, with an offset local to
            # that verse (what the detail renderer expects).
            starts, acc = [], 0
            for _, g in run:
                starts.append(acc)
                acc += len(g)
            owner = _np.zeros(acc, dtype=_np.int64)
            for vi, s in enumerate(starts):
                owner[s:] = vi
            n = len(vals)
            prefix = _np.concatenate([[0], _np.cumsum(vals)])
            # Same rejection rule as the within-verse walk above.
            bad_run = _np.concatenate([g["nikud_partial"].to_numpy(dtype=_np.int64)
                                       for _, g in run])
            bad_prefix = _np.concatenate([[0], _np.cumsum(bad_run)])
            for span_len in range(2, min(max_span + 1, n + 1)):
                span_vals = prefix[span_len:] - prefix[: n - span_len + 1]
                hits = _np.where(_np.isin(span_vals, target_set))[0]
                if guard_nikud and len(hits):
                    covered = bad_prefix[hits + span_len] - bad_prefix[hits]
                    hits = hits[covered == 0]
                for i in hits:
                    i = int(i)
                    v0, v1 = int(owner[i]), int(owner[i + span_len - 1])
                    if v0 == v1:
                        continue        # wholly inside one verse: not ours
                    (b, c0, s0), _ = run[v0]
                    (_, c1, s1), _ = run[v1]
                    local = i - starts[v0]
                    out.append({
                        "Book":  b,
                        "Ch":    int(c0),
                        "Vs":    int(s0),
                        "Track": track,
                        # Word numbers are relative to the starting verse, so
                        # the range runs past that verse's word count — the
                        # display marks these rows as crossing, and the detail
                        # panel renders the whole run.
                        "Words": f"{local+1}–{local+span_len}",
                        cipher:  int(span_vals[i]),
                        "_w0":   local,
                        "_w1":   local + span_len,
                        "_cross":  True,
                        "_end_ch": int(c1),
                        "_end_vs": int(s1),
                    })
    return out


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
    # ⚠️ The SQL is built POSITIONALLY: the nested loops below and the read-back
    # must walk the same list in the same order, or every cell lands in the
    # wrong row/column. Display order is used throughout so the matrix comes out
    # grouped like every other list; changing one of these four references
    # without the others silently transposes the results.
    cases = []
    for ma in CIPHER_DISPLAY_ORDER:
        v = int(a_vals[ma])
        for mb in CIPHER_DISPLAY_ORDER:
            cond = (f"{mb} BETWEEN {v - 1} AND {v + 1}"
                    if colel and mb not in COLEL_EXEMPT
                    else f"{mb} = {v}")
            # ⚠️ Per-COLUMN, not in the WHERE clause. A unit with a knowably
            # short vowel total must be excluded from the four vowel-mark
            # columns, but it is a perfectly good row for the other 31 — a
            # WHERE would drop it from every column and understate them.
            # Without this the matrix reported 1,364 matches for a bare query
            # under HaNekudot where the true count is 1: the query scores 0,
            # and so does every unit whose vowels could not all be counted.
            cond += nikud_partial_clause(mb)
            cases.append(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)")
    sql = f"SELECT {', '.join(cases)} FROM units {where_clause}"
    row = pd.read_sql_query(sql, raw_conn(conn), params=params).iloc[0]
    n = len(CIPHER_DISPLAY_ORDER)
    matrix_rows = {}
    for i, ma in enumerate(CIPHER_DISPLAY_ORDER):
        matrix_rows[f"{ma} ({a_vals[ma]})"] = [
            int(row.iloc[i * n + j]) for j in range(n)
        ]
    return pd.DataFrame.from_dict(matrix_rows, orient="index",
                                  columns=CIPHER_DISPLAY_ORDER)


# ---------------------------------------------------------------------------
# SECTION 8.  STATISTICS & VISUALIZATION HELPERS
# ---------------------------------------------------------------------------

def structure_frame(conn: sqlite3.Connection, *boundaries: str,
                    track: str = "Ksiv") -> pd.DataFrame:
    """Units of one or more boundary types, on a single variant track.

    Accepts several boundaries so Tab 2 can list first and second half-verses
    together in one frame (they are separate rows in `units`, not a single
    combined boundary). Ordered by book/chapter/verse and then by boundary, so
    a verse's two halves land adjacent and in reading order rather than being
    interleaved arbitrarily by insertion order.
    """
    if not boundaries:
        raise ValueError("structure_frame requires at least one boundary")
    # Perek/Sefer rows are stored on the Aggregate track; the flag is keyed off
    # the boundary, so mixing an aggregate boundary with a per-track one in a
    # single call would need two different tracks and is refused rather than
    # silently returning half the rows.
    aggregate = [b for b in boundaries if b in ("Perek", "Sefer")]
    if aggregate and len(aggregate) != len(boundaries):
        raise ValueError("cannot mix Perek/Sefer with per-track boundaries")
    trk = "Aggregate" if aggregate else track
    placeholders = ",".join("?" * len(boundaries))
    # CASE keeps FirstHalf before SecondHalf without relying on alphabetical
    # order of the type names (which happens to agree here, but would not for
    # any other pair we might combine later).
    order_case = " ".join(
        f"WHEN ? THEN {i}" for i in range(len(boundaries)))
    return pd.read_sql_query(
        f"SELECT * FROM units "
        f"WHERE boundary_type IN ({placeholders}) AND variant_track=? "
        f"ORDER BY {_book_rank_sql('book')}, book, chapter, verse, "
        f"CASE boundary_type {order_case} ELSE 99 END",
        raw_conn(conn),
        params=[*boundaries, trk, *boundaries])


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

# Display names for the vowel marks, so a nikud breakdown can say which mark it
# counted rather than printing a bare combining character.
# Display names for the breakdown panel. Spellings match NEKUDA_NAME_VALS, which
# follows the Remak's usage in שער כ"ח — otherwise a row would show one spelling
# and score another. No דגש entry: it is not a nekuda and never gets a row.
#
# The chatafim carry their conventional name AND the string actually summed,
# because those differ: the row is labelled "חטף פתח" but the value is שבא+פתח.
# Showing only the conventional name would leave the number unexplained.
NEKUDA_NAMES: Dict[str, str] = {
    "ְ": "שבא",
    "ֱ": "חטף סגול", "ֲ": "חטף פתח", "ֳ": "חטף קמץ",
    "ִ": "חירק", "ֵ": "צירי", "ֶ": "סגול", "ַ": "פתח", "ָ": "קמץ",
    "ֹ": "חולם", "ֺ": "חולם חסר", "ֻ": "קובוץ",
}

# What a chataf row is actually made of, appended to its name in the breakdown.
# Both methods treat a chataf as sheva + base, but they combine DIFFERENT things
# — the geometric one adds the marks (20+6), the milui one adds the names
# (שבא+פתח) — so the parenthetical has to say which, or the row implies the
# wrong arithmetic.
CHATAF_PARTS: Dict[str, Tuple[str, str]] = {
    "ֱ": ("שבא + סגול", "שבא סגול"),
    "ֲ": ("שבא + פתח",  "שבא פתח"),
    "ֳ": ("שבא + קמץ",  "שבא קמץ"),
}

# NIKUD_CIPHERS moved up beside CIPHER_NAMES — the search layer needs it there.


def locate_vocalized(cantillated: str, matched_cons: str) -> str:
    """Find the vocalised text of a sub-unit, given only its bare consonants.

    Result rows store `consonants` / `text_display` with no nikud, so feeding
    `matched_text` to the vowel-mark ciphers scored them as 0 — the detail panel
    contradicted the very search that produced the row. Recovering the pointed
    text from the parent verse fixes the values and lets the breakdown name each
    mark. Matches the shortest consecutive run of words whose consonants equal
    `matched_cons`; returns "" when nothing lines up, so callers can fall back.
    """
    if not cantillated or not matched_cons:
        return ""
    raw = _tokenize_raw_words(cantillated)
    cons = [strip_to_consonants(w) for w in raw]
    for i in range(len(raw)):
        acc = ""
        for j in range(i, len(raw)):
            acc += cons[j]
            if acc == matched_cons:
                return " ".join(raw[i:j + 1])
            if len(acc) >= len(matched_cons):
                break
    return ""


def derivation_steps(cipher: str, consonants: str) -> Optional[List[Tuple[str, str]]]:
    """Return [(label, value)] showing HOW a non-per-letter total is reached.

    The four ciphers `cipher_breakdown` returns None for are not opaque — each
    is a named operation on the Standard total, so each has work to show:

        KatanMispari    reduce the total to its digital root
        HaMerubahKlali  square the total
        KololEhad       add 1 for the unit
        KololOtiyot     add the letter count

    The printout used to say "No per-letter breakdown for this method" and stop
    at the total, which is true but useless: showing the work IS the point of a
    printout, and "Total value: 8" alone gives the reader nothing to check.
    """
    if not consonants:
        return None
    base = g_absolute(consonants)
    if cipher == "KatanMispari":
        steps = [("Standard total", str(base))]
        cur = base
        while cur >= 10:
            digits = " + ".join(str(d) for d in str(cur))
            cur = sum(int(d) for d in str(cur))
            steps.append((f"Reduce {digits}", str(cur)))
        steps.append(("Digital root", str(cur)))
        return steps
    if cipher == "HaMerubahKlali":
        return [("Standard total", str(base)),
                (f"Squared {base} × {base}", str(base ** 2))]
    if cipher == "KololEhad":
        return [("Standard total", str(base)),
                ("Plus 1 for the unit itself", str(base + 1))]
    if cipher == "KololOtiyot":
        n = sum(1 for c in consonants
                if STANDARD.get(_normalize_final(c), 0))
        return [("Standard total", str(base)),
                (f"Plus {n} letters", str(base + n))]
    return None


def nikud_breakdown(cipher: str, cantillated: str,
                    consonants: str = "") -> Optional[List[Tuple[str, int]]]:
    """Per-mark breakdown for the vowel-mark ciphers.

    HaNekudot / ImHaNekudot count each mark's geometry (dot=10, line=6);
    MiluiNekudot / ImMiluiNekudot count the gematria of the mark's Hebrew *name*
    (Gikatilla, Ginnat Egoz). The Im* pair also sum Standard over the letters, so
    those letters are listed first — matching how `compute_all_ciphers` builds
    the total. Marks are shown on ◌ (U+25CC) so a combining character is legible.
    """
    if cipher not in NIKUD_CIPHERS or not cantillated:
        return None
    table = (NEKUDA_NAME_VALS
             if cipher in ("MiluiNekudot", "ImMiluiNekudot") else NIKUD_VALS)
    rows: List[Tuple[str, int]] = []
    if cipher.startswith("Im"):
        for ch in consonants:
            val = STANDARD.get(_normalize_final(ch), 0)
            if val:
                rows.append((ch, val))
    milui = cipher in ("MiluiNekudot", "ImMiluiNekudot")
    for i, ch in enumerate(cantillated):
        if ch == DAGESH_OR_SHURUK:
            # U+05BC is a shuruk or a dagesh depending on position. A dagesh is
            # not a nekuda and contributes nothing, so it must not appear as a
            # row — a zero row would imply it was counted and came to nothing.
            if not is_shuruk(cantillated, i):
                continue
            val = SHURUK_NAME_VAL if milui else SHURUK_VAL
            rows.append((f"◌{ch} שורק", val))
            continue
        val = table.get(ch)
        if val:
            name = NEKUDA_NAMES.get(ch, "")
            parts = CHATAF_PARTS.get(ch)
            if parts:
                # Name first, then what was actually summed — the reader sees
                # "חטף פתח (שבא + פתח)" beside 26, or "(שבא פתח)" beside 791.
                name = f"{name} ({parts[1] if milui else parts[0]})"
            rows.append((f"◌{ch} {name}".strip(), val))
    return rows or None


def cipher_breakdown(cipher: str, consonants: str,
                     word_consonants: str = "",
                     cantillated: str = "") -> Optional[List[Tuple[str, int]]]:
    """Return [(display_label, letter_value)] for equation display in the UI.

    Returns None for ciphers with no letter-level breakdown (nikud ciphers,
    KatanMispari, HaMerubahKlali, KololEhad, KololOtiyot) or empty input.
    word_consonants (space-separated) drives word-boundary-aware ciphers.
    """
    # Vowel-mark ciphers do have a breakdown — per mark, not per letter.
    if cipher in NIKUD_CIPHERS:
        return nikud_breakdown(cipher, cantillated, consonants)
    _NO_BREAKDOWN = {"KatanMispari", "HaMerubahKlali", "KololEhad", "KololOtiyot"}
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
            result.append((f"{ch}→{sw}", GADOL_FINALS.get(sw, STANDARD.get(_normalize_final(sw), 0))))
        elif cipher == "AchasBeta":
            sw = ACHAS_BETA_MAP.get(base, base)
            result.append((f"{ch}→{sw}", STANDARD.get(_normalize_final(sw), 0)))
        elif cipher == "Mispari":
            # Name the letter's STANDARD value: י → "עשרה" → 575.
            name = NUMBER_NAMES.get(v_std, "")
            result.append((f"{ch}={v_std}→{name}", g_absolute(name)))
        elif cipher == "MispariHaGadol":
            # Name the letter's MILUI total: י → יוד=20 → "עשרים" → 620.
            mv = MILUI_VALS.get(base, 0)
            name = compose_number_name(mv)
            result.append((f"{ch}={mv}→{name}", g_absolute(name)))
        else:
            result.append((ch, 0))
    return result


# ---------------------------------------------------------------------------
# SECTION 9.  PRINT / EXPORT  (build_print_html)
# ---------------------------------------------------------------------------

def build_print_html(query_info, match_info, breakdown_rows, active_method,
                     colel, vals, query_breakdown=None, query_val=None,
                     match_nikud_unreliable=False, english="",
                     english_is_full_verse=False, ksiv_unpointed=False,
                     kri_display="", query_english="",
                     query_english_is_full_verse=False,
                     query_disputed_note="", query_method=None,
                     derivation=None, query_derivation=None) -> str:
    """Return a self-contained HTML document suitable for window.print().

    `breakdown_rows`/`vals` describe the *matched* corpus text. `query_breakdown`/
    `query_val` (added later) describe the user's own searched word under the
    same method — previously the print-out only showed how the match arrived at
    its value, never how the user's own input did, even though that is the
    other half of "these two are equal." When both are present the two
    calculation sections are labelled "Your Word" / "Matched Text" to
    disambiguate; with only the match breakdown (e.g. Gematria-value-mode
    prints, which have no query word) the heading is unchanged.

    `match_nikud_unreliable` (added later, code review): true when the matched
    unit's pointed text couldn't be located in its verse, so a vowel-mark
    cipher's value for it was computed without nikud. Without this, the export
    could show a "Your Word" total contradicting an uncaveated "Matched Text"
    total (e.g. 50 vs 0) with no explanation anywhere in the document.

    `english` is the bundled translation, included only when the reader ticked
    the panel's checkbox — the export mirrors the panel rather than always
    carrying it. `english_is_full_verse` marks the case where the unit is a
    word/phrase/half-verse but the translation necessarily covers the whole
    verse (there is no word-level alignment to the Hebrew), so the heading says
    so instead of implying the English renders just the highlighted span.
    """
    import html as _h
    from datetime import date as _d
    e = _h.escape

    _NIKUD_SET = {"HaNekudot", "ImHaNekudot", "MiluiNekudot", "ImMiluiNekudot"}
    _GLOSS = {
        "Word": "single word",
        "FirstHalf": "first half (up to the etnachta ֑)",
        "SecondHalf": "second half (after the etnachta ֑)",
        "TiphchaPhrase": "phrase up to the tiphcha ֖",
        "ZakefPhrase": "phrase up to the zakef ֔",
        "Verse": "full verse (פסוק)",
        "Petucha": "open paragraph (פ Petucha)",
        "Setuma": "closed paragraph (ס Setuma)",
        "Perek": "chapter (פרק Perek)",
        "Sefer": "Book (ספר Sefer)",
    }

    today   = _d.today().strftime("%Y-%m-%d")
    method  = e(CIPHER_DISPLAY_NAMES.get(active_method, active_method))
    # A cross-method drill-down scores the query and the match under DIFFERENT
    # methods, so each side must be labelled with its own. Falls back to the
    # match's method, which is right for every other call site.
    _qm_name = query_method or active_method
    q_method = e(CIPHER_DISPLAY_NAMES.get(_qm_name, _qm_name))
    _cross_method = bool(query_method) and query_method != active_method
    # None (not 0) when the caller withheld this method's value — a unit whose
    # vowel total is incomplete has its four NIKUD_CIPHERS keys removed from
    # `vals`, and defaulting to 0 here would print the very number the
    # exclusion exists to suppress.
    val     = (vals.get(active_method) if vals else None)
    # Withheld here rather than by the caller stripping `vals`: the panel now
    # keeps the real numbers so it can render "—" in place of them, so the
    # export has to apply the same rule itself.
    if ksiv_unpointed and active_method in NIKUD_CIPHERS:
        val = None
    val_txt = "—" if val is None else str(val)
    boundary = match_info.get("boundary", "Verse")
    book    = e(str(match_info.get("book", "")))
    ch      = e(str(match_info.get("chapter", "")))
    vs      = e(str(match_info.get("verse", "")))
    gloss   = e(_GLOSS.get(boundary, boundary))
    # convert screen <mark> to print-safe highlight span
    hl_verse = (match_info.get("highlighted_html", "") or "")
    hl_verse = hl_verse.replace("<mark>", '<span class="hl">').replace("</mark>", "</span>")

    # ── Section 1: query ─────────────────────────────────────────────────────
    if query_info:
        # `cons` (the stripped consonant string) is deliberately NOT shown.
        # It repeated the Text row above in a machine-readable form, and the
        # match side had no equivalent — so the document asserted an asymmetry
        # it could not justify. The letter-by-letter breakdown below already
        # shows every letter that was counted, which is what a reader needs.
        raw = e(query_info.get("raw", ""))
        # The query's own value, not the matched unit's — under colel they can
        # differ by 1, and labelling the match's value as "Value" under
        # "Search Query" conflated the two.
        _val_shown = query_val if query_val is not None else val_txt
        # Tab 2 reaches this with a *selected corpus unit* rather than a typed
        # query, and passes a label saying so; "Search Query" / "Input" would
        # otherwise describe something the reader never entered.
        _q_label = query_info.get("label") or ""
        _sec1_title = e(_q_label) if _q_label else "Search Query"
        _input_row_label = "Text" if _q_label else "Input"
        # The query's own translation. Escaped for the same reason the match's
        # is: corpus text must never inject tags. Attribution is NOT emitted
        # here — see the document-level notice.
        if query_english:
            # This block sits inside the query section, whose title already
            # names the unit, so it needs no reference of its own — but it must
            # not read identically to the match's block further down.
            _q_en_label = ("English — search query, full verse"
                           if query_english_is_full_verse
                           else "English — search query")
            _q_en_block = (f'<p class="en-label">{e(_q_en_label)}</p>'
                           f'<div class="en">{e(query_english)}</div>')
        else:
            _q_en_block = ""
        # A disputed-verse note about the QUERY belongs with the query. The
        # matched unit's note is rendered by its own caller; this one has no
        # other home, and a total that includes a disputed verse must say so
        # in the document, not only on screen.
        _q_disp = (f'<p class="fn">{e(query_disputed_note)}</p>'
                   if query_disputed_note else "")
        # State the two-method comparison outright. Without it the document
        # shows one method here and a different one over the match, which reads
        # as an inconsistency rather than the cross-method claim it is.
        _cross_note = (f'<p class="fn">Cross-method comparison: the search '
                       f'term is scored under {q_method}, and the matched text '
                       f'under {method}.</p>') if _cross_method else ""
        sec1 = f"""
<div class="sec">
  <div class="sec-title">{_sec1_title}</div>
  <table class="kv">
    <tr><td class="kl">{_input_row_label}</td><td class="kv-val rtl">{raw}</td></tr>
    <tr><td class="kl">Method</td><td class="kv-val">{q_method}</td></tr>
    <tr><td class="kl">Value</td><td class="kv-val big">{_val_shown}</td></tr>
  </table>
  {_cross_note}
  {_q_en_block}
  {_q_disp}
</div>"""
    else:
        sec1 = ""

    # ── Section 2: source match ───────────────────────────────────────────────
    # The translation is escaped, unlike the Hebrew line above it: hl_verse is
    # markup we generated (the <mark> highlight), whereas `english` is corpus
    # text and must never be able to inject tags into the export.
    if english:
        # Edition name omitted here for the same reason as on screen: the
        # attribution carries it. NOTE: the attribution is deliberately NOT
        # emitted here — it is rendered once at document level below, because
        # an export can now carry TWO translations (query + match) and the
        # CC-BY-NC notice must appear exactly once, not per block.
        # Say "match" only when a query translation is also in the document;
        # on its own the Source Text section title is enough.
        _m_word = "English — match" if query_english else "English"
        _en_label = (f"{_m_word}, full verse" if english_is_full_verse
                     else _m_word)
        en_block = (f'<p class="en-label">{e(_en_label)}</p>'
                    f'<div class="en">{e(english)}</div>')
    else:
        en_block = ""
    # Ksiv/Kri line, escaped: unlike hl_verse (markup we generated) this is
    # corpus text. Shown without the <mark> highlight, since nothing in it was
    # scored — the Kri is reference, and the Ksiv is already marked above.
    if kri_display:
        kri_block = (f'<p class="en-label">With Kri (read form in brackets)</p>'
                     f'<div class="verse rtl">{e(kri_display)}</div>'
                     f'<p class="fn">Bracketed = Kri, not counted. '
                     f'Values follow the Ksiv.</p>')
    else:
        kri_block = ""
    # Colel is mentioned ONLY when it actually affected this calculation.
    # There used to be a "Colel (±1): Not applied" row in the query section,
    # which put a line about colel on every print-out including the ones where
    # it did nothing. Joshua's rule: mention it when the calculation uses it,
    # otherwise keep it out of the document entirely.
    #
    # So: nothing when colel is off, and nothing when it is on but exempt for
    # this method (KololEhad/KololOtiyot/KatanMispari/HaMerubahKlali/HaNekudot
    # match exactly regardless). The note earns its place only in the case it
    # explains — where a printed match can differ from the searched value by 1
    # and would otherwise read as an arithmetic error.
    if colel and active_method not in COLEL_EXEMPT:
        colel_note = ('<p class="fn"><strong>כולל (±1) applied.</strong> '
                      'Matches include values one above and one below the '
                      'target as well as the exact value, so a match here may '
                      'differ from the searched value by 1.</p>')
    else:
        colel_note = ""
    sec2 = f"""
<div class="sec">
  <div class="sec-title">Source Text</div>
  <p class="ref"><strong>{book} {ch}:{vs}</strong> &nbsp;·&nbsp; <em class="gloss">{gloss}</em></p>
  <div class="verse rtl">{hl_verse}</div>
  {kri_block}
  {en_block}
  {colel_note}
</div>"""

    # ── Query and match accuracy warnings ────────────────────────────────────
    is_nikud = active_method in _NIKUD_SET
    # Query-side: the QUERY's raw input has no nikud under a vowel-mark method.
    # This used to render inside what is now the *matched-text* section (sec3)
    # — a placement left over from before "Your Word" (sec1b) existed, warning
    # about the wrong half of the document. It now sits with the query, and is
    # NOT nested inside sec1b, because a query with no nikud at all has no
    # breakdown rows either (nikud_breakdown returns None), so sec1b would
    # never render and the warning would never be seen.
    no_nikud = bool(query_info) and not any("ְ" <= c <= "ׇ"
                                            for c in query_info.get("raw", ""))
    sec1_warn = ('<div class="warn">⚠ This cipher counts vowel marks only. '
                'The input has no nikud — value is 0. '
                'Re-enter with vowel points for a meaningful result.</div>'
                if is_nikud and no_nikud else "")
    # Match-side: the matched unit's pointed text couldn't be located in its
    # verse, so its vowel-mark value was computed without nikud (often 0).
    # Found in code review: this used to be an on-screen-only caption gated
    # behind `if not app_view:` (app-view users saw an unexplained value with
    # no warning) and never reached the printed/downloaded document at all —
    # so even on the site, exporting hid the caveat the screen showed.
    match_nikud_warn = ("<div class=\"warn\">⚠ This match's pointed (vocalised) "
                        "text could not be located in its verse. Vowel-mark "
                        "methods for the matched text are computed without "
                        "nikud, so this total may not reflect the actual "
                        "reading.</div>"
                        if is_nikud and match_nikud_unreliable else "")
    # A separate condition from the one above: there the pointed text could not
    # be found; here it was found and the source itself prints it unpointed.
    # Both make the same total untrustworthy, so both have to reach the export.
    if is_nikud and ksiv_unpointed:
        match_nikud_warn += f'<div class="warn">⚠ {e(KSIV_UNPOINTED_NOTE)}</div>'
        # Enforced here, not merely upstream: a per-mark breakdown sums to the
        # very total being withheld (Genesis 8:17 rebuilt HaNekudot=922 from 61
        # rows), so any caller that hands over breakdown rows for a vowel method
        # on an unpointed unit would leak the number into the export. The panel
        # already suppresses these, but the document must not depend on that.
        breakdown_rows = None
        query_breakdown = None

    def _breakdown_table(rows) -> str:
        """Render one method's letter/mark breakdown as an HTML table."""
        has_sep = any("→" in lbl or "=" in lbl or "↔" in lbl for lbl, _ in rows)
        if has_sep:
            # Detect column header labels by cipher
            if active_method in ("Milui", "MiluiMaleh"):
                h1, h2 = "אות", "שם מלא"
            elif active_method in ("Neelam", "NeelAmMaleh"):
                h1, h2 = "אות", "נסתר"
            elif active_method in ("Emtzaiyot", "EmtzaiyotMaleh"):
                h1, h2 = "אות", "אמצעי"
            elif active_method == "AyakBachar":
                h1, h2 = "אות", "מוחלף (מאות)"
            else:
                h1, h2 = "אות", "מוחלף"
            body = ""
            for lbl, v in rows:
                for sep in ("=", "→", "↔"):
                    if sep in lbl:
                        p = lbl.split(sep, 1)
                        body += (f"<tr><td class='rtl'>{e(p[0])}</td>"
                                 f"<td class='rtl'>{e(p[1])}</td>"
                                 f"<td class='num'>{v}</td></tr>")
                        break
                else:
                    body += (f"<tr><td class='rtl' colspan='2'>{e(lbl)}</td>"
                             f"<td class='num'>{v}</td></tr>")
            total = sum(v for _, v in rows)
            foot = (f"<tfoot><tr><td colspan='2' class='rtl'><strong>סה״כ</strong></td>"
                    f"<td class='num'><strong>{total}</strong></td></tr></tfoot>")
            return (f"<table class='bd'><thead><tr>"
                   f"<th class='rtl'>{h1}</th><th class='rtl'>{h2}</th><th>ערך</th>"
                   f"</tr></thead><tbody>{body}</tbody>{foot}</table>")
        body = "".join(f"<tr><td class='rtl'>{e(lbl)}</td>"
                       f"<td class='num'>{v}</td></tr>" for lbl, v in rows)
        total = sum(v for _, v in rows)
        foot = (f"<tfoot><tr><td class='rtl'><strong>סה״כ</strong></td>"
                f"<td class='num'><strong>{total}</strong></td></tr></tfoot>")
        return (f"<table class='bd'><thead><tr>"
               f"<th class='rtl'>אות</th><th>ערך</th>"
               f"</tr></thead><tbody>{body}</tbody>{foot}</table>")

    # Section 1b, ahead of the source match: how the *user's own word* arrives
    # at its value. Only rendered when there is a real query word (not
    # Gematria-value-mode prints, which pass query_breakdown=None) and the
    # method has a letter/mark breakdown at all.
    if query_breakdown or query_derivation:
        # Matches the section-1 heading: "Your Word" is right for a typed Tab 1
        # search, wrong for a Tab 2 unit the reader selected from the table.
        _bd_label = e((query_info or {}).get("label") or "") or "Your Word"
        # ⚠️ The derived methods (KatanMispari, HaMerubahKlali, KololEhad,
        # KololOtiyot) have no per-letter rows, so gating this section on
        # query_breakdown alone made the whole "Your Word" calculation VANISH
        # for them — the document showed how the match reached its value but
        # never how the reader's own word did, which is the other half of
        # "these two are equal".
        if query_breakdown:
            _qbody = _breakdown_table(query_breakdown)
        else:
            _qsteps = "".join(
                f'<tr><td class="dl">{lbl}</td><td class="dv">{v}</td></tr>'
                for lbl, v in query_derivation)
            _qbody = f'<table class="deriv">{_qsteps}</table>'
        sec1b = f"""
<div class="sec">
  <div class="sec-title">Calculation — {_bd_label} ({q_method})</div>
  {_qbody}
</div>"""
    else:
        sec1b = ""

    # Disambiguate from Section 1b when both are present; otherwise this is the
    # only calculation section and keeps its original, unqualified heading.
    sec3_title = f"Calculation — Matched Text ({method})" if sec1b else f"Calculation — {method}"

    if breakdown_rows:
        note = ('<p class="fn">* AyakBachar maps to the hundreds tier — '
                'final forms (ך ם ן ף ץ) carry 500–900.</p>'
                if active_method == "AyakBachar" else "")
        sec3 = f"""
<div class="sec">
  <div class="sec-title">{sec3_title}</div>
  {match_nikud_warn}{_breakdown_table(breakdown_rows)}{note}
</div>"""
    elif derivation:
        # ⚠️ Showing the work IS the point of a printout. This branch used to
        # print "Total value: 8" and a note saying there was no breakdown —
        # true only in the narrow sense that the total is not a per-LETTER sum.
        # Katan Mispari reduces the Standard total to its digital root, and
        # those steps are exactly what a reader needs to check the number.
        _steps = "".join(
            f'<tr><td class="dl">{lbl}</td><td class="dv">{v}</td></tr>'
            for lbl, v in derivation)
        sec3 = f"""
<div class="sec">
  <div class="sec-title">{sec3_title}</div>
  {match_nikud_warn}
  <table class="deriv">{_steps}</table>
  <p>Total value: <strong>{val_txt}</strong></p>
  <p class="fn">This method derives its total from the Standard sum rather than
  from a value per letter, so the steps above are the calculation in full.</p>
</div>"""
    else:
        sec3 = f"""
<div class="sec">
  <div class="sec-title">{sec3_title}</div>
  {match_nikud_warn}
  <p>Total value: <strong>{val_txt}</strong></p>
  <p class="fn">No per-letter breakdown for this method — its total is not a sum
  over letters (digital root, squared total, or a kolel that adds a single term).</p>
</div>"""

    css = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+Hebrew:wght@400;700&display=swap');
@page{size:A4 portrait;margin:14mm}
*{box-sizing:border-box}
body{font-family:'Noto Serif Hebrew','SBL Hebrew','Times New Roman',serif;
     font-size:12pt;color:#000;background:#fff;margin:0;padding:10px;direction:ltr}
.rtl{direction:rtl;unicode-bidi:isolate}
.ph{display:flex;justify-content:space-between;border-bottom:2px solid #000;
    padding-bottom:5px;margin-bottom:14px;font-size:9pt}
.pf{border-top:1px solid #000;margin-top:14px;padding-top:3px;
    font-size:8pt;color:#555}
/* No break-inside:avoid here — see table.bd. Keeping a whole section together
   is only desirable when it fits on a page; a 60-row breakdown does not, and
   forcing it whole left large blank areas. The section title is kept with the
   content that follows it instead, which is the part that actually matters. */
.sec{margin-bottom:16px}
.sec-title{font-size:12pt;font-weight:bold;border-bottom:1px solid #000;
           margin-bottom:6px;padding-bottom:2px;
           /* Never let a page break fall between a heading and its table. */
           break-after:avoid;page-break-after:avoid}
.kv{width:100%;border-collapse:collapse}
.kv td{padding:2px 6px;vertical-align:top}
.kl{width:38%;font-weight:bold;color:#333}
.big{font-size:18pt;font-weight:bold}
.ref{font-size:11pt;margin:3px 0}
.gloss{color:#444;font-size:9pt}
.verse{font-size:15pt;line-height:2.4;border:1px solid #bbb;
       padding:8px 14px;margin:6px 0;text-align:right;direction:rtl}
.en-label{font-size:9pt;font-weight:bold;color:#333;margin:8px 0 2px}
.en{font-size:11pt;line-height:1.55;direction:ltr;text-align:left;
    border-left:3px solid #bbb;padding:4px 0 4px 10px;margin:0 0 4px;
    /* A cross-verse span's translation is several verses separated by blank
       lines; without this the export would run them together. */
    white-space:pre-line}
.hl{background:#ddd;text-decoration:underline;border:1px solid #000;
    padding:0 2px;-webkit-box-decoration-break:clone;box-decoration-break:clone}
/* Breakdown tables are read as Hebrew: the first column (אות) belongs on the
   RIGHT and the value column (ערך) on the left. The cells are emitted in
   logical order, so one direction rule flips the whole table rather than
   reordering every row by hand. Numeric cells stay LTR via .num below, so the
   digits themselves are unaffected. */
table.bd{width:100%;border-collapse:collapse;margin-top:6px;direction:rtl}
table.bd thead th{background:#f0f0f0;border:1px solid #000;padding:4px 7px;font-weight:bold}
table.bd tbody td{border:1px solid #bbb;padding:3px 7px}
table.bd tfoot td{border-top:3px double #000;padding:4px 7px}
/* A long breakdown table cannot fit in what is left of the page, so
   break-inside:avoid on its section pushed the ENTIRE section to the next
   sheet and left most of the current one blank. Sections may now split across
   pages; the header row repeats (thead display:table-header-group, below) so a
   continued table stays readable, and rows themselves never split. */
table.bd tr{break-inside:avoid;page-break-inside:avoid}
.num{text-align:center;direction:ltr}
.warn{border:2px solid #000;padding:7px 10px;font-weight:bold;margin-bottom:8px}
/* Derivation steps for the four methods with no per-letter breakdown. LTR:
   these rows are English labels and numerals, unlike table.bd which is RTL. */
table.deriv{border-collapse:collapse;margin:6px 0 8px;direction:ltr}
table.deriv td{border:0;padding:2px 0}
table.deriv td.dl{padding-right:14px;color:#333}
table.deriv td.dv{font-weight:bold;text-align:right;font-variant-numeric:tabular-nums}
table.deriv tr:last-child td{border-top:1px solid #000;padding-top:4px}
.fn{font-size:9pt;color:#555;font-style:italic;margin-top:5px}
@media print{
  *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
  thead{display:table-header-group}
  /* The header SHOULD repeat on a continued table; the total must not. A
     tfoot defaults to table-footer-group, which browsers repeat at the foot of
     every page a table spans — so a table split across two sheets printed
     "סה״כ" twice, once under a section that carries on overleaf. Demoting it to
     an ordinary row group prints it once, at the true end of the table. */
  tfoot{display:table-row-group}
  body{padding:0}
}"""

    # CC-BY-NC attribution, rendered exactly ONCE per document. It used to sit
    # inside the match's `if english:` block; now that an export can carry two
    # translations (query and match), emitting it per block would print the
    # licence notice twice. Attribution is a condition of the licence, so it
    # renders whenever either translation is present.
    _en_attrib = (f'<p class="fn">{e(ENGLISH_ATTRIBUTION)}</p>'
                  if (english or query_english) else "")

    return f"""<!DOCTYPE html>
<html lang="he">
<head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gematria — {book} {ch}:{vs}</title>
<style>{css}</style></head>
<body>
<div class="ph"><span><strong>Tanach Gematria Engine</strong></span><span>{today}</span></div>
{sec1}{sec1_warn}{sec1b}{sec2}{sec3}{_en_attrib}
<div class="pf">Generated by Tanach Gematria Search &amp; Structural Pattern Engine</div>
<script>
window.onload=function(){{
  document.fonts.ready.then(function(){{window.focus();window.print();}});
}};
</script>
</body></html>"""


# SECTION 10.  SELF-TEST  (python app.py selftest)
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
    #   sheva(20) + tsere(20) + hiriq(10) = 50. The dagesh in the bet scores
    #   NOTHING — it is not a nekuda (Etz Chaim Sha'ar 5) — and the taam is
    #   excluded. This asserted 60 while the dagesh was counted.
    assert g_hanekudot("שלום") == 0, g_hanekudot("שלום")
    assert g_hanekudot("בְּרֵאשִׁ֖ית") == 50, g_hanekudot("בְּרֵאשִׁ֖ית")
    assert g_hanekudot(SAMPLE_CORPUS[0].text) > 0
    # The shuruk/dagesh split: U+05BC is both, told apart by position. תֹהוּ is
    # holam(10) + shuruk(10) = 20 — the shuruk COUNTS; בְּרֵאשִׁית above shows
    # the dagesh does not. Pin both directions so neither can regress.
    assert is_shuruk("תֹהוּ", "תֹהוּ".index(DAGESH_OR_SHURUK)) is True
    assert is_shuruk("בָּרָא", "בָּרָא".index(DAGESH_OR_SHURUK)) is False
    assert g_hanekudot("תֹהוּ") == 20, g_hanekudot("תֹהוּ")
    # Chatafim are sheva + base, per the Ramak's own composition in Pardes
    # Rimonim שער כ"ח ch.1 ("וג' מורכבות, שבא קמץ, שבא פתח, שבא סגול"):
    #   hataf patah = 20+6 = 26, hataf kamatz = 20+16 = 36, hataf segol = 20+30 = 50.
    # They used to score as the bare base vowel, i.e. as if the sheva were absent.
    assert NIKUD_VALS["ֲ"] == NIKUD_VALS["ְ"] + NIKUD_VALS["ַ"] == 26
    assert NIKUD_VALS["ֳ"] == NIKUD_VALS["ְ"] + NIKUD_VALS["ָ"] == 36
    assert NIKUD_VALS["ֱ"] == NIKUD_VALS["ְ"] + NIKUD_VALS["ֶ"] == 50
    # MiluiNekudot (Gikatilla spellings): בְּרֵאשִׁ֖ית —
    #   sheva(שבא=303) + tsere(צירי=310) + hiriq(חירק=318) = 931.
    # The dagesh contributes nothing here either: it has no vowel-name to sum.
    assert g_milui_nekudot("שלום") == 0, g_milui_nekudot("שלום")
    assert g_milui_nekudot("בְּרֵאשִׁ֖ית") == 931, g_milui_nekudot("בְּרֵאשִׁ֖ית")
    assert SHURUK_NAME_VAL == _spelling_val("שורק")
    # Spellings follow the Remak's usage in שער כ"ח, the only vowel-name
    # orthography that can actually be checked (Ginnat Egoz is unavailable).
    # חיריק appears ZERO times in that gate; צירי outnumbers צרי 26 to 3.
    assert NEKUDA_NAME_VALS["ִ"] == _spelling_val("חירק") == 318
    assert NEKUDA_NAME_VALS["ֵ"] == _spelling_val("צירי") == 310
    # Chatafim are sheva + base in BOTH nikud methods — the geometric one adds
    # the marks, this one adds the names. Scoring a chataf as its bare base
    # vowel here while counting its sheva there was the inconsistency.
    for _hat, _base in (("ֲ", "ַ"), ("ֳ", "ָ"), ("ֱ", "ֶ")):
        assert (NEKUDA_NAME_VALS[_hat]
                == NEKUDA_NAME_VALS["ְ"] + NEKUDA_NAME_VALS[_base]), _hat
    assert NEKUDA_NAME_VALS["ֲ"] == 791, NEKUDA_NAME_VALS["ֲ"]
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
    assert g_ayak_bachar("נ") == 500,        g_ayak_bachar("נ")       # נ→ך (hundreds tier, Gadol=500)
    assert g_ayak_bachar("ע") == 700,        g_ayak_bachar("ע")       # ע→ן (hundreds tier, Gadol=700)
    assert g_ofanim(emet) == 126,             g_ofanim(emet)           # א→פ(80)+מ→מ(40)+ת→ו(6)
    assert g_achas_beta(emet) == 608,         g_achas_beta(emet)       # א→ח(8)+מ→ר(200)+ת→ת(400)
    assert g_reverse_avgad(emet) == 730,      g_reverse_avgad(emet)    # א→ת(400)+מ→ל(30)+ת→ש(300)
    # Word-boundary reset: HaAchor/Mityashev/Boneeh must reset counters between words.
    two_words = "אמת שלום"
    assert g_haachor(two_words) == 1819,      g_haachor(two_words)     # אמת:1281 + שלום:538
    assert g_mityashev(two_words) == 2827,    g_mityashev(two_words)   # אמת:1323 + שלום:1504
    assert g_boneeh(two_words) == 1825,       g_boneeh(two_words)      # אמת:483 + שלום:1342
    # Gate 30 §9 — the Remak's own worked example is the ONLY checksum this
    # method has, so pin it: yud's milui יוד = 20, and עשרים = 620 = כתר.
    assert MILUI_VALS["י"] == 20,             MILUI_VALS["י"]
    assert compose_number_name(20) == "עשרים", compose_number_name(20)
    assert g_mispari_hagadol("י") == 620,     g_mispari_hagadol("י")
    assert g_absolute("כתר") == 620,          g_absolute("כתר")
    # Composition invariant: every composed name must denote the number it
    # was built from, so a spelling slip cannot pass silently.
    _inv = {v: k for k, v in NUMBER_NAMES.items()}
    _inv.update({v: k for k, v in TEEN_NAMES.items()})
    for _ltr, _mv in MILUI_VALS.items():
        _nm = compose_number_name(_mv)
        _sum = sum(_inv[_p.strip()] for _p in _nm.split(" ו") if _p.strip() in _inv)
        assert _sum == _mv, (_ltr, _mv, _nm, _sum)
    # Reference parser: English, yeshivish, Hebrew; arabic and Hebrew numerals.
    assert parse_verse_ref("Genesis 1:1") == ("Genesis", 1, 1)
    assert parse_verse_ref("gen 1.1") == ("Genesis", 1, 1)
    assert parse_verse_ref("Bereishis 1 1") == ("Genesis", 1, 1)
    assert parse_verse_ref("בראשית א:א") == ("Genesis", 1, 1)
    assert parse_verse_ref("II Kings 2:1") == ("II Kings", 2, 1)
    assert parse_verse_ref("2 Kings 2:1") == ("II Kings", 2, 1)
    assert parse_verse_ref("מלכים ב ב:א") == ("II Kings", 2, 1)
    assert parse_verse_ref("Tehillim 119:1") == ("Psalms", 119, 1)
    # טו/טז are spelled to avoid a divine name; their values still sum right.
    assert parse_verse_ref("שמות טו:א") == ("Exodus", 15, 1)
    assert parse_verse_ref("Song of Songs 1:1") == ("Song of Songs", 1, 1)
    # Non-references must return None, not raise — Tab 2's filter falls back
    # to a book-substring match on None.
    for _bad in ("", "   ", "Genesis", "not a ref", "Zzz 1:1", "Genesis 0:1"):
        assert parse_verse_ref(_bad) is None, _bad
    # Every alias must name a real book, and every canonical book name must
    # parse back to itself — otherwise a book becomes unreachable by typing.
    for _b in set(_BOOK_ALIASES.values()):
        assert parse_verse_ref(f"{_b} 1:1") == (_b, 1, 1), _b
    # The DISPLAY spelling must parse too: the UI shows "Kings II", so a reader
    # copying what they see has to get the same verse as "II Kings".
    assert parse_verse_ref("Kings II 2:1") == ("II Kings", 2, 1)
    assert parse_verse_ref("Samuel I 1:1") == ("I Samuel", 1, 1)
    for _b, _lbl in BOOK_DISPLAY_NAMES.items():
        assert parse_verse_ref(f"{_lbl} 1:1") == (_b, 1, 1), _lbl
        assert _b in BOOK_ORDER, _b
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

    ext = extremes_table(conn, ["Verse", "Perek", "Sefer", "Petucha", "Setuma"])
    print("  Extremes table:")
    print(ext.to_string(index=False))
    print("\n=== ALL SELF-TESTS PASSED ===")
    conn.close()


# ---------------------------------------------------------------------------
# SECTION 10.  STREAMLIT USER INTERFACE
# ---------------------------------------------------------------------------

_PWA_HEAD_SNIPPET = (
    '<link rel="manifest" href="./app/static/manifest.json"/>'
    '<meta name="theme-color" content="#312e81"/>'
    '<link rel="apple-touch-icon" href="./app/static/icon-180.png"/>'
    '<meta name="apple-mobile-web-app-capable" content="yes"/>'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"/>'
    '<meta name="apple-mobile-web-app-title" content="Gematria"/>'
)


# Replaces Streamlit's "running man" status icon with a Hebrew letter that
# reshuffles while the app is busy.  Lives in <head> rather than a component
# because components render in a sandboxed iframe that cannot reach the parent
# DOM (the same restriction that blocks window.print(); see BUILD.md).
#
# NOTE: [data-testid="stStatusWidget"] is a Streamlit *internal* id.  It is not
# covered by any API stability promise, which is why requirements.txt pins
# streamlit — an unpinned rebuild could change the id and silently drop this
# back to the default icon.  Failure is cosmetic: no widget, no letters, app
# unaffected.
_LOADER_HEAD_SNIPPET = (
    "<style>"
    # The widget is display:block and Streamlit renders its own running content
    # (the Stop control) as a separate block, so an inline letter dropped in
    # front of it wraps onto its own line above. Force a single centred row.
    '[data-testid="stStatusWidget"]{display:flex !important;'
    "flex-direction:row !important;align-items:center !important;"
    "gap:.35em;white-space:nowrap;}"
    '[data-testid="stStatusWidget"] img,'
    '[data-testid="stStatusWidget"] svg{display:none !important;}'
    ".gem-nakdan{font-family:'Noto Serif Hebrew',David,'Times New Roman',serif;"
    "font-size:1.15rem;font-weight:700;line-height:1;color:#4F46E5;"
    "display:inline-block;min-width:1.25em;text-align:center;"
    "flex:0 0 auto;font-feature-settings:'liga' 0;}"
    # When Streamlit is idle the widget is empty, so the letter would be the
    # only child and would sit in the toolbar on its own. Show it only once
    # Streamlit has put its running content alongside it.
    ".gem-nakdan:only-child{display:none !important;}"
    "@media (prefers-color-scheme:dark){.gem-nakdan{color:#A5B4FC;}}"
    "</style>"
    "<script>(function(){"
    # Guards against _inject_loader_icon_fallback() (below) also running and
    # starting a second, redundant interval -- both mechanisms exist because
    # this one (patching Streamlit's installed index.html) silently fails on
    # some hosts (confirmed: Streamlit Community Cloud), so the fallback
    # always fires too; only one may actually be needed on a given host, but
    # which one can't be known in advance.
    "if(window.__gemNakdanActive){return;}window.__gemNakdanActive=true;"
    # 22 base letters + 5 finals, per the chosen letter set.
    'var L="אבגדהוזחטיכךלמםנןסעפףצץקרשת".split("");'
    # Reject anything shown in the last 4 ticks: pure random draws the same
    # glyph twice in a row often enough to read as a frozen spinner.
    "var recent=[];"
    "function pick(){for(var t=0;t<40;t++){"
    "var c=L[Math.floor(Math.random()*L.length)];"
    "if(recent.indexOf(c)===-1){recent.push(c);"
    "if(recent.length>4){recent.shift();}return c;}}"
    "return L[Math.floor(Math.random()*L.length)];}"
    # One self-healing interval: the widget is created and destroyed by
    # Streamlit on every run, so re-query each tick rather than caching a node.
    "setInterval(function(){"
    'var w=document.querySelector(\'[data-testid="stStatusWidget"]\');'
    "if(!w){return;}"
    'var el=w.querySelector(".gem-nakdan");'
    "if(!el){el=document.createElement(\"span\");"
    'el.className="gem-nakdan";w.insertBefore(el,w.firstChild);}'
    "el.textContent=pick();},140);"
    "})();</script>"
)


def _inject_loader_icon_fallback() -> None:
    """Runtime fallback for the loader-icon replacement, for hosts where
    patching Streamlit's installed index.html (_inject_pwa_head, below)
    silently has no effect -- confirmed on Streamlit Community Cloud, whose
    managed environment doesn't allow (or doesn't persist) that write.

    st.components.v1.html() can't substitute: it renders in a sandboxed
    iframe with no access to the parent DOM (the same restriction noted for
    window.print() in BUILD.md), so it can't reach the real status widget
    elsewhere on the page.

    Two things that look like they should work here don't:
    - A bare <script> tag inside st.markdown(unsafe_allow_html=True) is
      inserted via innerHTML-equivalent, and scripts inserted that way never
      execute -- a long-standing, deliberate browser restriction.
    - An onerror-triggered <img> (a classic workaround for exactly that
      restriction) was tried and tested directly against this app: it threw
      "Minified React error #231" and never ran. Streamlit's markdown
      renderer converts raw HTML into React elements rather than doing a
      plain innerHTML assignment, and React treats any `on*` attribute as a
      synthetic event *prop* expecting a function -- a plain HTML string
      value for `onerror` fails that expectation outright.

    An <iframe srcdoc="..."> sidesteps both: `srcdoc` isn't an event-prop
    name, so React passes it through untouched, and a <script> tag inside
    that srcdoc document runs as a normal same-document parse (not an
    innerHTML insertion), so it executes normally. A srcdoc iframe with no
    `sandbox` attribute is same-origin per spec, so it can reach back out via
    window.parent to the real page. Verified working end-to-end.

    Guarded by `window.__gemNakdanActive` on the *parent* window (set by
    _LOADER_HEAD_SNIPPET's own script too) so this is a safe no-op wherever
    the file patch already succeeded -- call unconditionally on every host,
    every run.
    """
    import html as _html_escape
    import json as _json
    import streamlit as st

    css = (
        '[data-testid="stStatusWidget"]{display:flex !important;'
        "flex-direction:row !important;align-items:center !important;"
        "gap:.35em;white-space:nowrap;}"
        '[data-testid="stStatusWidget"] img,'
        '[data-testid="stStatusWidget"] svg{display:none !important;}'
        ".gem-nakdan{font-family:'Noto Serif Hebrew',David,'Times New Roman',serif;"
        "font-size:1.15rem;font-weight:700;line-height:1;color:#4F46E5;"
        "display:inline-block;min-width:1.25em;text-align:center;"
        "flex:0 0 auto;font-feature-settings:'liga' 0;}"
        ".gem-nakdan:only-child{display:none !important;}"
        "@media (prefers-color-scheme:dark){.gem-nakdan{color:#A5B4FC;}}"
    )
    js = (
        "var P=window.parent;"
        "if(P.__gemNakdanActive){return;}P.__gemNakdanActive=true;"
        "var D=P.document;"
        "var s=D.createElement('style');s.textContent=" + _json.dumps(css) + ";"
        "D.head.appendChild(s);"
        "var L=" + _json.dumps("אבגדהוזחטיכךלמםנןסעפףצץקרשת") + ".split('');"
        "var recent=[];"
        "function pick(){for(var t=0;t<40;t++){"
        "var c=L[Math.floor(Math.random()*L.length)];"
        "if(recent.indexOf(c)===-1){recent.push(c);"
        "if(recent.length>4){recent.shift();}return c;}}"
        "return L[Math.floor(Math.random()*L.length)];}"
        "P.setInterval(function(){"
        "var w=D.querySelector('[data-testid=\"stStatusWidget\"]');"
        "if(!w){return;}"
        "var el=w.querySelector('.gem-nakdan');"
        "if(!el){el=D.createElement('span');"
        "el.className='gem-nakdan';w.insertBefore(el,w.firstChild);}"
        "el.textContent=pick();},140);"
    )
    srcdoc = _html_escape.escape(
        "<script>(function(){" + js + "})();</script>", quote=True)
    st.markdown(f'<iframe srcdoc="{srcdoc}" style="display:none" title="">'
                f'</iframe>', unsafe_allow_html=True)


def _signal_app_ready() -> None:
    """Tell an embedding page (the GitHub-Pages loader) the app is actually usable.

    The loader wraps this app in a cross-origin iframe. It cannot see inside to
    know when the app has finished its cold-start corpus build — Streamlit's
    server answers `_stcore/health` within a second, long before the search UI
    exists, so a health-based reveal flashes the loader away while the app is
    still loading. Instead we post a message *from inside the app* the moment
    the search input actually renders (i.e. the corpus is loaded and the page
    is interactive); the loader keeps its overlay up until it receives it.

    Same srcdoc-iframe trick as the loader-icon fallback (a bare <script> in
    st.markdown never executes, and React rejects on* attributes). From the
    srcdoc, `window.parent` is the app page and `window.parent.parent` is the
    loader (or the app itself when opened directly, where the message is a
    harmless no-op). Independent of the loader-icon guard so it fires whichever
    icon mechanism won.
    """
    import html as _html_escape
    import streamlit as st
    js = (
        "var P=window.parent;"                       # the app page
        "if(P.__gemReadyPosted){return;}"
        "var D=P.document;"
        "var t=P.setInterval(function(){"
        "if(P.__gemReadyPosted){clearInterval(t);return;}"
        "if(D.querySelector('input[aria-label=\"Hebrew phrase or name\"]')){"
        "P.__gemReadyPosted=true;clearInterval(t);"
        "try{P.parent.postMessage('gem-app-ready','*');}catch(e){}"
        "}},250);"
    )
    srcdoc = _html_escape.escape(
        "<script>(function(){" + js + "})();</script>", quote=True)
    st.markdown(f'<iframe srcdoc="{srcdoc}" style="display:none" title="">'
                f'</iframe>', unsafe_allow_html=True)


def _inject_pwa_head() -> None:
    """Patch Streamlit's served index.html with PWA + loader-icon tags.

    Streamlit offers no supported way to add tags to <head>, so we edit the
    package's static index.html (the standard workaround). Idempotent; runs at
    import so the Docker build step (`python app.py builddb`) bakes the patched
    file into the image. Static assets live in ./static (requires
    server.enableStaticServing, see .streamlit/config.toml).

    Each snippet is wrapped in its own HTML comment delimiters and *replaced* on
    every run rather than skipped when present. A content marker would make an
    already-patched index.html ignore later *edits* to a snippet — it would only
    ever pick up brand-new ones — which silently stranded the loader styling on
    a stale version once. Docker always starts from a pristine index.html; this
    matters for a local venv patched by an earlier release.
    """
    try:
        import streamlit as _stlib
        idx = pathlib.Path(_stlib.__file__).parent / "static" / "index.html"
        html = original = idx.read_text(encoding="utf-8")
        # Drop un-delimited injections written by earlier releases, so the
        # delimited blocks below are the single source of truth.
        html = html.replace(_PWA_HEAD_SNIPPET, "")
        html = re.sub(r"<style>(?:(?!</style>).)*?gem-nakdan.*?</style>", "",
                      html, flags=re.S)
        html = re.sub(r"<script>(?:(?!</script>).)*?gem-nakdan.*?</script>", "",
                      html, flags=re.S)
        for name, snippet in (("gem-pwa", _PWA_HEAD_SNIPPET),
                              ("gem-loader", _LOADER_HEAD_SNIPPET)):
            start, end = f"<!--{name}-start-->", f"<!--{name}-end-->"
            block = start + snippet + end
            pat = re.compile(re.escape(start) + ".*?" + re.escape(end), re.S)
            if pat.search(html):
                # lambda, not a replacement string: the snippets contain
                # backslashes and \g-like sequences that re would interpret.
                html = pat.sub(lambda _m: block, html)
            else:
                html = html.replace("<head>", "<head>" + block, 1)
        if html != original:
            idx.write_text(html, encoding="utf-8")
    except Exception:
        pass  # Head tags are an enhancement — never block the app over them


_inject_pwa_head()


def _tip(text: str):
    """Widget tooltip text — suppressed in app view (?view=app).

    Streamlit's hover tooltips clip and misposition on phone screens, and the
    app's Guide page covers the same explanations, so the PWA renders no `?`
    icons at all.
    """
    import streamlit as st
    return None if st.query_params.get("view") == "app" else text


def run_app() -> None:
    import streamlit as st

    st.set_page_config(page_title="Tanach Gematria Engine",
                       page_icon="📜", layout="wide",
                       initial_sidebar_state="collapsed")

    # Content text (verses, breakdowns, guide prose) in a serif Hebrew face —
    # matches the print view's Noto Serif Hebrew and renders nikud/taamim more
    # legibly. UI chrome (buttons, tabs, labels) stays in Streamlit's sans.
    # Slight size bump for vowel-mark legibility, most visible on phones.
    st.markdown(
        "<style>"
        "@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+Hebrew:wght@400;700&display=swap');"
        "[data-testid='stMarkdownContainer'] p,"
        "[data-testid='stMarkdownContainer'] li,"
        "[data-testid='stMarkdownContainer'] blockquote{"
        "font-family:'Noto Serif Hebrew',Georgia,'Times New Roman',serif;"
        "font-size:1.05rem;}"
        # Rule between per-method result blocks. currentColor at low alpha so it
        # reads on both themes without hard-coding either one.
        "hr.mdiv{border:0;border-top:1px solid currentColor;opacity:.18;"
        "margin:1.9rem 0 1.1rem;}"
        # The result line. Full-strength text against the dimmed blurb above it
        # carries the distinction on its own — an earlier version added a left
        # rule, but hanging a vertical mark directly above a boxed table put two
        # competing left edges a few pixels apart and read as clutter.
        #
        # Spacing does the grouping instead: it sits closer to the table it
        # describes than to the blurb above, so the value and its results read
        # as one block rather than as a caption stranded between the two.
        #
        # ⚠️ Kept TIGHT on purpose. A results page runs up to 35 of these blocks,
        # so every extra margin here is paid 35 times and turns into a long
        # scroll. The blurb is pulled up under its heading and the gap to the
        # value is trimmed, keeping the grouping while cutting the total height.
        ".mhead{margin-bottom:.15rem !important;}"
        ".mblurb{margin-top:0 !important;margin-bottom:0 !important;}"
        ".mval{font-size:.95rem;margin:.55rem 0 .25rem;"
        "font-variant-numeric:tabular-nums;}"
        ".mval b{font-size:1.15rem;font-weight:700;}"
        "</style>",
        unsafe_allow_html=True)

    _inject_loader_icon_fallback()
    _signal_app_ready()

    # App view (?view=app): the PWA opens here. Guide + Tabs 1-2 only,
    # classical cipher set by default. The regular site is unaffected.
    app_view = st.query_params.get("view") == "app"
    if app_view:
        # No sidebar in the app: its content is skipped below; this hides the
        # leftover expander chevron (best-effort, selectors vary by version).
        st.markdown(
            "<style>"
            "[data-testid='stSidebarCollapsedControl'],"
            "[data-testid='collapsedControl']{display:none !important;}"
            "</style>",
            unsafe_allow_html=True)

    def hide_uniform_track(df):
        """Drop the Track column unless these rows genuinely vary (see module fn)."""
        return drop_uniform_track(df, app_view)

    # The two heavy Tab 1 scans are cached so re-opening a panel, or touching any
    # unrelated widget, does not recompute them. `_conn` is underscore-prefixed so
    # Streamlit skips hashing the connection; `corpus_key` stands in for it, so a
    # custom Sefaria corpus cannot collide with the bundled one. Sequence args are
    # tuples because the key must be hashable and stable.
    # Bounded so distinct search values/filters don't grow these caches without
    # limit over a long-running container. `max_entries` alone is enough for
    # safety here (eviction just drops Streamlit's dict entry -- see cache_data
    # docs; no connection or resource teardown is involved), `ttl` is a second,
    # independent safety net.
    @st.cache_data(show_spinner=False, max_entries=100, ttl=3600)
    def cached_span_search(_conn, corpus_key, target, cipher, max_span, colel,
                           tracks, cross_verse=False):
        return span_search(_conn, target, cipher, max_span=max_span, colel=colel,
                           tracks=list(tracks) if tracks else None,
                           cross_verse=cross_verse)

    @st.cache_data(show_spinner=False, max_entries=100, ttl=3600)
    def cached_xm_matrix(_conn, corpus_key, a_vals_items, colel, tracks, boundaries):
        return _xm_count_matrix(_conn, dict(a_vals_items), colel,
                                list(tracks) if tracks else None,
                                list(boundaries) if boundaries else None)

    # Verse-mode lookups. These MUST be cached functions taking the connection
    # as `_conn`, not ad-hoc queries in the script body: an earlier version ran
    # raw_conn(conn).execute(...) at Tab-1 script level, so they re-executed on
    # every rerun of every session and took the Space down with SIGSEGV
    # (exit 139, `343f311`, reverted). A verse's units are a pure function of
    # its reference, so they cache perfectly. See HANDOFF's concurrency section.
    @st.cache_data(show_spinner=False, max_entries=200, ttl=3600)
    def cached_verse_units(_conn, corpus_key, book, chapter, verse):
        """Boundary types this verse actually has, in hierarchy order.

        Built from the DB rather than a fixed menu because 1,731 verses have no
        atnach and therefore no SecondHalf.
        """
        rows = raw_conn(_conn).execute(
            "SELECT DISTINCT boundary_type FROM units "
            "WHERE book=? AND chapter=? AND verse=? AND variant_track='Ksiv'",
            (book, int(chapter), int(verse))).fetchall()
        have = {r[0] for r in rows}
        return [u for u in ("Verse", "FirstHalf", "SecondHalf",
                            "TiphchaPhrase", "ZakefPhrase", "Word")
                if u in have]

    @st.cache_data(show_spinner=False, max_entries=200, ttl=3600)
    def cached_verse_unit_rows(_conn, corpus_key, book, chapter, verse, boundary):
        """(sub_id, consonants, text_display, nikud_partial) for one boundary.

        nikud_partial travels with the row deliberately: the query-side gate
        must read the SELECTED UNIT's own flag, never the parent verse's —
        922 clean halves and 15,856 clean words sit inside flagged verses.
        """
        return raw_conn(_conn).execute(
            "SELECT sub_id, consonants, text_display, nikud_partial "
            "FROM units WHERE book=? AND chapter=? AND verse=? "
            "AND boundary_type=? AND variant_track='Ksiv' ORDER BY sub_id",
            (book, int(chapter), int(verse), boundary)).fetchall()

    @st.cache_data(show_spinner=False, max_entries=2, ttl=3600)
    def cached_name_index():
        """The nikud tool's name lookup, built offline by build_name_index.py.

        {bare consonants: {options: [{form, count, variants}], source}}.
        Committed rather than built at runtime — it needs the corpus JSONL and
        the review CSVs, and none of that belongs in a page load.
        """
        path = pathlib.Path(__file__).with_name("nikud_names.json")
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @st.cache_data(show_spinner=False, max_entries=2, ttl=3600)
    def cached_wiktionary_nikud():
        """General vocabulary, built offline by build_wiktionary_nikud.py.

        Returns (headwords, derived), each {bare consonants: [pointed form]}.
        18,519 headwords from Hebrew Wiktionary plus 3,792 words recovered by
        splitting its multi-word phrases — the LOWEST-priority
        source. It covers ordinary words the Tanach corpus and the curated name
        lists do not, but it is a modern dictionary: for a name it would offer
        the common noun, so it must never outrank the other two.

        ⚠️ `derived` ranks below `headwords` and is kept separate for a reason:
        a word pulled out of a phrase is often construct-state or prefixed
        (תְּרוּמַת is "terumah OF"), so the vocalization is correct but
        grammatically BOUND. Fine as an offered variant, wrong as the default
        answer for a word typed on its own.
        """
        path = pathlib.Path(__file__).with_name("wiktionary_nikud.json")
        if not path.exists():
            return {}, {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("headwords", {}), data.get("derived", {})

    @st.cache_data(show_spinner=False, max_entries=4, ttl=3600)
    def cached_ref_index(_conn, corpus_key):
        """{book: {chapter: [verses]}} for the cascading selects.

        Cached because an st.expander's body executes even while collapsed, so
        an uncached build would scan the whole index on every rerun whether or
        not anyone opened it.
        """
        idx: Dict[str, Dict[int, List[int]]] = {}
        for b, c, v in raw_conn(_conn).execute(
                "SELECT DISTINCT book, chapter, verse FROM units "
                "WHERE variant_track='Ksiv' ORDER BY book, chapter, verse"):
            idx.setdefault(b, {}).setdefault(int(c), []).append(int(v))
        return idx

    @st.cache_data(show_spinner=False, max_entries=50, ttl=3600)
    def cached_boundary_population(_conn, corpus_key, tracks, boundaries):
        return boundary_population(_conn, list(tracks) if tracks else None,
                                   list(boundaries) if boundaries else None)

    @st.cache_data(show_spinner=False, max_entries=50, ttl=3600)
    def cached_method_spread(_conn, corpus_key, tracks, boundaries):
        """Distinct values each method produces over the units in scope.

        Methods differ enormously here — KatanMispari yields 9 distinct values
        over this corpus, ImMiluiNekudot yields 3,467 — so the average number of
        units sharing a value runs from ~36,000 down to ~95. Any single
        share-of-population threshold therefore measures a method's spread far
        more than it measures rarity. Expected-count per method is what makes
        cells comparable across columns.
        """
        where, params = [], []
        if tracks:
            where.append("variant_track IN (%s)" % ",".join("?" * len(tracks)))
            params += list(tracks)
        if boundaries:
            where.append("boundary_type IN (%s)" % ",".join("?" * len(boundaries)))
            params += list(boundaries)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        cols = ", ".join(f"COUNT(DISTINCT {c})" for c in CIPHER_NAMES)
        row = raw_conn(_conn).execute(f"SELECT {cols} FROM units{clause}",
                                      params).fetchone()
        return {c: (n or 1) for c, n in zip(CIPHER_NAMES, row)}

    # Loaded once per container and shared across sessions. ~23k short strings
    # (a few MB) — small enough beside the corpus itself that a single unbounded
    # entry is not a repeat of the cache-growth problem that caused the hangs;
    # it takes no arguments, so it can only ever hold one entry.
    @st.cache_resource(show_spinner=False)
    def _english_index() -> Dict[Tuple[str, int, int], str]:
        return load_english()

    def verse_english(book, chapter, verse) -> str:
        """Translation for one verse, or "" when unavailable."""
        try:
            return _english_index().get((book, int(chapter), int(verse)), "")
        except (TypeError, ValueError):
            return ""

    # max_entries=3 comfortably holds the base corpus + one custom-refs corpus
    # + one in-flight rebuild, so repeated "Retry Sefaria fetch" clicks (each
    # bumping _nonce, and therefore the cache key) can't pin unbounded ~370MB
    # corpora alive forever. No ttl: the hot base-corpus entry restores from
    # disk in seconds, so there's nothing worth periodically evicting it for.
    @st.cache_resource(show_spinner="Loading Tanach…", max_entries=3)
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
        # Both paths hand back a ThreadLocalConnection: this object is cached by
        # @st.cache_resource and therefore shared across every session and
        # script-runner thread, which a raw sqlite3 connection cannot survive.
        if not extra_refs_key and PREBUILT_DB.exists():
            disk = sqlite3.connect(str(PREBUILT_DB), check_same_thread=False)
            # A prebuilt DB from an older release can be missing columns this
            # release queries — a deployment that ships app.py but reuses a
            # stale tanach.db (Streamlit Cloud does exactly this) would then
            # fail with an opaque DatabaseError on the first search. Rather
            # than trust the file, check its schema and fall through to a
            # rebuild if it predates the current one.
            try:
                _have = {r[1] for r in disk.execute("PRAGMA table_info(units)")}
            except sqlite3.Error:
                _have = set()
            _need = set(CIPHER_NAMES) | {"nikud_partial", "boundary_type",
                                         "variant_track", "consonants"}
            _stale = _need - _have
            if _stale:
                disk.close()
                # Falls through to build_database below (~20-30s) instead of
                # serving a DB that cannot answer this release's queries.
                st.warning(
                    f"Bundled database is from an earlier version (missing: "
                    f"{', '.join(sorted(_stale)[:4])}"
                    f"{'…' if len(_stale) > 4 else ''}). Rebuilding it now — "
                    "this takes about half a minute and happens once.")
            else:
                conn = share_in_memory(disk)
                disk.close()
                return conn, len(verses), True, verse_index
        built = build_database(verses)
        conn = share_in_memory(built)
        built.close()
        return conn, len(verses), fetched_ok, verse_index

    def get_connection(extra_refs_key: str):
        nonce = st.session_state.get("sefaria_retry_nonce", 0)
        conn, n, ok, verse_index = _build_connection(extra_refs_key, nonce)
        # Identifies the actual corpus content, not just what refs were
        # requested: folds in the retry nonce and fetch outcome so a search
        # cached against a failed-fetch fallback corpus doesn't stay stuck as
        # a stale cache hit once a retry succeeds and the real corpus is built.
        corpus_key = f"{extra_refs_key}|{nonce}|{ok}"
        if not ok:
            st.warning("Couldn't fetch the requested Sefaria refs "
                       "(offline or rate-limited). Showing the base corpus without them.")
            if st.button("Retry Sefaria fetch"):
                st.session_state["sefaria_retry_nonce"] = nonce + 1
                st.rerun()
        return conn, n, verse_index, corpus_key

    if app_view:
        _extra_refs = ""
    else:
        with st.sidebar:
            _extra_refs = st.text_input(
                "Extra Sefaria refs (semicolon-separated)", "",
                key="sefaria_refs",
                help=_tip("e.g. Genesis 1; Psalms 23 — appended to the bundled corpus. "
                          "Adding refs triggers a full rebuild (~20–30 s)."))

    conn, n_loaded, verse_index, corpus_key = get_connection(_extra_refs)

    if not app_view:
      with st.sidebar:
        st.header("⚙️ Corpus")
        st.caption(f"{n_loaded:,} Masoretic verses — loaded from bundled corpus.")
        st.divider()
        st.subheader(f"Active methods ({N_CIPHERS})")
        st.write(", ".join(CIPHER_DISPLAY_ORDER))
        # ⚠️ Derived from the display groups, not hand-listed. The old caption
        # was maintained separately and had drifted: it called Avgad
        # "Traditional" (it is Remak-era, not Talmudic) while listing Achbi and
        # Agdat — the same temurah family — under a different heading, and it
        # grouped Katan and Siduri with the genuinely Talmudic methods.
        st.caption(
            "Talmud-attested: " + ", ".join(TALMUD_CIPHERS) + ". "
            "Common (later): " + ", ".join(COMMON_CIPHERS) + ". "
            "Core values: KatanMispari, Mispari, MispariHaGadol. "
            "Temurah: Achbi, Avgad, Agdat, ReverseAvgad, AyakBachar, AchasBeta. "
            "Positional: ReverseOrdinal, Ribua, Kidmi, Boneeh, HaAchor, HaMerubahKlali. "
            "Letter-name: Milui, Neelam, Emtzaiyot, MiluiMaleh, NeelAmMaleh, "
            "EmtzaiyotMaleh, Ofanim. "
            "Vowel-mark (nikud): " + ", ".join(NIKUD_CIPHERS) + ". "
            "Kolel: KololEhad, KololOtiyot. "
            "Text units: Word, ZakefPhrase, TiphchaPhrase, FirstHalf, "
            "SecondHalf, Verse, Perek, Sefer.")

    DETAIL_BOUNDARIES = {"Word", "ZakefPhrase", "TiphchaPhrase",
                         "FirstHalf", "SecondHalf", "Verse", "Petucha", "Setuma",
                         "WordSpan"}

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

    def _highlight_in_verse(cantillated: str, boundary: str, matched_cons,
                            span_range=None) -> str:
        """Return cantillated text as HTML with the matched sub-unit wrapped in <mark>."""
        import re as _re
        if boundary == "WordSpan" and span_range:
            # Index-based, not consonant-matching: a span's consonant string can
            # recur inside the same verse, and the "Word" branch below marks only
            # the first occurrence.
            return mark_word_span(cantillated, span_range[0], span_range[1])
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
                            active_method=None, query_info=None, colel=False,
                            span_range=None, track=None, end_ref=None,
                            query_ref=None, query_method=None):
        import streamlit.components.v1 as _components
        if boundary not in DETAIL_BOUNDARIES:
            return
        v = verse_index.get((book, int(chapter), int(verse)))
        if v is None:
            st.info("Source text not available for this unit.")
            return
        # Each variant fork tokenizes its own source text (see verse_forks), so a
        # match on a non-Ksiv track must be rendered and scored against that
        # text — verse_index holds the Ksiv reading by default.
        src_text = v.text
        if track == "Kri" and getattr(v, "kri_text", None):
            src_text = v.kri_text
        # A cross-verse span's word offsets are relative to the FIRST verse but
        # run past its end, so the source has to be the whole run of verses the
        # span covers. Joining them here means every downstream step — the
        # highlight, the consonants, the cipher values, the print-out — operates
        # on the same text the span was scored over, with no special-casing.
        cross_run = []
        if end_ref and boundary == "WordSpan":
            _e_ch, _e_vs = int(end_ref[0]), int(end_ref[1])
            _c, _s = int(chapter), int(verse)
            while (_c, _s) <= (_e_ch, _e_vs):
                _rv = verse_index.get((book, _c, _s))
                if _rv is None:
                    break
                cross_run.append((_c, _s, _rv))
                if (_c, _s) == (_e_ch, _e_vs):
                    break
                # Step to the next verse, rolling over to the next chapter when
                # this one runs out. Bounded by the end ref, so a missing verse
                # (which `break` above already guards) cannot loop forever.
                if verse_index.get((book, _c, _s + 1)) is not None:
                    _s += 1
                else:
                    _c, _s = _c + 1, 1
            if len(cross_run) > 1:
                src_text = " ".join(
                    (rv.kri_text if track == "Kri" and getattr(rv, "kri_text", None)
                     else rv.text)
                    for _, _, rv in cross_run)
        # A word span is not a stored boundary: reconstruct it from the word
        # offsets so the displayed text, the consonants, and the cipher values
        # all describe the span rather than the whole verse.
        span_w_cons = span_cons = None
        variant_consonantal_only = False
        if boundary == "WordSpan" and span_range:
            _i0, _i1 = span_range
            matched_text = " ".join(_tokenize_raw_words(src_text)[_i0:_i1])
            _tok = tokenize_words(src_text)
            if (track == "TextVariant" and getattr(v, "doublet_from", None)
                    and getattr(v, "doublet_to", None)):
                # The doublet is defined on bare consonants (e.g. אחר → ואחר) and
                # usually has no counterpart in the cantillated text, so it is
                # applied per word exactly as verse_forks builds the fork's word
                # list.  Consequence: the cantillated line shows the Ksiv
                # spelling while the values follow the variant reading — flagged
                # to the reader below rather than silently diverging.
                _sub, _ = apply_doublet_to_words(
                    _tok, v.doublet_from, v.doublet_to)
                variant_consonantal_only = _sub[_i0:_i1] != _tok[_i0:_i1]
                _tok = _sub
            span_w_cons = " ".join(_tok[_i0:_i1])
            span_cons = "".join(_tok[_i0:_i1])
        friendly_boundary = BOUNDARY_LABELS.get(boundary, boundary)
        # A cross-verse span is cited as a range, and says so in words: the
        # sof-pasuq it crosses is a real division in the text, so the panel
        # should never let it read like an ordinary single-verse match.
        is_cross = len(cross_run) > 1
        if is_cross:
            _last_c, _last_s, _ = cross_run[-1]
            ref_label = (f"{book} {chapter}:{verse}–{_last_s}"
                         if _last_c == int(chapter) else
                         f"{book} {chapter}:{verse}–{_last_c}:{_last_s}")
            st.markdown(f"**{ref_label}** · _{friendly_boundary}_")
            st.caption("⚠️ Crosses a verse boundary (sof pasuq ׃). "
                       "Full run shown below.")
        else:
            st.markdown(f"**{book} {chapter}:{verse}** · _{friendly_boundary}_")
        # Verses whose presence in the Masoretic text is disputed. Checked
        # across the whole run for a cross-verse span, since such a span can
        # reach into a disputed verse from an undisputed one.
        _disputed = {disputed_verse_note(book, c, s)
                     for c, s, _ in (cross_run or [(chapter, verse, None)])}
        for _note in sorted(n for n in _disputed if n):
            st.info(_note)
        # The same check for the QUERY verse. The loop above is keyed on the
        # MATCHED unit, so a disputed verse used as the search term had no
        # note anywhere — on screen or in the export — until a verse picker
        # made Joshua 21:36-37 directly selectable. Skipped when query and
        # match are the same verse, which the loop above already covered.
        if query_ref:
            _q_note = disputed_verse_note(*query_ref)
            if _q_note and (str(query_ref[0]), int(query_ref[1]), int(query_ref[2])) \
                    != (str(book), int(chapter), int(verse)):
                st.info(f"Search query ({query_ref[0]} {query_ref[1]}:"
                        f"{query_ref[2]}) — {_q_note}")
        sub_unit = boundary in ("Word", "ZakefPhrase", "TiphchaPhrase",
                                "FirstHalf", "SecondHalf", "WordSpan")
        # Ksiv/Kri divergence is shown INLINE on the single cantillated line —
        # the Kri bracketed after its Ksiv counterpart, the notation the source
        # itself uses — rather than repeating the whole verse a second time for
        # the sake of one or two differing words. Display only: the bracketed
        # text is never part of `cons`/`w_cons` and contributes to no value.
        #
        # The brackets are inserted AFTER highlighting, because the highlight is
        # computed against the Ksiv text and inserting words first would shift
        # every offset it depends on.
        _kri_line = getattr(v, "kri_text", None) if track != "Kri" else None
        _has_kri = bool(_kri_line) and not is_cross
        highlighted_html = ""
        if sub_unit and src_text:
            matched_cons = strip_to_consonants(matched_text) if matched_text else None
            highlighted = _highlight_in_verse(src_text, boundary, matched_cons,
                                              span_range=span_range)
        else:
            highlighted = src_text or ""
        if _has_kri:
            highlighted = merge_ksiv_kri_display(highlighted, _kri_line) or highlighted
        highlighted_html = highlighted
        st.markdown(highlighted, unsafe_allow_html=True)
        if _has_kri:
            st.caption("Bracketed = Kri, not counted. Values follow the Ksiv.")
        # Values: matched sub-unit when available, full verse otherwise.
        # In app view this readout is suppressed entirely — see below, near
        # where `vals` is displayed, for why and for the site-only caveat.
        #
        # HaNekudot and MiluiNekudot score the vowel marks ALONE, so the letters
        # on this row contribute nothing to the total and must not be labelled
        # as what matched. (ImHaNekudot/ImMiluiNekudot DO add the letters, so
        # their label stays accurate.) The old comment further down noting this
        # mislabelling is now resolved rather than deferred.
        _pure_nikud = active_method in ("HaNekudot", "MiluiNekudot")
        _cons_lbl = ("Consonants (not counted by this method)" if _pure_nikud
                     else "Matched consonants")
        if span_cons is not None:
            cons = span_cons
            if not app_view:
                st.markdown(f"**{_cons_lbl}:** `{cons}`")
                if variant_consonantal_only:
                    st.caption("Textual-variant track: the variant reading exists "
                               "in the consonantal text only, so the cantillated "
                               "line above shows the Ksiv spelling while the "
                               "values below follow the variant.")
        elif sub_unit and matched_text:
            cons = strip_to_consonants(matched_text)
            if not app_view:
                st.markdown(f"**{_cons_lbl}:** `{cons}`")
        else:
            cons = strip_to_consonants(src_text)
            if not app_view:
                st.markdown(f"**{'Consonants (not counted by this method)' if _pure_nikud else 'Consonants'}:** `{cons}`")
        # Derive word-boundary-aware consonants for Kaful/Mityashev/Meshulash
        if boundary == "FirstHalf":
            w_cons, _ = split_halves_word_cons(src_text)
        elif boundary == "SecondHalf":
            _, w_cons = split_halves_word_cons(src_text)
        elif boundary == "Word":
            w_cons = cons
        elif boundary == "WordSpan":
            # Must keep spaces: collapsing a multi-word span into one token
            # changes what the word-aware ciphers compute.
            w_cons = span_w_cons or cons
        else:
            w_cons = " ".join(tokenize_words(src_text))
        # Result rows carry only bare consonants, so passing matched_text
        # straight through scored every vowel-mark cipher as 0 — the panel
        # contradicted the search that produced the row (a Word match found via
        # HaNekudot=50 displayed HaNekudot=0). Recover the pointed text from the
        # parent verse; fall back to matched_text if it cannot be located.
        # Called once and cached (code review: was called twice with identical
        # args a few lines apart) — the result also drives match_nikud_unreliable.
        _located = locate_vocalized(src_text, cons) if sub_unit and matched_text else ""
        cantillated_src = (_located or matched_text) if sub_unit and matched_text else src_text
        # True when a vowel-mark cipher's value for this match was computed
        # without nikud, because the pointed text couldn't be located. Drives
        # the warning below AND is threaded into the print-out (see the
        # build_print_html call), since a wrong-looking value needs the same
        # explanation in every view and in the export, not just on the site.
        match_nikud_unreliable = bool(sub_unit and matched_text and not _located)
        vals = compute_all_ciphers(cons, cantillated_src, word_consonants=w_cons)
        # This unit contains a Ksiv word the source prints unpointed, so its
        # four vowel-mark totals are short by that word's contribution. Such
        # units are already excluded from every search; the panel is reached by
        # other routes (a letter-method search, or browsing in Tab 2), so the
        # same rule is applied here — the four values are removed outright
        # rather than shown as a misleading 0. Note ImHaNekudot/ImMiluiNekudot
        # do NOT fall to 0: they add letters to marks, so they quietly collapse
        # to the plain letter total, which reads as an ordinary number and is
        # more misleading than the zero.
        unit_nikud_partial = has_unpointed_word(cantillated_src)
        # App view drops the matched-consonants readout and this all-methods
        # table: the panel already opened on the one method the user searched
        # under (the caption/breakdown right below), and this table restates
        # the other 33 without being asked. Still computed either way — `vals`
        # feeds the print-out below regardless of whether it is shown here.
        # (The pure-vowel mislabelling this comment used to defer — "Matched
        # consonants" under HaNekudot/MiluiNekudot — is now fixed above.)
        if not app_view:
            # "—" rather than a dropped column, matching Tab 1's convention for
            # a query typed without nikud: the table keeps its shape and the
            # reader can see which four methods are unavailable and why.
            _vals_disp = {
                k: ("—" if (unit_nikud_partial and k in NIKUD_CIPHERS) else v)
                for k, v in vals.items()
            }
            st.dataframe(pd.DataFrame([_vals_disp]), width="stretch", hide_index=True)
        # Not part of the app-view simplification above: this says a value the
        # user is looking at may be WRONG, which matters in every view — code
        # review found it gated behind `if not app_view:`, so app-view users
        # saw an unexplained (often zero) value with no warning at all.
        if match_nikud_unreliable:
            st.caption("⚠️ Could not locate this unit's pointed text in the "
                       "verse; vowel-mark methods are computed without "
                       "nikud here.")
        # Distinct from the warning above, and NOT covered by it: there the
        # pointed text could not be found, so the caveat fires on a lookup
        # failure. Here the lookup succeeds and returns a word the source
        # prints bare — locate_vocalized happily finds `מצותו`, so
        # match_nikud_unreliable stays False and the reader would otherwise see
        # HaNekudot = 0 with no explanation at all.
        ksiv_unpointed = bool(unit_nikud_partial
                              and active_method in NIKUD_CIPHERS)
        # Gated on the ACTIVE METHOD, not merely on the unit: the caveat says
        # nothing about Gadol, Standard or the other 27 letter-based methods,
        # so showing it under their breakdowns was noise. The four vowel-mark
        # columns are still removed from the all-methods table above whenever
        # the unit is flagged, regardless of which method is active.
        if ksiv_unpointed:
            st.caption(f"⚠️ {KSIV_UNPOINTED_NOTE}")
        # Per-letter (or per-vowel-mark) breakdown for the active method.
        # Suppressed when the active method is a vowel-mark one on a unit whose
        # vowel totals were removed above — a breakdown of marks that are not
        # in the text would reinstate, letter by letter, exactly the number the
        # exclusion exists to withhold.
        breakdown_rows = None
        derivation = None
        if active_method and active_method in CIPHERS and not ksiv_unpointed:
            breakdown_rows = cipher_breakdown(active_method, cons, w_cons,
                                              cantillated=cantillated_src)
            if breakdown_rows:
                parts = " + ".join(f"{lbl}({val})" for lbl, val in breakdown_rows)
                total = sum(val for _, val in breakdown_rows)
                st.caption(f"**{active_method}:** {parts} = {total}")
            else:
                # Not a per-letter sum, but not opaque either: these four are
                # named operations on the Standard total and the steps are the
                # whole answer. Showing "Total value: 8" alone gave the reader
                # nothing to check.
                derivation = derivation_steps(active_method, cons)
                if derivation:
                    st.caption(f"**{active_method}:** " + " → ".join(
                        f"{lbl} = {v}" for lbl, v in derivation))
        if boundary in ("Petucha", "Setuma"):
            run = _paragraph_run(book, chapter, verse)
            if run and len(run) > 1:
                st.markdown("**Full paragraph block:**")
                for rv in run:
                    st.markdown(f"- {rv.book} {rv.chapter}:{rv.verse} — {rv.text}")
        # ── English translation (opt-in) ─────────────────────────────────────
        # Always the FULL verse's translation, even when the unit is a word,
        # phrase or half-verse: JPS is a sense-for-sense translation with no
        # word-level alignment to the Hebrew, so there is no honest way to show
        # "the English of this half-verse." Labelled accordingly for sub-units
        # rather than implying a correspondence that doesn't exist.
        # The key is shared with the print-out below, so ticking the box both
        # reveals the text here and includes it in the export.
        # Defined here rather than in the print block below because the English
        # checkbox key must share this panel's identity: two detail panels open
        # at once (e.g. a Tab 1 result and a cross-method result) would collide
        # on a fixed key and mirror each other's tick state.
        _uid = f"{book}_{chapter}_{verse}_{boundary}_{active_method}"
        _show_en_key = f"show_en_{_uid}"
        # For a cross-verse span the run is what the reader is looking at, so
        # the translation covers every verse in it, each labelled with its own
        # reference — one undifferentiated block would obscure where each verse
        # begins, which is the very boundary this panel is flagging.
        if is_cross:
            _parts = []
            for _c, _s, _ in cross_run:
                _t = verse_english(book, _c, _s)
                if _t:
                    _parts.append(f"{_c}:{_s}  {_t}")
            english_text = "\n\n".join(_parts)
        else:
            english_text = verse_english(book, chapter, verse)
        # The QUERY's translation, when the query is itself a verse reference
        # (verse mode, and Tab 2's selected unit). `query_ref` is
        # (book, chapter, verse); the query may be a sub-unit of it, in which
        # case the English still covers the whole verse — there is no
        # word-level alignment — and is labelled as such.
        query_english_text = ""
        if query_ref:
            _qb, _qc, _qv = query_ref
            query_english_text = verse_english(_qb, _qc, _qv)
        show_english = False
        if english_text or query_english_text:
            show_english = st.checkbox(
                "Show English translation", key=_show_en_key, value=False,
                help=_tip("Koren Jerusalem Bible (© Koren Publishers "
                          "Jerusalem, CC BY-NC). Shown for the full verse, and "
                          "included in the print-out / download while ticked."))
            # Query side first, mirroring the document order (query, then
            # match). Only when the query is a different verse from the match:
            # otherwise this would print the same translation twice.
            if show_english and query_english_text and \
                    (str(_qb), int(_qc), int(_qv)) != (str(book), int(chapter), int(verse)):
                # Name the verse once. `label` already reads "Selected Unit
                # (Genesis 1:1)" when Tab 2 or verse mode sets it, so appending
                # the reference again printed it twice.
                st.markdown(
                    f"**English — search query ({book_label(_qb)} {_qc}:{_qv}):**")
                st.text(query_english_text)
            if show_english and english_text:
                # No edition name in the heading — the attribution caption
                # directly below already names it, and repeating it here just
                # crowded the line. "full verse" stays: it is not attribution,
                # it says the English covers the whole verse rather than the
                # sub-unit being scored.
                # Name the matched verse whenever a query translation is also
                # on screen: two untitled "English:" blocks give the reader no
                # way to tell which verse each belongs to. "full verse" stays
                # for a sub-unit — it says the English covers the whole verse
                # rather than the scored span.
                _m_ref = (f" ({book_label(str(book))} {chapter}:{verse})"
                          if query_english_text else "")
                if sub_unit:
                    st.markdown(f"**English — match{_m_ref}, full verse:**")
                else:
                    st.markdown(f"**English — match{_m_ref}:**")
                # st.markdown would interpret stray _ * [ ] in the translation
                # as formatting (the markdown-injection class of bug fixed in
                # 4702cd8), so the text goes through st.text, not markdown.
                st.text(english_text)
            # One attribution for the panel, however many translations are
            # shown — same reasoning as the export's document-level notice.
            if show_english:
                st.caption(ENGLISH_ATTRIBUTION_SHORT)
        # ── Print / Export ───────────────────────────────────────────────────
        # The print-out used to show only how the *matched* text arrives at its
        # value, never how the searched word itself does — even though showing
        # both is the point of a "these are equal" result. query_info carries
        # only cons/raw/wcons (no vals), so the query's own breakdown and value
        # are computed here, the same way the matched unit's already are above.
        query_breakdown = None
        query_derivation = None
        query_val = None
        # The query is normally scored under the same method as the match, but a
        # cross-method drill-down scores the two sides differently — see the
        # query_method note at that call site.
        _q_method = query_method or active_method
        if query_info and query_info.get("cons") and _q_method in CIPHERS:
            _q_cons, _q_raw, _q_wcons = (query_info["cons"], query_info.get("raw", ""),
                                         query_info.get("wcons", ""))
            query_val = compute_all_ciphers(
                _q_cons, _q_raw, word_consonants=_q_wcons).get(_q_method)
            query_breakdown = cipher_breakdown(_q_method, _q_cons, _q_wcons,
                                               cantillated=_q_raw)
            if not query_breakdown:
                query_derivation = derivation_steps(_q_method, _q_cons)
        _print_key   = f"do_print_{_uid}"
        _html_doc = build_print_html(
            query_info,
            {"book": book, "chapter": chapter, "verse": verse,
             "boundary": boundary, "highlighted_html": highlighted_html},
            breakdown_rows, active_method or "Standard", colel, vals,
            query_breakdown=query_breakdown, query_val=query_val,
            derivation=derivation, query_derivation=query_derivation,
            match_nikud_unreliable=match_nikud_unreliable,
            # Only when the box is ticked: the export mirrors what the panel
            # shows rather than always carrying the translation.
            english=(english_text if show_english else ""),
            english_is_full_verse=sub_unit,
            # Query side of the same pair. Mirrors the panel: carried only
            # while the box is ticked, and only when the query is a different
            # verse from the match (otherwise the document would repeat one
            # translation under two headings).
            query_english=(query_english_text if (show_english and query_ref and
                           (str(query_ref[0]), int(query_ref[1]), int(query_ref[2]))
                           != (str(book), int(chapter), int(verse))) else ""),
            query_english_is_full_verse=True,
            # A disputed-verse note about the QUERY had no render site at all —
            # neither on screen nor in the export — because the existing note
            # is keyed on the matched unit. Joshua 21:36-37 become directly
            # selectable once a verse picker exists, so a total that includes
            # them must say so in the document.
            query_disputed_note=(disputed_verse_note(*query_ref)
                                 if query_ref else ""),
            # Cross-method drill-down: the query's own method, so each side of
            # the document is labelled and scored with the right one.
            query_method=query_method,
            # Same reasoning as match_nikud_unreliable: a caveat about a value
            # being wrong has to travel with the document, not stay on screen.
            ksiv_unpointed=ksiv_unpointed,
            # The Kri belongs in the export for the same reason it belongs on
            # screen — a reader looking at a bare Ksiv word needs the vocalised
            # reading. Display only: it is not in `cons` and takes no part in
            # any total.
            kri_display=(merge_ksiv_kri_display(src_text, _kri_line)
                         if (_kri_line and not is_cross) else ""),
        )
        _pc, _dc = st.columns(2)
        with _pc:
            if st.button("🖨️ Print / Save PDF", key=f"pr_{_uid}",
                         help=_tip("Opens browser print dialog. Choose 'Save as PDF' to export.")):
                st.session_state[_print_key] = True
        with _dc:
            st.download_button("📄 Download HTML", _html_doc,
                               file_name=f"gematria_{book}_{chapter}_{verse}.html",
                               mime="text/html", key=f"dl_{_uid}",
                               help=_tip("On iPhone/iPad: use this — open the downloaded file in Safari and print from there. On desktop: alternative to the Print button."))
        if st.session_state.get(_print_key):
            _components.html(_html_doc, height=1, scrolling=False)
            st.session_state[_print_key] = False

    # App view has no tabs at all: it is the search page, with the Guide on a
    # separate page reached by button (?page=guide). Unused sections are None
    # and their `with` blocks below are guarded, so their code never runs.
    if app_view:
        tab2 = tab3 = tab4 = None
        _page = st.query_params.get("page")
        if _page in ("guide", "nikud"):
            tab1 = None
            if st.button("← Back to Gematria Search"):
                st.query_params["page"] = "search"
                st.rerun()
            tab_guide = st.container() if _page == "guide" else None
            tab_nikud = st.container() if _page == "nikud" else None
        else:
            tab_guide = tab_nikud = None
            _hd_l, _hd_r = st.columns([3, 1])
            with _hd_l:
                st.title("Tanach Gematria Search")
            with _hd_r:
                if st.button("📖 Guide & Sources"):
                    st.query_params["page"] = "guide"
                    st.rerun()
                if st.button("נִקּוּד Nikud tool"):
                    st.query_params["page"] = "nikud"
                    st.rerun()
            tab1 = st.container()
    else:
        (tab_guide, tab1, tab2, tab3, tab4, tab_nikud) = st.tabs([
            "📖 Guide & Sources",
            "1 · Phrase & Name Matcher",
            "2 · Scriptural Structural Explorer",
            "3 · Textual Echoes & Anomalies",
            "4 · Macro Statistical Dashboard",
            "נִקּוּד Nikud tool",
        ])

    # ======================= NIKUD TOOL =================================
    # Type a word or phrase, get it back vocalized, edit any word from its
    # attested options, then copy it out or send it to the search.
    #
    # It is a TOOL rather than a search-box feature because the search box must
    # resolve to ONE vocalization to compute a value, which forces a guess. Here
    # the ambiguity is just information to show.
    if tab_nikud is not None:
      with tab_nikud:
        st.title("Nikud tool")
        st.markdown(
            "Type a Hebrew word or phrase to add nikud. Where a word has more "
            "than one attested vocalization you can pick between them, then "
            "send the result to the search.")

        _nk_raw = st.text_input(
            "Hebrew word or phrase", key="nk_input",
            placeholder="e.g. דוד בן ישי",
            help=_tip("Looked up in the Tanach first, then a curated name "
                      "list, then a Hebrew dictionary. Anything found nowhere "
                      "is left bare and flagged."))

        if _nk_raw.strip():
            _names = cached_name_index()
            _wikt, _wikt_derived = cached_wiktionary_nikud()
            _words = _nk_raw.split()
            _chosen = []
            _missing = []

            # ⚠️ In "X בן Y" / "X בת Y" the flanking words are PERSONAL NAMES,
            # and without that signal the tool reads them as ordinary corpus
            # words: אבא בן יצחק resolved אבא to אָבֹא, "I will come", because
            # that is what the Tanach attests for those consonants. The
            # relational word is the only cue that a name is meant, so it
            # promotes name-sourced vocalizations for its neighbours.
            #
            # Only the two adjacent words are affected. "בן" elsewhere in a
            # phrase means "son" in the ordinary sense and must not reorder
            # anything.
            _REL = {"בן", "בת", "ben", "bat", "bas"}
            _name_pos = set()
            for _i, _t in enumerate(_words):
                if strip_to_consonants(_t) in _REL or _t.lower() in _REL:
                    if _i:
                        _name_pos.add(_i - 1)
                    if _i + 1 < len(_words):
                        _name_pos.add(_i + 1)

            for _wi, _w in enumerate(_words):
                _bare = strip_to_consonants(_w)
                _entry = _names.get(_bare)
                # Wiktionary is the bottom tier: it fills in only where the
                # corpus and the curated lists have nothing, and otherwise just
                # adds variants below them. It is a modern dictionary, so for a
                # name it would offer the common noun — אבא as "father" rather
                # than the verb attested in the corpus — and must never be
                # allowed to displace the first choice.
                _seen = ({v for o in _entry["options"] for v in o["variants"]}
                         if _entry else set())
                _extra = [f for f in _wikt.get(_bare, []) if f not in _seen]
                # Phrase-derived forms rank below real headwords — they are
                # often bound (construct/prefixed), so they may be offered but
                # must never be the default.
                _bound = [f for f in _wikt_derived.get(_bare, [])
                          if f not in _seen and f not in _extra]
                if not _entry and not _extra and not _bound:
                    _chosen.append(_w)
                    if _bare:
                        _missing.append(_w)
                    continue
                _opts = list(_entry["options"]) if _entry else []
                _is_curated = bool(_entry) and str(
                    _entry.get("source", "")).startswith("curated")
                for _o in _opts:
                    _o.setdefault("curated", _is_curated)
                _opts += [{"form": f, "count": 0, "variants": [f],
                           "wiktionary": True} for f in _extra]
                _opts += [{"form": f, "count": 0, "variants": [f],
                           "wiktionary": True, "bound": True} for f in _bound]

                if _wi in _name_pos and len(_opts) > 1:
                    # Next to בן/בת, prefer a form some source vouches for as a
                    # NAME over one attested only as an ordinary corpus word.
                    # Curated entries are name lists outright, so they lead; a
                    # dictionary headword beats a bound phrase fragment.
                    #
                    # ⚠️ This does NOT reorder corpus forms among themselves.
                    # The index records only frequency, with no name/not-name
                    # flag, and none can be derived reliably — proximity to בן
                    # in the corpus ranks כִּי and אֶחָד above every actual name.
                    # So אבא בן יצחק still leads with the verb אָבֹא: the corpus
                    # attests nothing else for those letters. The correct
                    # אַבָּא is offered directly beneath it and the caption below
                    # says so, which is honest about an ambiguity the data
                    # cannot settle rather than guessing at it.
                    _opts.sort(key=lambda o: (
                        0 if o.get("curated") else
                        1 if not o.get("wiktionary") else
                        2 if not o.get("bound") else 3))
                if len(_opts) == 1:
                    _chosen.append(_opts[0]["form"])
                    continue
                # More than one attested vocalization: offer the choice rather
                # than pick silently. Keyed on the word AND its position, so
                # the same word twice in a phrase can differ.
                _key = f"nk_pick_{_wi}_{_bare}"
                _labels = {
                    o["form"]: (f"{o['form']}"
                                + (f"  ·  {o['count']}× in Tanach"
                                   if o["count"] else "")
                                + ("  ·  dictionary (in a phrase)"
                                   if o.get("bound") else
                                   "  ·  dictionary" if o.get("wiktionary")
                                   else "")
                                + f"  ·  HaNekudot {g_hanekudot(o['form'])}")
                    for o in _opts}
                _pick = st.selectbox(
                    f"{_w} — {len(_opts)} attested forms",
                    [o["form"] for o in _opts],
                    format_func=lambda f, _l=_labels: _l[f],
                    key=_key)
                _chosen.append(_pick)

            _result = " ".join(_chosen)
            st.markdown("**Result**")
            st.code(_result, language=None)

            if _name_pos:
                # The corpus records how often a spelling occurs, not whether
                # it is a name, so a word like אבא leads with the verb the
                # Tanach attests. Say so plainly — the right form is in the
                # dropdown, and only the reader knows which is meant.
                st.caption(
                    "Read as a name: the words beside **בן**/**בת** prefer a "
                    "name vocalization. If a name shares its spelling with an "
                    "ordinary word, check the dropdown.")

            _res_cons = strip_to_consonants(_result)

            if _missing:
                st.warning(
                    "Not vocalized: "
                    + ", ".join(f"**{w}**" for w in _missing)
                    + ". The vowel-mark values need every word pointed, so "
                    "they are not shown. Fix or remove the word to see them.")
            elif _res_cons:
                # ⚠️ Values ONLY when every word was vocalized. With a word left
                # bare the totals are computed over partly-pointed text and come
                # out knowably SHORT — which is exactly the condition
                # nikud_partial suppresses everywhere else in the app. Showing
                # them would present an understated number as a real one.
                _vals = compute_all_ciphers(_res_cons, _result,
                                            word_consonants=_result)
                _vc = st.columns(4)
                for _i, _cm in enumerate(NIKUD_CIPHERS):
                    with _vc[_i]:
                        st.metric(CIPHER_DISPLAY_NAMES.get(_cm, _cm).split(" —")[0],
                                  _vals[_cm])

            # Still offered when a word is unvocalized: only the four vowel-mark
            # methods are undefined, and the other 31 are unaffected by nikud.
            # The label says which case you are in so the handoff is not silent
            # about it.
            _send_label = ("🔍 Send to gematria search" if not _missing else
                           "🔍 Search anyway (vowel-mark methods excluded)")
            if st.button(_send_label, type="primary",
                         width="stretch", key="nk_search"):
                # Hand the vocalized text to Tab 1 exactly as a typed search
                # would arrive, so every downstream path — results, detail
                # panel, export — behaves identically.
                st.session_state["t1_committed"] = {
                    "cons": _res_cons,
                    "raw": _result,
                    "wcons": " ".join(tokenize_words(_result)) or _res_cons,
                }
                st.session_state["t1_mode"] = "Hebrew text"
                st.session_state["nk_sent"] = _result
                if app_view:
                    # App view has real pages, so it can navigate outright.
                    st.query_params["page"] = "search"
                st.rerun()

            if st.session_state.get("nk_sent"):
                # ⚠️ On the SITE the tabs are st.tabs, which cannot be switched
                # programmatically — the search is committed but the reader is
                # still looking at this tab, so without this the button appears
                # to do nothing. Say so explicitly rather than leave them
                # guessing.
                st.success(
                    f"**{st.session_state['nk_sent']}** is loaded into the "
                    "search — open **1 · Phrase & Name Matcher** to see the "
                    "results."
                    if not app_view else
                    f"**{st.session_state['nk_sent']}** sent to the search.")

            st.caption("Select the result text above to copy it.")

    # ===================== TAB GUIDE: GUIDE & SOURCES ==================
    # Guarded: tab_guide is None on the app-view search page.
    if tab_guide is not None:
      with tab_guide:
        st.title("Guide & Sources" if app_view else
                 "Tanach Gematria Search & Structural Pattern Engine")
        st.markdown(
            "Search the Tanach by gematria. Enter a Hebrew word, name, or "
            "phrase to find every word, phrase, or verse that shares its "
            f"value under any of {N_CIPHERS} methods."
            + ("" if app_view else
               " The other tabs browse the text by structure, surface "
               "recurring patterns, and chart the corpus statistically.")
        )
        with st.expander("How to use this app", expanded=True):
            st.markdown("**Gematria Search**" if app_view else
                        "**1 · Phrase & Name Matcher**")
            st.markdown(
                "Type any Hebrew word, name, or phrase to see its value under "
                "each method.\n\n"
                "Select a method to see every matching unit in the Tanach. "
                "Click a result to open the full verse with the match "
                "highlighted and a letter-by-letter breakdown.\n\n"
                "Toggle **כולל (±1)** on to also match values one above or "
                "below.\n\n"
                "**🔍 All word-span matches** finds runs of consecutive words "
                "whose combined value matches, optionally carrying across a "
                "verse boundary.\n\n"
                "**🔀 Cross-method matches** compares every value of your "
                "input against every method, highlighting rare matches."
            )

            if not app_view:
                st.markdown("**2 · Scriptural Structural Explorer**")
                st.markdown(
                    "Browse the entire Tanach by structural unit: Chapter (פרק Perek), "
                    "book (ספר Sefer), open paragraph (Pesucha פ), "
                    "closed paragraph (Setuma ס), or individual Verse (פסוק). "
                    f"Every row shows gematria totals under the {N_CIPHERS} methods for that block."
                    "Click a row to open the verse detail panel."
                )

            if not app_view:
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
                    f"value-distribution histograms, a {N_HEATMAP_CIPHERS}-method correlation heatmap, a per-book fingerprint "
                    "chart, and integer ranges with no verse representation. All charts are interactive — "
                    "hover, zoom, and download. A **cross-method half-verse balance heatmap** at the "
                    "bottom shows, for every method pair, the fraction of verses whose first half "
                    "(row method) equals the second half (column method)."
                )


        st.divider()
        st.subheader("📖 Reference material")
        st.caption(
            "Method rules describe exactly what the engine computes. "
            "Attributions give the earliest known source for each method, "
            "Talmudic or later."
        )

        with st.expander(f"The {N_CIPHERS} gematria methods"):
            st.caption(
                f"These {N_CIPHERS} are the methods implemented here, not a complete "
                "catalogue — the tradition contains many more, and further "
                "variants can be formed by combining them. Each is listed with "
                "the earliest source known for it.")
            # ⚠️ Sorted at render time, not hand-ordered. The rows below are a
            # literal list that had drifted into roughly DB order, putting
            # Atbash at row 21; sorting here keeps the Guide in step with every
            # other list in the app automatically, so adding a method in any
            # position still lands it in the right place.
            st.table(pd.DataFrame(sorted([
                {"Method": "Standard",
                 "Hebrew": "מספר הכרחי / ישר (Mispar Hechrachi)",
                 "Rule": "Standard values: א=1 … י=10, כ=20 … ק=100 … ת=400. Finals = same as base form.",
                 "Source": "The 29th of the ל\"ב middos of R' Eliezer ben Yose ha-Gelili (c. 200 CE). סנהדרין ל\"ח ע\"א; נדרים ל\"ב ע\"א (Avraham's 318 men = אֱלִיעֶזֶר). The term גימטריאות appears in אבות ג׳:י״ח."},
                {"Method": "Katan",
                 "Hebrew": "מספר קטן (Mispar Katan)",
                 "Rule": "Reduce each letter to its significant digit (drop trailing zeros: ק=1, מ=4), then sum.",
                 "Source": "חסידי אשכנז (12th–13th c.); brought in ספר גימטריאות, attributed to R' Yehuda haChassid (d. 1217). The Remak notes at פרדס רימונים שער ל׳ פרק ח׳ that מספר קטן is what most call this reduction — his own first method under that heading is מספר המעוגל, the wraparound."},
                {"Method": "Gadol",
                 "Hebrew": "מספר גדול (Mispar Gadol)",
                 "Rule": "Like Standard, but final forms carry 500–900: ך=500, ם=600, ן=700, ף=800, ץ=900.",
                 "Source": "The sofios themselves are early — מנצפ\"ך צופים אמרום (מגילה ב׳ ע״ב), and אטב\"ח of R' Chiya (סוכה נ״ב ע״ב) needs the 500–900 tier for its pairs to reach 1000. Set out systematically in שפתי ישנים (R' Shabsai Bass, 17th c.)."},
                {"Method": "KatanMispari",
                 "Hebrew": "קטן מספרי (Mispar Katan Mispari)",
                 "Rule": "Sum all Standard values first; then iteratively reduce the grand total to a single digit (digital root). Differs from Katan, which reduces each letter before summing.",
                 "Source": "Brought in פרדס רימונים (שער ל׳)."},
                {"Method": "Siduri",
                 "Hebrew": "מספר סידורי (Mispar Siduri)",
                 "Rule": "Ordinal position: א=1, ב=2 … ת=22. Sequence, not standard value.",
                 "Source": "פרדס רימונים, שער הגימטריאות (שער ל׳), the Remak (1548). Letter-position counting is already implicit in שבת ק״ד ע״א."},
                {"Method": "ReverseOrdinal",
                 "Hebrew": "מספר אחור סידורי (Reverse Ordinal)",
                 "Rule": "Reverse alphabetical index: ת=1, ש=2, ר=3 … א=22. The inverse of Siduri.",
                 "Source": "חסידי אשכנז (12th–13th c.); brought in ספר רזיאל המלאך."},
                {"Method": "Ribua",
                 "Hebrew": "מספר מרובע / פרטי (Mispar Meruba Prati)",
                 "Rule": "Square each individual letter's Standard value, then sum all squares (Σ vᵢ² — per letter, not the total squared).",
                 "Source": "Used widely by the בעל הטורים (R' Yaakov ben Asher, 14th c.) in his peirush on the Torah. Also in פרדס רימונים (שער ל׳)."},
                {"Method": "HaMerubahKlali",
                 "Hebrew": "מספר המרובע הכללי (Mispar HaMerubah HaKlali)",
                 "Rule": "The entire Standard sum squared as one integer: (Σv)². Unlike Ribua, which squares per letter. "
                         "Searching it alone returns the same matches as Standard; its use is in Cross-method matches.",
                 "Source": "פרדס רימונים (the Remak, שער ל׳)."},
                {"Method": "Kidmi",
                 "Hebrew": "מספר קדמי (Mispar Kidmi / HaKadmon)",
                 "Rule": "Cumulative prefix sum of Standard values: each letter's value = Σ Standard values from א up to and including it. א=1, ב=3, ג=6, ד=10 … ת=1495.",
                 "Source": "Laid out in פרדס רימונים (שער ל׳, פרק ח׳) by the Remak (1548)."},
                {"Method": "Milui",
                 "Hebrew": "מילוי / מספר שמי (Mispar Milui)",
                 "Rule": "Spell each letter's full name as a Hebrew word, then sum Standard values of all spelling letters. א=אלף=111, ב=בית=412, ח=חית=418 …",
                 "Source": "Used in the Zoharic ספרא דצניעותא. פרדס רימונים, שער ל׳ (the Remak, 1548)."},
                {"Method": "Neelam",
                 "Hebrew": "נעלם (Mispar Neelam — Hidden)",
                 "Rule": "Like Milui, but drop the first letter of each spelling — only the hidden remainder counts. א→לף=110, ח→ית=410 …",
                 "Source": "פרדס רימונים (שער הגימטריאות, שער ל׳, the Remak, 1548)."},
                {"Method": "Emtzaiyot",
                 "Hebrew": "אמצעיות (Emtzaiyot — Middle Letters)",
                 "Rule": "Standard value of the second letter of each letter's Milui name. Uses the 2-letter spellings of the Arizal: אלף→ל=30, בית→י=10. ה (הא) and פ (פא) have two-letter names, so the second letter is also the last — for these the value matches Ofanim.",
                 "Source": "The Arizal's letter-name spellings; brought in ספר רזיאל המלאך."},
                {"Method": "Ofanim",
                 "Hebrew": "אופנים (Ofanim — Wheels)",
                 "Rule": "Replace each letter with the final letter of its Milui name spelling, take Standard value.",
                 "Source": "ספר רזיאל המלאך."},
                {"Method": "HaNekudot",
                 "Hebrew": "מספר הנקודות (Mispar HaNekudot)",
                 "Rule": "Each vowel mark scored by its shape: every dot=10, every line=6. Sheva=20, Patah=6, Kamatz=16, Hiriq=10, Tsere=20, Segol=30, Holam=10, Kubutz=30, Shuruk=10. The chatafim are a sheva plus their base vowel: chataf patah=26, chataf kamatz=36, chataf segol=50. The dagesh, taamim and the shin/sin dot are not nekudos and score 0.",
                 "Source": "תיקוני הזהר תיקון ע׳: a dot is a yud, a line is a vav (נקודה איהי י׳, וקוא איהו ו׳). The shapes of the nekudos are in פרדס רימונים שער כ״ח (שער הנקודות) פרק א׳, where the Remak names the chatafim as שבא קמץ, שבא פתח, שבא סגול. עץ חיים שער ה׳ excludes the dagesh: דגש ורפה אינם לא טעמים ולא נקודות ולא תגין."},
                {"Method": "ImHaNekudot",
                 "Hebrew": "עם הנקודות (Im HaNekudot — With the Vowels)",
                 "Rule": "Standard gematria of the consonants plus HaNekudot value of the vowel marks. Combines consonant totals with vowel-mark geometric values in a single sum.",
                 "Source": "פרדס רימונים (the Remak, 1548), שער הגימטריאות (שער ל׳), פרק ח׳."},
                {"Method": "MiluiNekudot",
                 "Hebrew": "מילוי הנקודות (Mispar Milui HaNekudot)",
                 "Rule": "Standard gematria of each vowel mark's Hebrew name. שבא=303, חירק=318, צירי=310, סגול=99, פתח=488, קמץ=230, חולם=84, קובוץ=204, שורק=606. The chatafim are named as the Remak names them, a sheva with their base vowel: חטף פתח=791, חטף קמץ=533, חטף סגול=402.",
                 "Source": "R' Yosef Gikatilla, גנת אגוז (1274), in the section on the sod of the nekudos. The spellings used here are the Remak's in פרדס רימונים שער כ״ח."},
                {"Method": "ImMiluiNekudot",
                 "Hebrew": "עם מילוי הנקודות (Im Milui HaNekudot)",
                 "Rule": "Standard gematria of the consonants plus Milui HaNekudot (vowel-mark name values). Combines the two layers: consonant totals + gematria of each vowel mark's name.",
                 "Source": "Combines R' Yosef Gikatilla's מילוי הנקודות (גנת אגוז, 1274) with the Remak's עם הנקודות (פרדס רימונים, 1548). No single source gives this exact combination."},
                {"Method": "MiluiMaleh",
                 "Hebrew": "מילוי מלא (Milui Maleh — Full Filling)",
                 "Rule": "Like Milui, but uses the Maleh (מלא) 3-letter spellings for כ and מ: כ=כאף=101, מ=מאם=81. All other letter spellings are identical to standard Milui.",
                 "Source": "kisvei haAri (cf. שער הכוונות) and Sephardic sources, following the scribal tradition of כתיב מלא and כתיב חסר."},
                {"Method": "NeelAmMaleh",
                 "Hebrew": "נעלם מלא (Neelam Maleh — Full Hidden)",
                 "Rule": "Like Neelam, but with Maleh 3-letter spellings: כ→אף=81, מ→אם=41. Reveals an additional Alef hidden inside each of these letters.",
                 "Source": "The maleh spellings of מילוי מלא (kisvei haAri) applied to the נעלם."},
                {"Method": "EmtzaiyotMaleh",
                 "Hebrew": "אמצעיות מלא (Emtzaiyot Maleh — Full Middle)",
                 "Rule": "Like Emtzaiyot, but with Maleh 3-letter spellings. Both כ (כאף) and מ (מאם) now yield א=1 as their inner letter, fully distinct from their Ofanim value. אלף→ל=30, בית→י=10, כאף→א=1, מאם→א=1.",
                 "Source": "The maleh spellings of מילוי מלא (kisvei haAri) applied to אמצעיות."},
                {"Method": "Atbash",
                 "Hebrew": "אתב\"ש (At-Bash)",
                 "Rule": "Mirror the alphabet: א↔ת, ב↔ש, ג↔ר … then Standard values of the swapped letters.",
                 "Source": "שֵׁשַׁךְ in ירמיהו כ״ה:כ״ו and נ״א:מ״א is בָּבֶל by atbash. Stated explicitly in סנהדרין כ״ב ע״ב."},
                {"Method": "Albam",
                 "Hebrew": "אלב\"ם (Al-Bam)",
                 "Rule": "Split 22 letters into two groups of 11; swap across groups: א↔ל, ב↔מ, ג↔נ … (ROT-11).",
                 "Source": "Spelled out in ילקוט שמעוני (יתרו, רמז רע\"א). Listed among the temuros by Jastrow and the Dictionary of the Talmud."},
                {"Method": "Achbi",
                 "Hebrew": "אכב\"י (Ach-Bi)",
                 "Rule": "Split into two 11-letter groups, reverse each internally: א↔כ, ב↔י … ל↔ת, מ↔ש …",
                 "Source": "Set out as a grid in ספר רזיאל המלאך; brought by the Radal on פרקי דרבי אליעזר."},
                {"Method": "Atbach",
                 "Hebrew": "אטב\"ח (At-Bach)",
                 "Rule": "Pairs whose values sum to 10/100/1000: א↔ט, ב↔ח; י↔צ, כ↔פ; ק↔ץ … Finals carry 600–900.",
                 "Source": "From R' Chiya (late 2nd/early 3rd c. CE). The phrase בְּאַטְבַּ\"ח שֶׁל רַבִּי חִיָּיא appears in סוכה נ״ב ע״ב. Also counted in the ל\"ב middos of R' Eliezer ben Yose ha-Gelili."},
                {"Method": "Avgad",
                 "Hebrew": "אבג\"ד (Av-Gad / Abgad)",
                 "Rule": "+1 cyclic shift: א→ב, ב→ג … ת→א. Then Standard values of the shifted letters. Also known as Mispar Ha'Ahari (next-letter value).",
                 "Source": "Brought in טעם זקנים (R' Eliezer Ashkenazi). R' Avraham Abulafia (13th c.) uses the next-letter method in his sefarim."},
                {"Method": "Agdat",
                 "Hebrew": "אגד\"ת (Ag-Dat)",
                 "Rule": "+2 cyclic shift: א→ג, ב→ד … ש→א, ת→ב. Then Standard values of the shifted letters.",
                 "Source": "פרדס רימונים, שער כ״ב (the Remak, 1548)."},
                {"Method": "ReverseAvgad",
                 "Hebrew": "אבג\"ד הפוך (Reverse Avgad)",
                 "Rule": "−1 cyclic shift: Bet→Alef, Gimel→Bet … Alef wraps to Tav. Opposite of Avgad.",
                 "Source": "R' Eliezer Ashkenazi, טעם זקנים."},
                {"Method": "AyakBachar",
                 "Hebrew": "אי\"ק בכ\"ר (Ayak Bachar)",
                 "Rule": "3×9 cyclic rotation across units/tens/hundreds columns: א→י→ק→א, ב→כ→ר→ב … ט→צ→ץ→ט.",
                 "Source": "תיקוני הזהר (תיקון כ״א)."},
                {"Method": "AchasBeta",
                 "Hebrew": "אח\"ס בט\"ע (Achas Beta)",
                 "Rule": "22 letters in three blocks of 7/7/7 cycle positionally; ת stands outside and is invariant.",
                 "Source": "פרדס רימונים (the Remak, שער ל׳)."},
                {"Method": "Boneeh",
                 "Hebrew": "מספר בונה (Mispar Bone'eh) — a modern label; classically מספר האחוריים",
                 "Rule": "Cumulative prefix sums per word: letter 1 alone, then 1+2, then 1+2+3 … Resets at each word boundary. Not to be confused with HaAchor (מספר האחור), which multiplies each letter by its position.",
                 "Source": "ספר עץ חיים ל״ד:ב׳ — the אחוריים of a Name, written out progressively. שער הפסוקים (וישלח) counts the letters of those expansions."},
                {"Method": "HaAchor",
                 "Hebrew": "מספר האחור (Mispar HaAchor)",
                 "Rule": "Each letter × its ordinal position within the word (1st×v₁ + 2nd×v₂ + …). Position resets per word.",
                 "Source": "No source located. Not among the Remak's nine in פרדס רימונים שער ל׳, and no attestation found for מספר האחור under that or any variant name."},
                {"Method": "Mispari",
                 "Hebrew": "מספר המספריי (Mispar HaMispari)",
                 "Rule": "Spell each letter's Standard value as a Hebrew number-word, then sum the values of those words. י=10→עשרה=575; ה=5→חמשה=353; א=1→אחד=13. Follows the Remak's own masculine spellings.",
                 "Source": "פרדס רימונים, שער הגימטריאות (שער ל׳) §8 — the Remak (1548)."},
                {"Method": "MispariHaGadol",
                 "Hebrew": "מספריי הגדול (Mispar HaMispari HaGadol)",
                 "Rule": "Spell each letter's MILUI total as a Hebrew number-word, then sum those words' values. י→יוד=20→עשרים=620; א→אלף=111→מאה ואחד עשר.",
                 "Source": "פרדס רימונים, שער הגימטריאות (שער ל׳) §9 — the Remak (1548): 'יו\"ד במילואו עשרים, ועשרים בגימט' כתר'. He brings it on a single letter as a remez, and spells only that one number, so the compound spellings here are reconstructed."},
                {"Method": "KololEhad",
                 "Hebrew": "כולל (Kolel — Word)",
                 "Rule": "Standard total + 1. The word counted as one additional unit. Standard ±1 adjustment to link words differing by one.",
                 "Source": "בעל הטורים (R' Yaakov ben Asher, 14th c.). Defined in פרדס רימונים, שער הגימטריאות (שער ל׳) §4 as the second half of מספר מוספי — 'או המלה עצמה', or the word itself."},
                {"Method": "KololOtiyot",
                 "Hebrew": "כולל אותיות (Kolel — Letters / Mispar Musafi)",
                 "Rule": "Standard total + letter count. Each letter adds 1 beyond its gematria value. Also called Mispar Musafi.",
                 "Source": "פרדס רימונים, שער הגימטריאות (שער ל׳) §4, the Remak (1548): 'מספר מוספי הוא שמוסיפין האותיות מן המלה על המספר או המלה עצמה'."}
            ], key=lambda r: (CIPHER_DISPLAY_ORDER.index(r["Method"])
                              if r["Method"] in CIPHER_DISPLAY_ORDER
                              else len(CIPHER_DISPLAY_ORDER)))))

        with st.expander("Boundary types"):
            st.dataframe(pd.DataFrame([
                {"Boundary": "Word (תיבה)",      "Meaning": "Single word token, split on space and maqaf (־).",                                             "Why meaningful": "Smallest meaning-bearing unit; classic gematria target (name totals, first/last words)."},
                {"Boundary": "Zakef phrase (זָקֵף)",   "Meaning": "Phrase ending at a zakef katon (֔), tipcha (֖) or atnach (֑).",                          "Why meaningful": "The finest cantillation phrase — the smallest unit the Masoretic accents mark off above a word."},
                {"Boundary": "Tipcha phrase (טִפְחָא)", "Meaning": "Phrase ending at a tipcha (֖) or atnach (֑).",                                          "Why meaningful": "A sub-half phrase: coarser than a zakef phrase, finer than a half-verse."},
                {"Boundary": "Half-verse (חצי פסוק)",  "Meaning": "The two halves of a verse, split at the Asnachta (֑).",                                  "Why meaningful": "The Asnachta is the verse's primary cantillation pause — its main syntactic division. Balance between the halves is a recognized gematria pattern."},
                {"Boundary": "Verse (פסוק)",      "Meaning": "One Masoretic verse, ending at Sof Pasuq (׃).",                                               "Why meaningful": "The canonical citation and reading unit."},
                {"Boundary": "Pesucha / Petucha (פ)", "Meaning": "'Open' paragraph — a full blank line to end of scroll column; a major thematic break.",     "Why meaningful": "A deliberate Masoretic division, larger than a verse. One of two authentic paragraph units."},
                {"Boundary": "Setuma (ס)",        "Meaning": "'Closed' paragraph — a short gap mid-line; a minor thematic break.",                           "Why meaningful": "The finer Masoretic paragraph division. Both Petucha and Setuma predate chapter numbering."},
                {"Boundary": "Perek (פרק)",       "Meaning": "Chapter boundary.",                                                                             "Why meaningful": "Introduced ~13th century CE (not a Masoretic unit). Convenient macro-aggregation for reference."},
                {"Boundary": "Sefer (ספר)",       "Meaning": "One book of the Tanach.",                                                                       "Why meaningful": "The largest aggregation level; totals across a whole book."},
            ]), width="stretch", hide_index=True)

        # App view searches the Ksiv track only, so the reading-track material
        # would describe controls and results the app never shows. State the
        # scope instead. (Planned: replace tracks with a variants toggle that
        # flags the few verses that actually differ — see HANDOFF.)
        if app_view:
            st.info(
                "**Reading text:** searches the **Ksiv (כְּתִיב)** — the "
                "consonantal text as written. Kri (קְרֵי) readings and textual "
                "variants are not included.")
        # Guarded with a two-space-indented `with` so the ~40-line body keeps its
        # original indentation — same trick the tab guards use.
        if not app_view:
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
            ]), width="stretch", hide_index=True)
            st.markdown("**Esther doublets**")
            st.dataframe(pd.DataFrame([
                {"Reference": f"{b} {c}:{v}", "Received": spec["from"],
                 "Variant": spec["to"], "Note": spec["note"]}
                for (b, c, v), spec in TEXTUAL_VARIANT_SPECS.items()
                if spec["category"] == "Doublet"
            ]), width="stretch", hide_index=True)
            st.markdown("""
**Aggregate** — Structural totals (Perek/Sefer sums from Ksiv verses). Not a text variant; a statistical macro-unit.

---
**Tiqqune Sopherim (תיקוני סופרים) — 18 scribal corrections (documented, not engine-forked)**

These 18 places are where the Masoretic tradition records that scribes emended the text — mainly to remove anthropomorphisms or avoid theological offence. The received Masoretic text already contains the corrected reading. The "original" wording is preserved in rabbinic literature (Mekhilta, Sifre Num. §84, Yalkut Shimoni, Tanḥuma). Note: the exact list of 18 varies across sources.
""")
            st.dataframe(pd.DataFrame(TIQQUNE_SOPHERIM), width="stretch", hide_index=True)
            st.markdown("""
**Doublet passages (documented, not engine-forked)**

These are separate references that share nearly identical text — two distinct verses in two different books, not two readings of one verse. The fork engine doesn't apply here; they are best studied by comparing the two passages directly.
""")
            st.dataframe(pd.DataFrame(DOUBLET_PASSAGES), width="stretch", hide_index=True)

        with st.expander("The Rule of the Colel (כּוֹלֵל)"):
            st.markdown("""
The *Colel* (כּוֹלֵל, "the inclusive / the whole") permits adding or subtracting **1** to a gematria total — conventionally counting "the word itself" or "the number as a unit" as one extra. A match within ±1 of the target is treated as equivalent.

This principle appears throughout Kabbalistic and Hasidic commentary and is invoked by various authorities (including the Vilna Gaon and Baal HaTurim–style annotations). Its precise origin is diffuse; present it as a traditional/widely-used principle rather than pinning it to a single text.

**How the toggle works:** when enabled, a search matches values one above and one below the target.

**Methods the toggle deliberately skips** (their matches stay exact even with כולל on):

- **KololEhad / KololOtiyot** — the kolel adjustment *is* the method; applying the tolerance too would count the same leniency twice.
- **KatanMispari** — a digital root has only 9 possible values, so a ±1 window would span a third of the whole space and matches would stop meaning anything.
- **HaMerubahKlali** — the squared grand total is not an additive sum; "counting the word itself as one" has no coherent referent there, since (S+1)² ≠ S²+1.
- **HaNekudot** — every vowel-mark value is even (dot = 10, line = 6), so every total is even and a ±1 (odd) target can never equal another HaNekudot value.
""")

        # Texts and licences. The bundled translation is CC-BY-NC, whose
        # attribution requirement is a licence condition rather than a
        # courtesy, so it has to be visible somewhere durable rather than only
        # in the detail panel that shows it. Sits last: it is reference
        # material, not an introduction.
        st.divider()
        with st.expander("Texts & licences"):
            st.markdown(
                "**Hebrew** (all calculations) — *Tanach with Ta'amei "
                "Hamikra*, from [tanach.us](http://www.tanach.us/Tanach.xml) "
                "via [Sefaria](https://www.sefaria.org). Public Domain.\n\n"
                "**English** (display only) — *The Koren Jerusalem Bible*, "
                "© Koren Publishers Jerusalem, via Sefaria. "
                "[CC BY-NC 4.0]"
                "(https://creativecommons.org/licenses/by-nc/4.0/). "
                "Joshua 21:36–37, which it omits, are from the "
                "public-domain JPS 1917.\n\n"
                "Translations are shown for the whole verse — they are "
                "sense-for-sense, with no word-level alignment to the Hebrew.\n\n"
                "This application is licensed CC BY-NC 4.0."
            )

    # ======================= TAB 1: PHRASE MATCHER =======================
    # Guarded: tab1 is None on the app-view guide page.
    if tab1 is not None:
      with tab1:
        if not app_view:
            st.subheader("Phrase & Name Matcher")
        mode = st.radio("Search by",
                        ["Hebrew text", "Gematria value", "Verse reference"],
                        horizontal=True, key="t1_mode")

        if mode == "Hebrew text":
            # ── Simple text input (keyboard widget removed — see commented block below) ──
            c1, c2 = st.columns([4, 2])
            with c1:
                # The input and its Search button live in a form so that typing
                # and clicking Search works in one go. st.text_input only sends
                # its value to the server on Enter or blur, so a bare button had
                # to be disabled until then (the value, hence `cons`, was still
                # empty) — and simply enabling it hits Streamlit's two-click
                # problem, where the first click merely blurs the input and is
                # swallowed. A form's submit button collects the current widget
                # values and submits them in a single round trip. Enter still
                # submits, since that is a form's built-in behaviour.
                with st.form("t1_text_search", border=False):
                    raw = st.text_input(
                        "Hebrew phrase or name", key="t1_hebrew",
                        placeholder="e.g. שלום",
                        help=_tip("Nikud and ta'amim are ignored for most ciphers. "
                                  "For HaNekudot / ImHaNekudot / MiluiNekudot / ImMiluiNekudot, include nikud for accurate results."))
                    submitted = st.form_submit_button(
                        "🔍 Search", type="primary", width="stretch")
            with c2:
                colel = st.toggle("כולל (±1)", value=False,
                                  key="t1_text_colel",
                                  help=_tip("Rule of the Colel: also match "
                                            "Value−1 and Value+1. Not applied "
                                            "to methods where ±1 is built in "
                                            "or meaningless (Kolel methods, "
                                            "KatanMispari, HaMerubahKlali, "
                                            "HaNekudot)."))

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
            #               help="Toggle Hebrew on-screen keyboard", width="stretch")
            #     st.markdown("</div>", unsafe_allow_html=True)
            # _kbd_slot = st.empty()
            # if kbd_open:
            #     with _kbd_slot.container(border=True):
            #         for _row in ["יטחזוהדגבא", "רקצפעסנמלכ", "ץףןםךתש"]:
            #             _cols = st.columns(len(_row))
            #             for _col, _ch in zip(_cols, _row):
            #                 _col.button(_ch, key=f"hk_{_ch}", on_click=_kbd_add,
            #                             args=(_ch,), width="stretch")
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
            #                             on_click=_kbd_add, args=(_mark,), width="stretch")
            #         _ctl1, _ctl2, _ctl3 = st.columns(3)
            #         _ctl1.button("Space", key="hk_space", on_click=_kbd_add,
            #                      args=(" ",), width="stretch")
            #         _ctl2.button("⌫ Delete", key="hk_bksp",
            #                      on_click=_kbd_bksp, width="stretch")
            #         _ctl3.button("✕ Clear", key="hk_clear",
            #                      on_click=_kbd_clear, width="stretch")
            # ── END ON-SCREEN KEYBOARD ────────────────────────────────────────────────

            cons = normalize_query(raw)
            word_cons = " ".join(tokenize_words(raw))
            # TODO(site): revisit whether the full site still needs this readout.
            # Dropped from app view as clutter — the search echoes the query back
            # in its results heading anyway.
            if not app_view:
                st.markdown(f"**Cleaned consonants:** `{cons or '—'}`")
            if submitted:
                if cons:
                    st.session_state["t1_committed"] = {
                        "cons": cons, "raw": raw, "wcons": word_cons}
                else:
                    # The button is always enabled now, so an empty submit is
                    # possible and should say why nothing happened.
                    st.warning("Enter a Hebrew phrase or name to search.")
        elif mode == "Verse reference":
            # Free text is the PRIMARY input: this audience knows references
            # cold and types "בראשית א:א" faster than it works three dropdowns.
            # The cascading selects are a site-only fallback.
            #
            # ⚠️ The selects must NOT go inside an st.form — widgets in a form
            # do not report until submit, so the chapter list could never
            # narrow to the book just chosen.
            #
            # ⚠️ Every DB read here goes through a cached_* function. Ad-hoc
            # raw_conn(conn).execute() in this branch segfaulted the Space
            # (exit 139, reverted in 2b7b710).
            _vs_ref = None
            # ⚠️ "Use this reference" cannot assign to t1_vs_ref: Streamlit
            # raises StreamlitAPIException if a session-state key is written
            # after its widget has been instantiated, and the text input above
            # owns that key. The button therefore writes a SEPARATE durable
            # key, which seeds the widget's `value` on the following rerun.
            # (Same class of problem as the on-screen keyboard's buffer, which
            # is why that one keeps _KBD_BUF distinct from the widget key.)
            _vs_seed = st.session_state.pop("t1_vs_seed", None)
            if _vs_seed is not None:
                st.session_state["t1_vs_ref"] = _vs_seed
            _vs_raw = st.text_input(
                "Verse reference", key="t1_vs_ref",
                placeholder="e.g. Genesis 1:1, בראשית א:א, Bereishis 1:1",
                help=_tip("English, yeshivish or Hebrew book names. "
                          "Chapter and verse may be digits or Hebrew "
                          "letters."))
            if _vs_raw.strip():
                _vs_ref = parse_verse_ref(_vs_raw)
                if _vs_ref is None:
                    st.warning("Could not read that reference. Try "
                               "'Genesis 1:1' or 'בראשית א:א'.")
            # Browse sits directly under the reference box and ABOVE the colel
            # toggle: it is part of choosing what to search, whereas colel is a
            # property of the search itself.
            #
            # Shown in BOTH views. It was app-view-gated at first, on the
            # theory that three dropdowns are wrong for a phone — but that
            # simply removed the only non-typing way in, and reads as the box
            # having vanished. On a narrow screen the columns stack, which is
            # fine. Collapsed by default: typing the reference is the primary
            # path, so browsing should not take up the screen unasked.
            with st.expander("Browse for a reference"):
                _ridx = cached_ref_index(conn, corpus_key)
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    # Canonical Tanach order, not alphabetical — a reader
                    # looks for Shemos after Bereishis, not after Ruth.
                    _b_opts = [b for b in BOOK_ORDER if b in _ridx]
                    _b_sel = st.selectbox("Book", _b_opts,
                                          format_func=book_label,
                                          key="t1_vs_book")
                with bc2:
                    _c_sel = st.selectbox("Chapter",
                                          sorted(_ridx.get(_b_sel, {})),
                                          key="t1_vs_ch")
                with bc3:
                    _v_sel = st.selectbox(
                        "Verse", _ridx.get(_b_sel, {}).get(_c_sel, []),
                        key="t1_vs_v")
                if st.button("Use this reference", key="t1_vs_use",
                             width="stretch"):
                    # Writes the SEED key, not the widget key — see above.
                    # The canonical name goes in the box: parse_verse_ref
                    # resolves both spellings, and this keeps the committed
                    # value aligned with the stored `book` column.
                    st.session_state["t1_vs_seed"] = f"{_b_sel} {_c_sel}:{_v_sel}"
                    st.rerun()
            colel = st.toggle("כולל (±1)", value=False, key="t1_vs_colel",
                              help=_tip("Rule of the Colel: also match "
                                        "Value−1 and Value+1."))

            if _vs_ref:
                _vb, _vc, _vv = _vs_ref
                _vobj = verse_index.get((_vb, _vc, _vv))
                if _vobj is None:
                    st.warning(f"{book_label(_vb)} {_vc}:{_vv} is not in the loaded corpus.")
                else:
                    _unit_opts = cached_verse_units(conn, corpus_key, _vb, _vc, _vv)
                    _unit = st.selectbox(
                        "Search which unit?", _unit_opts,
                        format_func=lambda u: BOUNDARY_LABELS.get(u, u),
                        key="t1_vs_unit",
                        help=_tip("The whole verse, or a part of it."))
                    _rows = cached_verse_unit_rows(conn, corpus_key, _vb, _vc,
                                                   _vv, _unit)
                    _pick = None
                    if len(_rows) == 1:
                        _pick = _rows[0]
                    elif _rows:
                        _labels = {r[0]: (r[2] or r[1]) for r in _rows}
                        _sid = st.selectbox(
                            "Which one?", [r[0] for r in _rows],
                            format_func=lambda s: _labels.get(s, s),
                            key="t1_vs_sub")
                        _pick = next((r for r in _rows if r[0] == _sid), None)
                    if _pick:
                        _p_cons, _p_disp, _p_partial = _pick[1], _pick[2], _pick[3]
                        # ⚠️ QUERY-SIDE nikud gate. Every other nikud_partial
                        # check in this file is a corpus-side SQL predicate;
                        # without this the query itself could be scored on a
                        # knowably-short vowel total and then searched,
                        # returning real-looking matches for a value that
                        # should not exist. Read from the SELECTED UNIT's own
                        # row, never the parent verse's.
                        if _p_partial:
                            st.info(
                                "This unit contains a Ksiv word the source "
                                "prints without nikud, so the four vowel-mark "
                                "methods are undefined here and are not "
                                "offered. The other methods are unaffected.")
                        # Pointed text: known outright for a whole verse; for a
                        # sub-unit it must be located inside the verse, as Tab 2
                        # does for a matched row.
                        if _unit == "Verse":
                            _p_raw = _vobj.text
                            _p_w = " ".join(tokenize_words(_vobj.text))
                        elif _unit == "FirstHalf":
                            _p_raw = locate_vocalized(_vobj.text, _p_cons)
                            _p_w = split_halves_word_cons(_vobj.text)[0]
                        elif _unit == "SecondHalf":
                            _p_raw = locate_vocalized(_vobj.text, _p_cons)
                            _p_w = split_halves_word_cons(_vobj.text)[1]
                        else:
                            _p_raw = locate_vocalized(_vobj.text, _p_cons)
                            _p_w = (" ".join(tokenize_words(_p_raw))
                                    if _p_raw else _p_cons)
                        import html as _h_esc
                        st.markdown(
                            f"**{book_label(_vb)} {_vc}:{_vv}** · "
                            f"_{BOUNDARY_LABELS.get(_unit, _unit)}_")
                        st.markdown(
                            f"<div dir='rtl' style='font-size:1.15rem'>"
                            f"{_h_esc.escape(_p_disp or _p_cons)}</div>",
                            unsafe_allow_html=True)
                        _vs_note = disputed_verse_note(_vb, _vc, _vv)
                        if _vs_note:
                            st.info(_vs_note)
                        if st.button("🔍 Search this unit", type="primary",
                                     key="t1_vs_go", width="stretch"):
                            st.session_state["t1_committed"] = {
                                "cons": _p_cons, "raw": _p_raw or _p_cons,
                                "wcons": _p_w or _p_cons,
                                "label": f"Selected Unit ({book_label(_vb)} {_vc}:{_vv})",
                                "ref": (_vb, _vc, _vv),
                                # Full unit identity, boundary included, so the
                                # searched unit can be dropped from its own
                                # results. Set ONLY in verse mode: a typed
                                # Hebrew query is a string, not a corpus
                                # reference, so nothing there is self-matching.
                                "unit": (_vb, _vc, _vv, _unit),
                                "nikud_partial": bool(_p_partial),
                            }
        else:
            nc1, nc2 = st.columns([3, 2])
            with nc1:
                num_raw = st.number_input(
                    "Gematria value", min_value=1, max_value=10_000_000,
                    value=2701, step=1, key="t1_num",
                    help=_tip("Search every method for corpus units equal to this value."))
            with nc2:
                colel = st.toggle("כולל (±1)", value=False,
                                  key="t1_num_colel",
                                  help=_tip("Rule of the Colel: also match "
                                            "Value−1 and Value+1. Not applied "
                                            "to methods where ±1 is built in "
                                            "or meaningless (Kolel methods, "
                                            "KatanMispari, HaMerubahKlali, "
                                            "HaNekudot)."))
            target = int(num_raw)
            cons = ""
            word_cons = ""

        _BOUND_OPTS = ["Perek", "Sefer", "Verse", "Petucha", "Setuma",
                       "FirstHalf", "SecondHalf",
                       "TiphchaPhrase", "ZakefPhrase", "Word"]
        # App view defaults to the two units people actually search on a phone;
        # the half-verse units stay available but off by default.
        _BOUND_DEFAULT = (["Verse", "Word"] if app_view
                          else ["Verse", "FirstHalf", "SecondHalf", "Word"])
        if app_view:
            # App view is Ksiv-only. The variant tracks agree with Ksiv across
            # the overwhelming majority of the corpus, so the selector spends
            # scarce phone screen space and adds noise for very little gain.
            tracks = ["Ksiv"]
            bounds = st.multiselect(
                "Text units", _BOUND_OPTS, default=_BOUND_DEFAULT,
                key="t1_bounds",
                format_func=lambda b: BOUNDARY_LABELS.get(b, b))
        else:
            cc1, cc2 = st.columns(2)
            with cc1:
                tracks = st.multiselect(
                    "Reading tracks",
                    ["Ksiv", "Kri", "TextVariant"],
                    default=["Ksiv"],
                    key="t1_tracks",
                    format_func=lambda t: TRACK_LABELS.get(t, t))
            with cc2:
                bounds = st.multiselect(
                    "Text units", _BOUND_OPTS, default=_BOUND_DEFAULT,
                    key="t1_bounds",
                    format_func=lambda b: BOUNDARY_LABELS.get(b, b))

        # Method picker sits here, ahead of any search, so methods can be chosen
        # before a word is entered rather than only after results exist.
        # Gematria-value mode searches every method by design, so it has none.
        # Both views use the same order now — app view used to lead with the
        # classical eight while the site showed DB column order, so the two
        # disagreed about where every method sat.
        _t1_opts = CIPHER_DISPLAY_ORDER
        active_ciphers = [CIPHER_DISPLAY_ORDER[0]]
        if mode in ("Hebrew text", "Verse reference"):
            # ⚠️ Query-side nikud gate. When the committed unit is flagged
            # nikud_partial its vowel total is knowably short, so the four
            # vowel-mark methods are removed from the picker outright rather
            # than offered and then quietly wrong. This is the query-side twin
            # of nikud_partial_clause, which only ever filtered the corpus.
            _q_partial = bool((st.session_state.get("t1_committed") or {})
                              .get("nikud_partial"))
            _picker_opts = ([c for c in _t1_opts if c not in NIKUD_CIPHERS]
                            if _q_partial else _t1_opts)
            _default = [c for c in (st.session_state.get("t1_ciphers")
                                    or [_picker_opts[0]])
                        if c in _picker_opts] or [_picker_opts[0]]
            ciphers_sel = st.multiselect(
                "Show matches for method(s)", _picker_opts, default=_default,
                key=("t1_ciphers_np" if _q_partial else "t1_ciphers"),
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c))
            # Sorted, not selection-ordered: a multiselect returns options in
            # the order they were CLICKED, so the results below would appear in
            # whatever sequence the reader happened to tick them.
            active_ciphers = in_display_order(ciphers_sel or [_picker_opts[0]])

        # Perek/Sefer rows are stored under the "Aggregate" track (a DB tag,
        # not a reading tradition). Auto-include it when those boundaries are selected.
        effective_tracks = list(tracks)
        if any(b in (bounds or []) for b in ("Perek", "Sefer")) and "Aggregate" not in effective_tracks:
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
                    # Every row equals the searched value, so Value adds nothing
                    # here either — except under colel, where it varies.
                    shape_result_columns(
                        vocalize_result_text(hide_uniform_track(res_num_disp),
                                             verse_index),
                        app_view, drop_value=not colel),
                    width="stretch", hide_index=True,
                    on_select="rerun", selection_mode="single-row", key="t1_num_sel")
                sel_num = event_num.selection.rows
                if sel_num:
                    row_num = res_num.iloc[sel_num[0]]
                    with st.expander("📜 Verse detail", expanded=True):
                        render_verse_detail(
                            row_num["Book"], row_num["Chapter"], row_num["Verse"],
                            row_num["Boundary"], matched_text=row_num.get("Text"),
                            active_method=row_num["Method"],
                            # Gematria-value mode searches a typed number, not a
                            # word — t1_committed here would be leftover state
                            # from an unrelated earlier text search (or absent),
                            # and the print-out would show it as if it were the
                            # word behind *this* search.
                            query_info=None,
                            colel=colel)
        elif _committed := st.session_state.get("t1_committed"):
            _c_cons  = _committed["cons"]
            _c_raw   = _committed["raw"]
            _c_wcons = _committed["wcons"]
            payload = search_phrase(conn, _c_cons, cantillated=_c_raw,
                                    word_consonants=_c_wcons, colel=colel,
                                    tracks=effective_tracks or None, boundaries=bounds or None)
            vals = payload["values"]
            # Shown in both views now, and no longer the bare consonants: search
            # behavior can be hard to eyeball ("did it search what I actually
            # typed?"), so this echoes the query close to verbatim — nikud and
            # spacing kept, only ta'amim silently dropped (no cipher reads them,
            # so there's nothing to warn about). Previously app view dropped this
            # line entirely on the theory that the input box above already shows
            # it, but the input box doesn't scroll into view with the results.
            #
            # The old bare-consonants version (_c_cons) was structurally immune
            # to markdown injection — HE_CONSONANTS can't contain a backtick.
            # This near-verbatim version isn't: a literal backtick in the typed
            # input closes the code span early and lets the remainder render as
            # loose markdown (reproduced: "שלום `bold*text" broke the heading).
            # Backticks aren't Hebrew orthography, so swapping them for a
            # lookalike doesn't compromise "shows what was searched" for the
            # nikud/spacing this line exists for.
            _display_query = strip_taamim(_c_raw).replace("`", "ˋ")
            st.markdown(f"#### Results for `{_display_query}`")
            # _t1_opts / active_ciphers come from the method picker above, which
            # renders before a search is committed. App view orders all methods
            # with the classical (Talmud-attested) ones first. The computed-values
            # table itself now renders lower down, just above cross-method.
            _NIKUD_CIPHERS = {"HaNekudot", "ImHaNekudot", "MiluiNekudot", "ImMiluiNekudot"}
            _has_nikud = any(ch in NIKUD_VALS for ch in _c_raw)
            if any(c in _NIKUD_CIPHERS for c in active_ciphers) and not _has_nikud:
                st.warning(
                    "One or more selected methods (HaNekudot / ImHaNekudot / "
                    "MiluiNekudot / ImMiluiNekudot) count vowel marks. "
                    "Your input has no nikud, so their values will be 0 or equal to Standard. "
                    "Add nikud for accurate results.")
            for _ci, cipher in enumerate(active_ciphers):
                res = payload["results"][cipher]
                tgt = vals[cipher]
                # Verse mode only: drop the searched unit from its own results.
                # Filtered BEFORE len(res) so the heading and the table agree,
                # and before the dataframe is built so selection indices still
                # address this frame (see the stale-index note below).
                res = drop_self_match(res, _committed.get("unit"), cipher)

                # A rule between methods. Without it the blurb of one method and
                # the heading of the next read as one block, and on a phone it
                # is genuinely ambiguous which description belongs to which.
                if _ci:
                    st.markdown("<hr class='mdiv'>", unsafe_allow_html=True)

                # ⚠️ The method name ENDS in Hebrew (`Emtzaiyot Maleh — אמצעיות
                # מלא`). Appending " = 99 — 2 result(s)" to it puts digits and
                # `=`/`—` directly after an RTL run, so the bidi algorithm pulls
                # them INTO that run and reorders them: the heading rendered as
                # "Emtzaiyot Maleh — אמצעיות 2 — 99 = מלא result(s)". Keep the
                # name on its own line and the numbers on a second line, so no
                # digit is ever adjacent to Hebrew.
                import html as _h_m
                st.markdown(
                    f"<h4 class='mhead'>"
                    # quote=False: these are display strings, not attribute
                    # values. Escaping quotes would render Achbi — אכב&quot;י
                    # and every possessive as &#x27; in the source.
                    f"{_h_m.escape(CIPHER_DISPLAY_NAMES.get(cipher, cipher), quote=False)}"
                    f"</h4>", unsafe_allow_html=True)
                if CIPHER_BLURB.get(cipher):
                    # Blurb goes UNDER its own heading. It used to print above,
                    # so every description sat between two methods and appeared
                    # to belong to the one before it. Rendered as markdown rather
                    # than st.caption so it can carry a class — st.caption's own
                    # margins are what pushed it away from its heading.
                    st.markdown(
                        f"<p class='mblurb' style='font-size:.875rem;opacity:.7'>"
                        f"{_h_m.escape(CIPHER_BLURB[cipher], quote=False)}</p>",
                        unsafe_allow_html=True)
                # Order: what the method IS (heading, then blurb), then what it
                # FOUND. The value used to sit above the blurb in the same grey
                # caption style, so the method's definition and its outcome ran
                # together as one undifferentiated block. This line is the
                # result, so it is styled as a result and not as more prose.
                _res_bits = [f"<b>{tgt}</b>"]
                if colel:
                    _res_bits.append(f"Colel window {tgt-1}–{tgt+1}")
                _res_bits.append(f"{len(res)} result(s)")
                st.markdown(
                    "<div class='mval'>Value " + " · ".join(_res_bits) + "</div>",
                    unsafe_allow_html=True)
                if res.empty:
                    st.info("No structural unit in the loaded corpus matches this value.")
                else:
                    event = st.dataframe(
                        # drop_value: this table is one method whose value is in
                        # the heading above — unless colel is on, where rows
                        # legitimately differ across the ±1 window.
                        shape_result_columns(
                            vocalize_result_text(hide_uniform_track(res),
                                                 verse_index),
                            app_view, drop_value=not colel),
                        width="stretch", hide_index=True,
                        on_select="rerun", selection_mode="single-row",
                        # _c_cons suffix: same class of bug as the xm_run/span_run
                        # checkboxes above, worse in effect. Without it, selecting a
                        # row then running a NEW search leaves the OLD row index
                        # selected under this key; Streamlit reapplies that index to
                        # the NEW results DataFrame and Verse Detail silently shows
                        # whatever verse now sits at that position — not a stale
                        # warning, a wrong answer that looks like a real result.
                        # Reproduced: selecting row 5 for "שלום" (Genesis 10:21),
                        # then searching "אמת" with no further click, left Verse
                        # Detail open showing Genesis 5:2.
                        key=f"t1_sel_{cipher}_{_c_cons}")
                    sel = event.selection.rows
                    if sel:
                        row = res.iloc[sel[0]]
                        with st.expander("📜 Verse detail", expanded=True):
                            render_verse_detail(
                                row["Book"], row["Chapter"], row["Verse"], row["Boundary"],
                                matched_text=row.get("Text"), active_method=cipher,
                                query_info=st.session_state.get("t1_committed"),
                                # Verse mode commits a "ref" alongside the
                                # query text, so both sides' translation and
                                # the query-side disputed note can render.
                                query_ref=(st.session_state.get("t1_committed")
                                           or {}).get("ref"),
                                colel=colel)

            # Moved down from just under the results heading: it is reference
            # material, not the answer, so it sits with the cross-method block.
            st.markdown("**Computed values across all methods**")
            # Without nikud the vowel-mark methods have nothing to count:
            # HaNekudot/MiluiNekudot come out 0, and ImHaNekudot/ImMiluiNekudot
            # come out exactly Standard — which reads like a real result and is
            # the more misleading of the two. Show "—" so the box never implies
            # a vowel-mark value was measured.
            _vals_show = {
                k: ("—" if (k in NIKUD_CIPHERS and not _has_nikud) else vals[k])
                for k in _t1_opts if k in vals
            }
            st.dataframe(pd.DataFrame([_vals_show]),
                         width="stretch", hide_index=True)
            if not _has_nikud:
                st.caption("— = needs nikud. Add vowel points to your input to get "
                           "the four vowel-mark methods.")

            # "Coincidences" implied the matches are accidental, which is the
            # opposite of why someone opens this box.
            with st.expander("🔀 Cross-method matches", expanded=False):
                # Streamlit executes an expander body even while it is collapsed, so
                # this block used to run on every search and every widget interaction.
                # Opt-in keeps a plain search cheap; once run, the result is cached.
                # Key includes the searched word: a bare "xm_run" key persists its
                # checked state across an unrelated NEW search too (Streamlit keeps
                # widget state by key, not by what's on screen), silently re-running
                # this scan against a different word than the one it was turned on
                # for. Keying on _c_cons gives each distinct search its own toggle,
                # defaulting to off, the way a fresh search should.
                if not st.checkbox("Compute cross-method matrix", key=f"xm_run_{_c_cons}",
                                   help=f"Scans the corpus under all {N_CIPHERS} methods to build a {N_CIPHERS}x{N_CIPHERS} count matrix. Takes a few seconds, so it is off by default and a plain search does not pay for it."):
                    st.caption("Off by default — this scan takes a few seconds. "
                               "Tick to run it; the result is then cached.")
                else:
                    st.caption(
                        "Rows = your word under **Method A** (value shown); columns = "
                        "corpus **Method B** searched. Cell = match count. **Colour = "
                        "lift**: the cell's matches against what that method typically "
                        "produces per value, so columns stay comparable even though "
                        "methods differ hugely in spread (KatanMispari yields 9 "
                        "distinct values here, ImMiluiNekudot 3,467). Warmer = rarer "
                        "than typical for its own method; cool = as common or commoner. "
                        "Colel, track, and unit filters are shared with the search above."
                    )
                    if not _has_nikud:
                        st.caption(
                            "Your input has no nikud, so the four vowel-mark **rows** "
                            "search a value of 0 and mean nothing. The vowel-mark "
                            "**columns** are still valid — they ask whether any corpus "
                            "unit's vowel-mark total equals one of your word's other "
                            "values, which does not require nikud on your input. Note "
                            "that every HaNekudot total is even (dot=10, line=6), so an "
                            "odd value can never match that column."
                        )
                    a_vals = dict(vals)
                    pop = cached_boundary_population(
                        conn, corpus_key, tuple(effective_tracks or ()),
                        tuple(bounds or ())) or 1
                    # "rate" is the share of the searched population a cell
                    # accounts for: matches / units currently in scope. The old
                    # label said "5%" without ever saying 5% of what.
                    # Filter by absolute match count, not by share of the
                    # population. This panel exists to find things you can then
                    # read: 30,000 matches is unusable however statistically
                    # interesting it is, and 20 can be looked through. A
                    # share-of-population rule also measured the wrong thing —
                    # it called שלום's Milui value "notable" when that value is
                    # 44x *more* common than typical for Milui.
                    fc1, fc2 = st.columns([3, 2])
                    with fc1:
                        xm_limit = st.slider(
                            "Hide cells with more than this many matches",
                            5, 500, 25, step=5, key="xm_limit",
                            help=_tip("A browsability cutoff: cells you could "
                                      "actually sit and read. Independent of "
                                      "corpus size and of how many distinct "
                                      "values a method happens to produce."))
                    with fc2:
                        xm_nolimit = st.checkbox("No limit", key="xm_nolimit",
                                                 help=_tip("Show every cell."))
                    with st.spinner("Building cross-method matrix…"):
                        xm_df = cached_xm_matrix(
                            conn, corpus_key, tuple(sorted(a_vals.items())), colel,
                            tuple(effective_tracks or ()), tuple(bounds or ())
                        )
                    # Colour by lift, not by share of population. expected =
                    # population / distinct values for that method, so each
                    # column is judged against its own spread; lift < 1 means
                    # genuinely rarer than typical, lift > 1 commoner.
                    spread = cached_method_spread(
                        conn, corpus_key, tuple(effective_tracks or ()),
                        tuple(bounds or ()))
                    expected = {m: max(pop / spread.get(m, 1), 1e-9)
                                for m in xm_df.columns}
                    lift_mat = xm_df.astype("float").copy()
                    for m in lift_mat.columns:
                        lift_mat[m] = lift_mat[m] / expected[m]
                    if not xm_nolimit:
                        xm_df = xm_df.where(xm_df <= xm_limit, other=0)
                    # gmap is computed before any blanking so it never sees NaN.
                    xm_show = xm_df.astype("float")
                    # With no nikud on the input the four vowel-mark rows are
                    # meaningless: HaNekudot/MiluiNekudot search 0, and the Im*
                    # pair search Standard + 0, which looks like a real result
                    # but carries no vowel information. Blank them to "—" rather
                    # than showing counts nobody should read. Columns stay live.
                    dead_rows = ([r for r in xm_show.index
                                  if r.split(" (")[0] in NIKUD_CIPHERS]
                                 if not _has_nikud else [])
                    if dead_rows:
                        xm_show.loc[dead_rows, :] = float("nan")

                    def _grey_dead(row):
                        return (["color:#9ca3af;background-color:#f3f4f6"] * len(row)
                                if row.name in dead_rows else [""] * len(row))

                    st.dataframe(
                        xm_show.style.background_gradient(
                            cmap="YlOrRd_r", axis=None,
                            gmap=lift_mat.to_numpy(),
                        ).apply(_grey_dead, axis=1).format(precision=0, na_rep="—"),
                        width="stretch",
                    )
                    st.markdown("**Drill into a pair/s**")
                    st.caption(
                        "The two sides use different methods: A scores **your "
                        "search term**, B scores the **corpus units** looked up "
                        "with that value.")
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        drill_a = st.selectbox(
                            "Method A — your search term", CIPHER_DISPLAY_ORDER,
                            key="xm_drill_a",
                            format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                            help=_tip("Which method scores the word you "
                                      "searched. Its value is what gets looked "
                                      "up in the corpus.")
                        )
                    with dc2:
                        drill_b_list = st.multiselect(
                            "Method B — corpus results (one or more)",
                            CIPHER_DISPLAY_ORDER,
                            default=[CIPHER_DISPLAY_ORDER[0]], key="xm_drill_b",
                            format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                            help=_tip("Which method the corpus units are scored "
                                      "under when matching that value.")
                        )
                    drill_val = a_vals[drill_a]
                    for drill_b in in_display_order(drill_b_list or [CIPHER_DISPLAY_ORDER[0]]):
                        st.markdown(
                            f"**{drill_a}({_c_raw.strip()}) = {drill_val}** "
                            f"→ corpus units with **{drill_b} = {drill_val}**"
                            + (" ± 1" if colel else "")
                        )
                        drill_res = search_value(
                            conn, drill_b, drill_val, colel, effective_tracks or None, bounds or None
                        )
                        # Same-method drill (A == B) on a verse-mode query is a
                        # plain same-method search, so the searched unit comes
                        # back as a trivial hit. When A != B it is a real
                        # finding — the unit's B value equalling its own A
                        # value — so drop_self_match keeps it.
                        drill_res = drop_self_match(
                            drill_res,
                            (st.session_state.get("t1_committed") or {}).get("unit"),
                            drill_b if drill_a == drill_b else None)
                        if drill_res.empty:
                            st.info(f"No corpus unit matches {drill_a}/{drill_b} at the current filters.")
                        else:
                            ev_drill = st.dataframe(
                                # Shaped like every other result table: combines
                                # Book/Chapter/Verse into one reference and drops
                                # SubID in app view, and hides a Track column
                                # that is the same on every row. Row order and
                                # count are untouched, so the selection index
                                # below still addresses drill_res.
                                shape_result_columns(
                                    vocalize_result_text(
                                        drop_uniform_track(drill_res),
                                        verse_index),
                                    app_view),
                                width="stretch", hide_index=True,
                                on_select="rerun", selection_mode="single-row",
                                # _c_cons suffix: same reason as t1_sel_* above —
                                # a stale row index would otherwise apply to a
                                # different search's drill-down results.
                                key=f"xm_drill_sel_{drill_b}_{_c_cons}",
                            )
                            if ev_drill.selection.rows:
                                rd = drill_res.iloc[ev_drill.selection.rows[0]]
                                with st.expander("📜 Verse detail", expanded=True):
                                    render_verse_detail(
                                        rd["Book"], rd["Chapter"], rd["Verse"],
                                        rd["Boundary"], matched_text=rd.get("Text"),
                                        active_method=drill_b,
                                        # ⚠️ A cross-method drill-down has TWO
                                        # methods: drill_a scores the search
                                        # term, drill_b scores the corpus unit.
                                        # Without query_method the export scored
                                        # the query with drill_b too, printing a
                                        # query total that matched nothing — e.g.
                                        # Atbash(name)=2344 above an Atbash
                                        # verse total of 1036, presented as an
                                        # equality. The real claim was
                                        # Standard(name)=1036 == Atbash(verse).
                                        query_method=drill_a,
                                        # Same searched word as the main results
                                        # list — a cross-method drill-down is
                                        # still "this word equals that text",
                                        # so the print-out needs both halves.
                                        query_info=st.session_state.get("t1_committed"),
                                # Verse mode commits a "ref" alongside the
                                # query text, so both sides' translation and
                                # the query-side disputed note can render.
                                query_ref=(st.session_state.get("t1_committed")
                                           or {}).get("ref"),
                                        colel=colel,
                                    )

            with st.expander("🔍 All word-span matches", expanded=False):
                # Streamlit executes an expander body even while it is collapsed, so
                # this block used to run on every search and every widget interaction.
                # Opt-in keeps a plain search cheap; once run, the result is cached.
                # Keyed on the searched word for the same reason as the
                # cross-method checkbox above: a bare key would carry a checked
                # state over onto an unrelated new search and silently scan it too.
                if not st.checkbox("Run word-span scan", key=f"span_run_{_c_cons}",
                                   help="Scans every contiguous 2-N word sequence in the corpus. Takes a few seconds, so it is off by default and a plain search does not pay for it."):
                    st.caption("Off by default — this scan takes a few seconds. "
                               "Tick to run it; the result is then cached.")
                else:
                    st.caption(
                        "Scans every contiguous sequence of 2–N words in the corpus "
                        "for matches to the same gematria value. Finds patterns that "
                        "cross structural boundaries (e.g., last word of one phrase + "
                        "first words of the next)."
                    )
                    sc1, sc2 = st.columns([2, 1])
                    with sc1:
                        _span_default = active_ciphers[0] if active_ciphers else CIPHER_DISPLAY_ORDER[0]
                        span_cipher = st.selectbox(
                            "Cipher",
                            CIPHER_DISPLAY_ORDER,
                            index=(CIPHER_DISPLAY_ORDER.index(_span_default)
                                   if _span_default in CIPHER_DISPLAY_ORDER else 0),
                            format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                            key="span_cipher",
                        )
                    with sc2:
                        span_max = st.slider("Max words in span", 2, 15, 7, key="span_max")
                    # Off by default: a span that crosses a sof-pasuq crosses a
                    # real division in the text, so including those is the
                    # reader's decision, not a silent default. Keyed on the
                    # searched word for the same reason as the scan checkbox
                    # above — a bare key would carry the setting onto an
                    # unrelated later search.
                    span_cross = st.checkbox(
                        "Include spans that cross verse boundaries",
                        key=f"span_cross_{_c_cons}",
                        help=_tip("Off: every span stays inside one verse. On: also "
                                  "finds spans running from the end of one verse into "
                                  "the next, across the sof pasuq (׃). Those rows are "
                                  "marked 'crosses verses'. Never crosses between books."))
                    span_tgt = vals[span_cipher]
                    st.markdown(
                        f"Searching **{span_cipher} = {span_tgt}**"
                        + (f" (colel ±1: {span_tgt-1}–{span_tgt+1})" if colel else "")
                    )
                    # Inline spinner, same reason as the matrix above.
                    with st.spinner("Scanning word spans…"):
                        span_df = cached_span_search(
                            conn, corpus_key, span_tgt, span_cipher,
                            span_max, colel, tuple(effective_tracks or ()),
                            span_cross,
                        )
                    if span_df.empty:
                        st.info("No multi-word span matches this value with the current settings.")
                    else:
                        _n_cross = (int(span_df["_cross"].sum())
                                    if "_cross" in span_df.columns else 0)
                        st.markdown(
                            f"**{len(span_df)} span match(es)**"
                            + (f" — {_n_cross} of them cross a verse boundary"
                               if _n_cross else ""))
                        # Internal offset columns stay out of the table; row order is
                        # unchanged, so selection indices still address span_df.
                        # _cross is promoted to a visible column first, so a
                        # crossing row is identifiable in the table itself and
                        # not only after opening its detail panel.
                        _span_vis = span_df.copy()
                        if span_cross and _n_cross:
                            # zip over the raw columns rather than itertuples():
                            # pandas renames leading-underscore fields to _2/_3
                            # positionally, so r._cross would not resolve.
                            _span_vis.insert(
                                4, "Spans",
                                [f"→ {int(ec)}:{int(ev)}" if cx else "within verse"
                                 for cx, ec, ev in zip(span_df["_cross"],
                                                       span_df["_end_ch"],
                                                       span_df["_end_vs"])])
                        span_show = shape_result_columns(
                            vocalize_result_text(
                                hide_uniform_track(
                                    _span_vis[[c for c in _span_vis.columns
                                               if not c.startswith("_")]]),
                                verse_index),
                            app_view)
                        span_event = st.dataframe(
                            span_show, width="stretch", hide_index=True,
                            # _c_cons suffix: same reason as t1_sel_* above.
                            on_select="rerun", selection_mode="single-row",
                            key=f"span_sel_{_c_cons}")
                        span_sel = span_event.selection.rows
                        if span_sel:
                            sr = span_df.iloc[span_sel[0]]
                            with st.expander("📜 Verse detail", expanded=True):
                                render_verse_detail(
                                    sr["Book"], int(sr["Ch"]), int(sr["Vs"]),
                                    "WordSpan", active_method=span_cipher,
                                    span_range=(int(sr["_w0"]), int(sr["_w1"])),
                                    track=sr["Track"], colel=colel,
                                    # The span scan searches the value of the
                                    # word the reader typed, so the print-out
                                    # owes them that word's own calculation
                                    # alongside the span's — without this the
                                    # export showed only half of "these two
                                    # are equal".
                                    query_info=st.session_state.get("t1_committed"),
                                # Verse mode commits a "ref" alongside the
                                # query text, so both sides' translation and
                                # the query-side disputed note can render.
                                query_ref=(st.session_state.get("t1_committed")
                                           or {}).get("ref"),
                                    end_ref=((int(sr["_end_ch"]), int(sr["_end_vs"]))
                                             if sr.get("_cross") else None))
        else:
            st.warning("Enter a Hebrew phrase to search.")

    # ===================== TAB 2: STRUCTURAL EXPLORER =====================
    # Guarded: tab2 does not exist in app view.
    if tab2 is not None:
      with tab2:
        st.subheader("Scriptural Structural Explorer")
        t2_ciphers = CIPHER_DISPLAY_ORDER
        # "BothHalves" is a tab-2-only pseudo-boundary, not a stored boundary_type.
        # Browsing here is about comparing units, and forcing a choice between
        # first and second half made the two searchable only in isolation — a
        # half-verse's counterpart was always one radio click away. The combined
        # option lists both, with a Half column saying which is which.
        kind = st.radio(
            "Browse by",
            ["Perek", "Sefer", "Petucha", "Setuma", "Verse",
             "BothHalves", "TiphchaPhrase", "ZakefPhrase"],
            horizontal=True,
            format_func=lambda b: T2_BOUNDARY_LABELS.get(
                b, BOUNDARY_LABELS.get(b, b)))
        if kind == "BothHalves":
            df = structure_frame(conn, "FirstHalf", "SecondHalf")
        else:
            df = structure_frame(conn, kind)
        if df.empty:
            st.info(f"No {T2_BOUNDARY_LABELS.get(kind, BOUNDARY_LABELS.get(kind, kind))} units in the loaded corpus yet.")
        else:
            # boundary_type is carried only for the combined half-verse view,
            # where it is the one thing distinguishing two rows of the same
            # verse; for every other `kind` it is constant and would be noise.
            display_cols = (["book", "chapter", "verse", "boundary_type",
                             "variant_track"] + t2_ciphers
                            if kind == "BothHalves" else
                            ["book", "chapter", "verse", "variant_track"] + t2_ciphers)
            show = df[[c for c in display_cols if c in df.columns]].rename(
                columns={"book": "Book", "chapter": "Chapter", "verse": "Verse",
                         "boundary_type": "Half", "variant_track": "Track"})
            if "Half" in show.columns:
                show["Half"] = show["Half"].map(lambda h: HALF_LABELS.get(h, h))
            # Drop before labelling: the check reads raw track names, and a
            # uniform-Ksiv listing should not advertise a variant column.
            show = hide_uniform_track(show)
            if "Track" in show.columns:
                show["Track"] = show["Track"].map(lambda t: TRACK_LABELS.get(t, t))
            # The filter used to be a book-name substring only, so reaching a
            # specific verse meant typing the book and then scrolling a
            # 23,206-row canvas table — there was no chapter or verse filter at
            # all. It now runs through parse_verse_ref first, so a full
            # reference jumps straight to the verse while a bare book name
            # still behaves exactly as before. Same parser as Tab 1's verse
            # mode: one implementation, two call sites.
            q = st.text_input(
                "Filter (reference or book)", "",
                placeholder="e.g. Genesis 1:1, בראשית א:א, or just Genesis",
                help=_tip("A full reference jumps to that verse. A book name "
                          "on its own filters to that book."))
            if q:
                _ref = parse_verse_ref(q)
                if _ref:
                    _rb, _rc, _rv = _ref
                    mask = ((show["Book"] == _rb)
                            & (show["Chapter"] == _rc)
                            & (show["Verse"] == _rv))
                    if not mask.any():
                        st.info(f"{_rb} {_rc}:{_rv} has no "
                                f"{T2_BOUNDARY_LABELS.get(kind, BOUNDARY_LABELS.get(kind, kind))} "
                                "unit. Showing the whole book instead.")
                        mask = show["Book"] == _rb
                    show = show[mask]
                else:
                    show = show[show["Book"].str.contains(q, case=False, na=False)]
            st.caption("Click any gematria value cell to find every unit in the corpus "
                       f"that shares that number, across the {N_CIPHERS} methods.")
            t2_col_config = {
                "Book":    st.column_config.TextColumn("Book", width="medium"),
                "Chapter": st.column_config.NumberColumn("Chapter", width="small"),
                "Verse":   st.column_config.NumberColumn("Verse", width="small"),
                "Track":   st.column_config.TextColumn("Track", width="small"),
                "Half":    st.column_config.TextColumn("Half", width="small"),
            }
            for _c in t2_ciphers:
                t2_col_config[_c] = st.column_config.NumberColumn(_c, width="small")
            event2 = st.dataframe(
                # Shaped at display time, not before: the filter above still
                # reads Parsha (which holds the book name, so it filters by book).
                shape_result_columns(vocalize_result_text(show, verse_index)),
                width="stretch", hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config=t2_col_config,
                height=400,
                key="t2_sel")
            st.caption(f"{len(show)} {T2_BOUNDARY_LABELS.get(kind, BOUNDARY_LABELS.get(kind, kind))} unit(s). "
                       "Every method column is an indexed gematria total for that block.")

            cipher_pick = st.selectbox(
                "Look up matches for which method's value?",
                t2_ciphers, index=0, key="t2_cipher_pick",
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                help="Pick a gematria method, then select a row above. The bottom "
                     "panel lists every corpus unit sharing that row's value under "
                     f"any of the {N_CIPHERS} methods.")

            sel_rows = event2.selection.rows
            _sel_partial = False
            if sel_rows:
                row2 = show.iloc[sel_rows[0]]

                # Does the selected unit itself contain an unpointed Ksiv word?
                # If so its four vowel-mark totals are short by that word's
                # contribution, and must neither be displayed nor used as a
                # search target — a knowably incomplete number would otherwise
                # go looking for units that "share" it. Read from `df` (the
                # source frame) because `show` drops the flag column.
                _sel_partial = bool(
                    df.loc[show.index[sel_rows[0]]].get("nikud_partial", 0))

                # Show this row's values across every method. A unit carrying
                # an unpointed Ksiv word shows "—" for the four vowel-mark
                # methods rather than dropping the columns: same convention Tab
                # 1 uses for a query typed without nikud, so the table keeps its
                # shape and the reader can see WHICH methods are unavailable.
                summary = {
                    c: ("—" if (_sel_partial and c in NIKUD_CIPHERS)
                        else int(row2[c]))
                    for c in t2_ciphers if c in row2.index
                }
                st.markdown(f"**Selected unit — values across the {N_CIPHERS} methods:**"
                            if t2_ciphers is CIPHER_DISPLAY_ORDER else
                            "**Selected unit — classical method values:**")
                st.dataframe(pd.DataFrame([summary]),
                             width="stretch", hide_index=True)
                if _sel_partial:
                    st.caption(f"— = {KSIV_UNPOINTED_NOTE}")

            # Refuse the search outright rather than running it on a value that
            # is known to be wrong. Guarded with a flag rather than st.stop(),
            # which would also kill Tabs 3 and 4 further down the script.
            _t2_blocked = bool(sel_rows) and _sel_partial and cipher_pick in NIKUD_CIPHERS
            if _t2_blocked:
                st.warning(
                    f"**{cipher_pick}** is unavailable here: this unit "
                    "contains a Ksiv word printed without nikud, so its total "
                    "is incomplete. The other methods work.")

            if sel_rows and not _t2_blocked:
                cell_val = int(row2[cipher_pick])
                st.markdown(
                    f"**{cipher_pick} = {cell_val}** — every unit in the corpus "
                    f"that shares this value (up to 50 per method):")
                # BothHalves is not a real boundary_type, so the detail panel
                # (which highlights the matched span and derives word-aware
                # consonants per boundary) has to be told which half this row
                # actually is. Recovered from the row's own Half label rather
                # than from `kind`, which no longer identifies one boundary.
                if kind == "BothHalves":
                    _row_boundary = next(
                        (b for b, lbl in HALF_LABELS.items()
                         if lbl == row2.get("Half")), "FirstHalf")
                else:
                    _row_boundary = kind
                match_df = search_value_all_methods(conn, cell_val)
                # The selected unit always equals its own value, so it came
                # back among its own matches. This frame carries a Method
                # column, so only the row under the SAME method as the clicked
                # cell is trivial — the same unit appearing under a different
                # method is a real cross-method finding and stays.
                match_df = drop_self_match(
                    match_df,
                    (row2["Book"], int(row2["Chapter"]), int(row2["Verse"]),
                     _row_boundary),
                    cipher_pick)
                if match_df.empty:
                    st.info("No corpus unit has this exact value under any method.")
                    if _row_boundary in DETAIL_BOUNDARIES:
                        with st.expander("📜 Verse detail", expanded=True):
                            # No query_info: nothing in the corpus shares this
                            # value, so the selected unit is the only text on
                            # screen — there is no second half of an equality to
                            # print. active_method is still passed, or the panel
                            # would show no breakdown at all for the very method
                            # the reader picked.
                            render_verse_detail(
                                row2["Book"], row2["Chapter"], row2["Verse"],
                                _row_boundary, active_method=cipher_pick)
                else:
                    ev_match = st.dataframe(
                        # Left as an explicit column list: Tab 2 is site-only,
                        # so shape_result_columns would change nothing except to
                        # re-introduce the SubID column this list deliberately
                        # omits. Track is excluded for the same reason it is
                        # dropped elsewhere — every row here is Ksiv.
                        match_df[["Method", "Book", "Chapter", "Verse",
                                  "Boundary", "Text", "Value"]],
                        width="stretch", hide_index=True,
                        on_select="rerun", selection_mode="single-row",
                        key="t2_match_sel")
                    st.caption(f"{len(match_df)} match(es) across "
                               f"{match_df['Method'].nunique()} method(s).")
                    if ev_match.selection.rows:
                        rm = match_df.iloc[ev_match.selection.rows[0]]
                        # A Tab 2 print-out used to carry only the *matched*
                        # unit's calculation, because query_info was never
                        # passed here — so the export of "these two units share
                        # a value" showed the working for one of them and left
                        # the selected unit (the reason the reader is looking at
                        # this row at all) unaccounted for. The selected unit
                        # plays the same role Tab 1's search word does, so it is
                        # threaded through as query_info and appears under
                        # "Your Word" alongside the match.
                        # `show` is the display frame — the consonants column is
                        # filtered out of it — so the selected unit's text is
                        # read from `df` at the same positional index, which is
                        # safe only because `show` is built from `df` by column
                        # selection and row filtering that preserves the index.
                        _sel_src = df.loc[show.index[sel_rows[0]]]
                        _sel_cons = str(_sel_src.get("consonants", "") or "")
                        _sel_v = verse_index.get(
                            (row2["Book"], int(row2["Chapter"]),
                             int(row2["Verse"])))
                        _sel_raw = ""
                        if _sel_v is not None and _sel_cons:
                            # Recover the pointed text so vowel-mark methods in
                            # the export are computed with nikud, matching how
                            # the panel scores the matched unit.
                            _sel_raw = locate_vocalized(_sel_v.text, _sel_cons)
                        # wcons must keep word boundaries or the word-aware
                        # ciphers (Kaful/Mityashev/Boneeh/HaAchor) score the
                        # unit as one long token. Derived per boundary the same
                        # way render_verse_detail derives its own w_cons.
                        _sel_b = str(_sel_src.get("boundary_type", ""))
                        if _sel_v is not None and _sel_b == "FirstHalf":
                            _sel_w = split_halves_word_cons(_sel_v.text)[0]
                        elif _sel_v is not None and _sel_b == "SecondHalf":
                            _sel_w = split_halves_word_cons(_sel_v.text)[1]
                        elif _sel_v is not None and _sel_b == "Verse":
                            _sel_w = " ".join(tokenize_words(_sel_v.text))
                        else:
                            # Word / phrase units: recover spacing from the
                            # located pointed text when possible, else treat as
                            # a single token (correct for Word, the common case).
                            _sel_w = (" ".join(tokenize_words(_sel_raw))
                                      if _sel_raw else _sel_cons)
                        # Tab 2 has no typed search word, so the export's
                        # default "Your Word" heading would misdescribe what
                        # this section shows; `label` renames it to the unit the
                        # reader actually selected.
                        _sel_ref = (f"{row2['Book']} {row2['Chapter']}:"
                                    f"{row2['Verse']}")
                        _t2_query = ({"raw": _sel_raw or _sel_cons,
                                      "cons": _sel_cons,
                                      "wcons": _sel_w or _sel_cons,
                                      "label": f"Selected Unit ({_sel_ref})"}
                                     if _sel_cons else None)
                        with st.expander("📜 Verse detail", expanded=True):
                            render_verse_detail(
                                rm["Book"], rm["Chapter"], rm["Verse"],
                                rm["Boundary"], matched_text=rm.get("Text"),
                                active_method=str(rm.get("Method", "")),
                                query_info=_t2_query,
                                # The selected unit IS a verse reference, so the
                                # panel and export can now show both sides'
                                # translation. Previously only the match's
                                # English travelled, so an export of "these two
                                # units share a value" carried one side of it.
                                query_ref=(row2["Book"], int(row2["Chapter"]),
                                           int(row2["Verse"])))

    # ===================== TAB 3: ECHOES & ANOMALIES =====================
    # Guarded for app view (tab3 is None there). The two-space `with` keeps
    # the original body indentation valid without re-indenting the block.
    if tab3 is not None:
      with tab3:
        st.subheader("Textual Echoes & Anomalies")

        # --- Filter controls ---
        col_m1, col_m2, col_opts = st.columns([3, 3, 2])
        with col_m1:
            t3_ma = st.multiselect(
                "Method A", CIPHER_DISPLAY_ORDER, default=["Standard"], key="t3_ma",
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                help="Gematria method for the first element of each pattern")
        with col_m2:
            t3_mb = st.multiselect(
                "Method B", CIPHER_DISPLAY_ORDER, default=["Standard"], key="t3_mb",
                format_func=lambda c: CIPHER_DISPLAY_NAMES.get(c, c),
                help="Method for the second element. When Cross-method is off, Method A is used for both.")
        with col_opts:
            t3_cross = st.toggle(
                "Cross-method", False, key="t3_cross",
                help="When on, all A×B method combinations are tested. "
                     "When off, only same-method (A=B) patterns.")
            t3_colel = st.toggle("כולל (±1)", False, key="t3_colel")

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
        eff_a = in_display_order(t3_ma or CIPHER_DISPLAY_ORDER)
        eff_b = in_display_order((t3_mb if t3_cross else eff_a) or CIPHER_DISPLAY_ORDER)

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
                unified[display_cols], width="stretch", hide_index=True,
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
                    # Every pattern here is a claim that two units share a value,
                    # so each unit's print-out owes the reader the *other* one's
                    # calculation too — otherwise the export shows one side of an
                    # equality and silently drops the half that makes it a
                    # pattern at all. The counterpart plays the role Tab 1's
                    # search word plays, so it is threaded through as query_info
                    # (labelled, since the reader typed nothing).
                    def _t3_counterpart(other_ref, other_meth, other_label):
                        p = parse_pattern_ref(other_ref)
                        if not p:
                            return None
                        ob, oc, ov, obound = p
                        orow = raw_conn(conn).execute(
                            "SELECT consonants FROM units WHERE book=? AND chapter=? "
                            "AND verse=? AND boundary_type=? AND variant_track='Ksiv'",
                            (ob, int(oc), int(ov), obound)).fetchone()
                        if not orow or not orow[0]:
                            return None
                        ocons = str(orow[0])
                        ov_obj = verse_index.get((ob, int(oc), int(ov)))
                        oraw = (locate_vocalized(ov_obj.text, ocons)
                                if ov_obj is not None else "")
                        # Word-boundary-aware consonants, derived per boundary the
                        # same way render_verse_detail derives its own — without
                        # this the word-aware ciphers score the unit as one token.
                        if ov_obj is not None and obound == "FirstHalf":
                            owc = split_halves_word_cons(ov_obj.text)[0]
                        elif ov_obj is not None and obound == "SecondHalf":
                            owc = split_halves_word_cons(ov_obj.text)[1]
                        elif ov_obj is not None and obound == "Verse":
                            owc = " ".join(tokenize_words(ov_obj.text))
                        else:
                            owc = (" ".join(tokenize_words(oraw)) if oraw else ocons)
                        return {"raw": oraw or ocons, "cons": ocons,
                                "wcons": owc or ocons,
                                "label": f"{other_label} ({ob} {oc}:{ov})"}

                    for idx, (label, ref_str, meth) in enumerate(pairs):
                        parsed = parse_pattern_ref(ref_str)
                        if parsed:
                            book, chap, vs, boundary = parsed
                            st.markdown(f"**{label}**")
                            _other_label, _other_ref, _other_meth = pairs[1 - idx]
                            render_verse_detail(
                                book, chap, vs, boundary,
                                active_method=meth,
                                query_info=_t3_counterpart(
                                    _other_ref, _other_meth, _other_label),
                                # This tab's own colel toggle — `colel` is Tab
                                # 1's widget and is not bound when Tab 3 renders
                                # without it (app view sets tab1 = None).
                                colel=t3_colel)

    # ===================== TAB 4: STATISTICS DASHBOARD ===================
    # Guarded for app view (tab4 is None there); see tab3 note above.
    if tab4 is not None:
      with tab4:
        st.subheader("Macro Statistical Dashboard")

        st.markdown("#### Highs & lows by structure — Standard method")
        ext = extremes_table(conn, ["Verse", "Perek", "Sefer",
                                    "Petucha", "Setuma", "Word"])
        if not ext.empty:
            st.dataframe(ext, width="stretch", hide_index=True)
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
                st.plotly_chart(fig_h, width="stretch",
                                config={"scrollZoom": False})
            st.caption(f"{len(plot_df)} verse(s), each counted once (כְּתִיב Ksiv track). "
                       "Hover for exact counts; click legend to toggle; drag to zoom.")

            # ---- Method correlation heatmap (interactive) ----
            st.markdown("#### How the methods relate to each other")
            st.caption("Pearson correlation across all verse totals. "
                       "Methods with high correlation produce similar rankings; "
                       "low/negative correlation highlights structurally distinct searches. "
                       "Hover any cell to see the exact value.")
            numeric_cols = [c for c in CIPHER_DISPLAY_ORDER
                           if c in plot_df.columns and c not in _HEATMAP_EXCLUDE]
            corr = plot_df[numeric_cols].corr().round(2)
            fig_corr = px.imshow(
                corr, text_auto=True, color_continuous_scale="RdBu_r",
                zmin=-1, zmax=1, aspect="auto",
                title="Method correlation (verse totals)")
            fig_corr.update_layout(height=480,
                                   margin=dict(t=50, b=30, l=120, r=20))
            st.plotly_chart(fig_corr, width="stretch",
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
                st.plotly_chart(fig_bk, width="stretch",
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
                st.dataframe(gap_df, width="stretch", hide_index=True)
                st.caption("Integer ranges with no verse in the loaded corpus. "
                           "Values near wide gaps are statistically rarer.")

        st.divider()
        with st.expander("Cross-method half-verse balance — corpus overview",
                         expanded=False):
            import plotly.express as _px_xm

            _BALANCE_COLS = [c for c in CIPHER_DISPLAY_ORDER if c not in _HEATMAP_EXCLUDE]

            @st.cache_data(show_spinner="Computing cross-method balance matrix…")
            def _xm_balance_matrix(_conn, corpus_key):
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
                row = pd.read_sql_query(sql, raw_conn(_conn)).iloc[0]
                total = int(row["total_verses"]) or 1
                data = [[int(row[f"{mx}_vs_{my}"]) / total for my in _BALANCE_COLS]
                        for mx in _BALANCE_COLS]
                return pd.DataFrame(data, index=_BALANCE_COLS, columns=_BALANCE_COLS), total

            rate_df, total_verses = _xm_balance_matrix(conn, corpus_key)
            fig_xm = _px_xm.imshow(
                rate_df, text_auto=".1%",
                color_continuous_scale="YlOrRd", aspect="auto",
                title="Cross-method half-verse balance rate (Colel ±1)",
                labels=dict(x="Second half — method",
                            y="First half — method", color="Rate"),
            )
            fig_xm.update_layout(height=560, margin=dict(t=50, b=30, l=120, r=20))
            st.plotly_chart(fig_xm, width="stretch",
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
