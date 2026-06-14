# -*- coding: utf-8 -*-
"""
Tanach Gematria Search Engine, Structural Pattern Database & Statistical Visualizer
===================================================================================

A self-contained Streamlit application implementing:

  * A complete, correct multi-cipher gematria engine (11 ciphers).
  * Real consonant-cleaning (strips nikud + ta'amim, keeps the 22 base letters).
  * Atnach-based half-verse splitting and Petucha/Setuma paragraph parsing.
  * A Ksiv/Kri + Masoretic textual-variant forking engine (Itture Sopherim,
    Esther doublets — see TEXTUAL_VARIANT_SPECS).
  * An in-memory, fully indexed SQLite store.
  * Proximity / internal-balance / macro-micro pattern recognition.
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
    """Mispar Kidmi / Meshulash - triangular cumulative value per letter."""
    return sum(KIDMI.get(_normalize_final(c), 0) for c in s)


def _temurah_value(s: str, mapping: Dict[str, str]) -> int:
    """Substitute each letter via a temurah map, then take the Absolute value."""
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


# Ordered registry of every cipher. The order here is the column order used
# throughout the database and the UI.
CIPHERS: Dict[str, Callable[[str], int]] = {
    "Absolute": g_absolute,     # Mispar Hechrachi / Yaschar      (required)
    "Katan": g_katan,           # Mispar Katan (reduced)          (required)
    "Gadol": g_gadol,           # Mispar Gadol (final 500-900)    (required)
    "Atbash": g_atbash,         # א"ת ב"ש                          (required)
    "Albam": g_albam,           # א"ל ב"ם                          (required)
    "Atbah": g_atbah,           # א"ט ב"ח                          (required)
    "Avgad": g_avgad,           # א"ב ג"ד                          (required)
    "Siduri": g_siduri,         # Mispar Siduri (ordinal)         (researched)
    "Ribua": g_ribua,           # Mispar Meruba Prati (squared)   (researched)
    "Kidmi": g_kidmi,           # Mispar Kidmi / Meshulash        (researched)
    "Achbi": g_achbi,           # א"כ ב"י temurah variant         (researched)
}
CIPHER_NAMES: List[str] = list(CIPHERS.keys())


def compute_all_ciphers(consonants: str) -> Dict[str, int]:
    """Return {cipher_name: value} for a cleaned consonant string."""
    return {name: fn(consonants) for name, fn in CIPHERS.items()}


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
    """Split a (vocalised/cantillated) verse into first/second half by Atnach.

    First half  = start .. through the word bearing the Atnach mark.
    Second half = remainder up to Sof Pasuq.
    If no Atnach is present, the whole verse is treated as the first half.
    Returns (first_half_consonants, second_half_consonants).
    """
    idx = text.find(ATNACH)
    if idx == -1:
        return strip_to_consonants(text), ""
    # Extend the split to the end of the atnach-bearing word so we don't sever
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
            ))

    # --- Doublet: Esther 8:11 / 9:27 alternative reading ---
    if v.doublet_from and v.doublet_to:
        base_cons = forks[0].full_consonants
        if v.doublet_from in base_cons:
            doub_cons = base_cons.replace(v.doublet_from, v.doublet_to, 1)
            # Re-derive halves from the substituted source text so that
            # first_half + second_half == doub_cons even when the substitution
            # word straddles the Atnach boundary.
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
            ))
    return forks


# ---------------------------------------------------------------------------
# SECTION 4.  VERIFIED OFFLINE SAMPLE CORPUS  (+ optional Sefaria loader)
# ---------------------------------------------------------------------------
#
# Each verse below is a real, verified Masoretic verse carrying nikud + ta'amim
# (so half-verse splitting on the Atnach is genuine). Paragraph markers shown in
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
    #     TextVariant restores the vav (+6 Absolute).
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
                url, headers={"User-Agent": "tanakh-gematria/1.0"})
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


CORPUS_FILE = pathlib.Path(__file__).parent / "tanach_corpus.jsonl"


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


def _cipher_tuple(consonants: str) -> Tuple[int, ...]:
    vals = compute_all_ciphers(consonants)
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

    def insert(sub_id, book, chapter, verse, parsha, boundary, track, cons, disp=None):
        if not cons:
            return
        cur.execute(
            f"""INSERT INTO units
                (sub_id, book, chapter, verse, parsha, boundary_type,
                 variant_track, consonants, text_display, {CIPHER_INSERT_COLS})
                VALUES (?,?,?,?,?,?,?,?,?,{CIPHER_PLACEHOLDERS})""",
            (sub_id, book, chapter, verse, parsha, boundary, track, cons,
             disp or cons, *_cipher_tuple(cons)),
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
        insert(f.sub_id, f.book, f.chapter, f.verse, f.parsha,
               "Verse", f.variant_track, f.full_consonants)
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
                   f.full_consonants)

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

    # --- 6c. Macro-micro resonance: a verse value divides its Perek total ---
    pereks = df[df.boundary_type == "Perek"]
    perek_idx = {(r.book, r.chapter): r for _, r in pereks.iterrows()}
    for _, vrow in vfull.iterrows():
        prow = perek_idx.get((vrow.book, vrow.chapter))
        if prow is None:
            continue
        for c in CIPHER_NAMES:
            pv, vv = int(prow[c]), int(vrow[c])
            if vv and pv and pv % vv == 0 and pv != vv:
                log("MacroMicro", c, vv, pv,
                    f"{vrow.book} {vrow.chapter}:{vrow.verse}",
                    f"Perek {vrow.book} {vrow.chapter}",
                    f"verse divides chapter (x{pv // vv})")

    conn.commit()


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


def search_phrase(conn: sqlite3.Connection, phrase_consonants: str,
                  colel: bool = False, tracks: Optional[List[str]] = None,
                  boundaries: Optional[List[str]] = None) -> Dict[str, object]:
    """Compute every cipher value for the input phrase and search each one."""
    values = compute_all_ciphers(phrase_consonants)
    results = {c: search_value(conn, c, values[c], colel, tracks, boundaries)
               for c in CIPHER_NAMES}
    return {"values": values, "results": results}


def normalize_query(raw: str) -> str:
    """Clean a Hebrew query string down to its 22-letter consonant skeleton."""
    return strip_to_consonants(raw)


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
    """Max/Min/Mean/Median/Std of the Absolute value per macro-structure type."""
    rows = []
    for b in boundaries:
        df = structure_frame(conn, b)
        if df.empty:
            continue
        col = df["Absolute"]
        rows.append({
            "Structure": b, "Count": int(col.count()),
            "Max": int(col.max()), "Min": int(col.min()),
            "Mean": round(float(col.mean()), 1),
            "Median": float(col.median()),
            "StdDev": round(float(col.std() or 0.0), 1),
        })
    return pd.DataFrame(rows)


def density_gaps(conn: sqlite3.Connection, cipher: str = "Absolute",
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
# SECTION 9.  SELF-TEST  (python app.py selftest)
# ---------------------------------------------------------------------------

def run_selftest() -> None:
    print("=== Gematria engine self-test ===")
    g11 = strip_to_consonants(SAMPLE_CORPUS[0].text)
    assert g_absolute(g11) == 2701, g_absolute(g11)
    print(f"Genesis 1:1 consonants: {g11}")
    print(f"  Absolute = {g_absolute(g11)} (expected 2701)  OK")

    shalom = "שלום"
    assert g_absolute(shalom) == 376, g_absolute(shalom)
    assert g_siduri(shalom) == 52, g_siduri(shalom)
    print(f"  שלום Absolute={g_absolute(shalom)} (376), Siduri={g_siduri(shalom)} (52)  OK")

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
    print("  All 11 ciphers pass spot-checks  OK")

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

    res = search_value(conn, "Absolute", 2701)
    assert (res["Value"] == 2701).any()
    print(f"  Search Absolute=2701 -> {len(res)} hit(s)  OK")

    res_c = search_value(conn, "Absolute", 2700, colel=True)
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
    import matplotlib.pyplot as plt
    import seaborn as sns
    import streamlit as st

    st.set_page_config(page_title="Tanach Gematria Engine",
                       page_icon="📜", layout="wide",
                       initial_sidebar_state="collapsed")
    sns.set_theme(style="whitegrid")

    @st.cache_resource(show_spinner="Building gematria database…")
    def _build_connection(extra_refs_key: str, _nonce: int):
        # Primary corpus: bundled full Tanach. Falls back to SAMPLE_CORPUS
        # if the file isn't present (e.g. development without the data file).
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

    st.title("Tanach Gematria Search & Structural Pattern Engine")
    st.caption(
        "Multi-cipher gematria engine over the complete Masoretic text — "
        "23,206 cantillated verses sourced from Sefaria. "
        "All cipher values are computed from consonants only; "
        "nikud and ta'amim are stripped before counting. "
        "Individual references can be appended via the sidebar."
    )

    with st.sidebar:
        st.header("⚙️ Corpus")
        st.markdown("Full Tanach loaded from bundled corpus (23,206 verses). "
                    "Optionally append additional Sefaria references below "
                    "(requires internet; semicolon-separated):")
        extra = st.text_input("Sefaria refs", value="",
                              placeholder="Genesis 1; Psalms 23; Exodus 20")
        st.divider()
        st.subheader("Active ciphers (11)")
        st.write(", ".join(CIPHER_NAMES))
        st.caption("Required: Absolute, Katan, Gadol, Atbash, Albam, Atbah, "
                   "Avgad.  Researched additions: Siduri, Ribua, Kidmi, Achbi.")

    conn, n_loaded, verse_index = get_connection(extra)

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

    def render_verse_detail(book, chapter, verse, boundary, matched_text=None):
        if boundary not in DETAIL_BOUNDARIES:
            return
        v = verse_index.get((book, int(chapter), int(verse)))
        if v is None:
            st.info("Source text not available for this unit.")
            return
        st.markdown(f"**{book} {chapter}:{verse}** · _{boundary}_")
        sub_unit = boundary in ("Word", "FirstHalf", "SecondHalf")
        if sub_unit and v.text:
            matched_cons = strip_to_consonants(matched_text) if matched_text else None
            highlighted = _highlight_in_verse(v.text, boundary, matched_cons)
            st.markdown(f"**Cantillated:** {highlighted}", unsafe_allow_html=True)
        else:
            st.markdown(f"**Cantillated:** {v.text}")
        # Cipher values: matched sub-unit when available, full verse otherwise
        if sub_unit and matched_text:
            cons = strip_to_consonants(matched_text)
            st.markdown(f"**Matched consonants:** `{cons}`")
        else:
            cons = strip_to_consonants(v.text)
            st.markdown(f"**Consonants:** `{cons}`")
        vals = {name: fn(cons) for name, fn in CIPHERS.items()}
        st.dataframe(pd.DataFrame([vals]), use_container_width=True, hide_index=True)
        if boundary in ("Petucha", "Setuma"):
            run = _paragraph_run(book, chapter, verse)
            if run and len(run) > 1:
                st.markdown("**Full paragraph block:**")
                for rv in run:
                    st.markdown(f"- {rv.book} {rv.chapter}:{rv.verse} — {rv.text}")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "1 · Phrase & Name Matcher",
        "2 · Scriptural Structural Explorer",
        "3 · Textual Echoes & Anomalies",
        "4 · Macro Statistical Dashboard",
        "📖 Guide & Sources",
    ])

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
                "Variant tracks", ["Ksiv", "Kri", "TextVariant", "Aggregate"],
                default=["Ksiv", "Kri", "TextVariant"])
        with cc2:
            bounds = st.multiselect(
                "Boundary types",
                ["Word", "FirstHalf", "SecondHalf", "Verse",
                 "Perek", "Parsha", "Petucha", "Setuma"],
                default=["Word", "Verse", "FirstHalf", "SecondHalf"])

        if cons:
            payload = search_phrase(conn, cons, colel=colel,
                                    tracks=tracks or None, boundaries=bounds or None)
            vals = payload["values"]
            st.markdown("#### Computed values across all ciphers")
            st.dataframe(pd.DataFrame([vals]), use_container_width=True,
                         hide_index=True)

            cipher = st.selectbox("Show matches for cipher", CIPHER_NAMES, index=0)
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
                            matched_text=row.get("Text"))
        else:
            st.warning("Enter a Hebrew or transliterable phrase to search.")

    # ===================== TAB 2: STRUCTURAL EXPLORER =====================
    with tab2:
        st.subheader("Scriptural Structural Explorer")
        kind = st.radio("Browse by", ["Perek", "Parsha", "Petucha", "Setuma",
                                       "Verse"], horizontal=True)
        df = structure_frame(conn, kind)
        if df.empty:
            st.info(f"No '{kind}' units in the loaded corpus yet. "
                    "Load full chapters from Sefaria to populate macro structures.")
        else:
            display_cols = (["book", "chapter", "verse", "parsha", "sub_id",
                             "variant_track"] + CIPHER_NAMES)
            show = df[[c for c in display_cols if c in df.columns]].rename(
                columns={"book": "Book", "chapter": "Chapter", "verse": "Verse",
                         "parsha": "Parsha", "sub_id": "ID",
                         "variant_track": "Track"})
            q = st.text_input("Filter (book / parsha contains)", "")
            if q:
                mask = (show["Book"].str.contains(q, case=False, na=False) |
                        show["Parsha"].str.contains(q, case=False, na=False))
                show = show[mask]
            event2 = st.dataframe(
                show, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="t2_sel")
            st.caption(f"{len(show)} '{kind}' unit(s). Every cipher column is an "
                       "indexed gematria total for that structural block.")
            if kind in DETAIL_BOUNDARIES and event2.selection.rows:
                row2 = show.iloc[event2.selection.rows[0]]
                with st.expander("📜 Verse detail", expanded=True):
                    render_verse_detail(row2["Book"], row2["Chapter"], row2["Verse"], kind)

    # ===================== TAB 3: ECHOES & ANOMALIES =====================
    with tab3:
        st.subheader("Textual Echoes & Anomalies Tracker")
        pat = pd.read_sql_query("SELECT * FROM patterns", conn)
        if pat.empty:
            st.info("No patterns flagged in the current corpus. Internal-balance, "
                    "proximity-echo and macro-micro detectors run automatically on "
                    "load — add more chapters to surface more anomalies.")
        else:
            counts = pat["pattern_type"].value_counts().to_dict()
            m1, m2, m3 = st.columns(3)
            m1.metric("Internal balances", counts.get("InternalBalance", 0))
            m2.metric("Proximity echoes", counts.get("ProximityEcho", 0))
            m3.metric("Macro–micro resonances", counts.get("MacroMicro", 0))

            ptype = st.selectbox("Anomaly type",
                                 ["(all)"] + sorted(pat["pattern_type"].unique()))
            cfilter = st.selectbox("Cipher", ["(all)"] + CIPHER_NAMES)
            view = pat.copy()
            if ptype != "(all)":
                view = view[view.pattern_type == ptype]
            if cfilter != "(all)":
                view = view[view.cipher == cfilter]
            view = view.rename(columns={
                "pattern_type": "Type", "cipher": "Cipher", "value_a": "Value A",
                "value_b": "Value B", "ref_a": "Reference A", "ref_b": "Reference B",
                "detail": "Detail"}).drop(columns=["pattern_id"])
            st.dataframe(view, use_container_width=True, hide_index=True)

    # ===================== TAB 4: STATISTICS DASHBOARD ===================
    with tab4:
        st.subheader("Macro Statistical Dashboard")

        st.markdown("#### Extremes ticker")
        ext = extremes_table(conn, ["Verse", "Perek", "Parsha",
                                    "Petucha", "Setuma", "Word"])
        if not ext.empty:
            st.dataframe(ext, use_container_width=True, hide_index=True)

        st.markdown("#### Distribution histograms (verse totals)")
        # Each verse appears exactly once. The per-verse Petucha/Setuma rows are
        # the SAME verses re-tagged, so including them would double-count any
        # marker-bearing verse and skew the distribution; paragraph-level stats
        # live in the extremes ticker above instead.
        plot_df = structure_frame(conn, "Verse")

        if plot_df.empty:
            st.info("Not enough structural data to plot. Load chapters from Sefaria.")
        else:
            # Stack vertically (one chart per row) so each is legible on a phone;
            # a tall figure scales to the screen width on mobile and desktop alike.
            fig, axes = plt.subplots(3, 1, figsize=(7, 11))
            for ax, c, color in zip(axes, ["Absolute", "Katan", "Atbash"],
                                    ["#2c6fbb", "#bb572c", "#3aa66f"]):
                series = plot_df[c].dropna()
                kde = series.nunique() > 2
                sns.histplot(series, kde=kde, ax=ax, color=color, bins=20)
                ax.set_title(f"{c} totals")
                ax.set_xlabel("Gematria value")
            fig.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
            st.caption(f"{len(plot_df)} verse(s), each counted once (Ksiv track).")

            st.markdown("#### Density 'dead zones' (Absolute, verses)")
            dz = density_gaps(conn, "Absolute", "Verse")
            st.write(f"Observed range **{dz['min']}–{dz['max']}**, "
                     f"**{len(dz['present'])}** distinct values present, "
                     f"**{len(dz['gaps'])}** unrepresented gap-band(s).")
            if dz["gaps"]:
                gap_df = pd.DataFrame(
                    [{"Gap start": g[0], "Gap end": g[1],
                      "Width": g[1] - g[0] + 1} for g in dz["gaps"][:50]])
                st.dataframe(gap_df, use_container_width=True, hide_index=True)
                st.caption("These integer ranges have zero verse representation in "
                           "the loaded corpus — statistical uniqueness rises near "
                           "wide dead zones.")

    # ===================== TAB 5: GUIDE & SOURCES ========================
    with tab5:
        st.subheader("📖 Guide & Sources")
        st.caption(
            "Every cipher, variant track, boundary type, and rule used by this engine "
            "— with its traditional name and scholarly source. Cipher rules are exact "
            "(verified against the engine code). Historical attributions are traditional "
            "and noted where uncertain."
        )

        with st.expander("The 11 gematria ciphers", expanded=True):
            st.dataframe(pd.DataFrame([
                {"Cipher": "Absolute",
                 "Hebrew": "מספר הכרחי / ישר (Mispar Hechrachi)",
                 "Rule": "Standard values: א=1 … י=10, כ=20 … ק=100 … ת=400. Finals = same as base form.",
                 "Earliest Source": "Biblical era (attested in use). Rabbinic formulation: BT Nedarim 32a interprets the '318 servants' of Gen. 14:14 as the name אֱלִיעֶזֶר (=318) — the earliest clear Talmudic use. The term 'gematriot' appears as a category of wisdom in Mishnah Avot 3:18. BT Sanhedrin 22a discusses the practice explicitly."},
                {"Cipher": "Katan",
                 "Hebrew": "מספר קטן (Mispar Katan)",
                 "Rule": "Reduce each letter to its significant digit (drop trailing zeros: ק=1, מ=4), then sum.",
                 "Earliest Source": "Medieval. No Talmudic source for this specific reduction. Formalized in Hasidei Ashkenaz tradition (12th–13th c.), appearing in works such as Sefer Gematriot (attr. R. Yehuda he-Hasid, d. 1217)."},
                {"Cipher": "Gadol",
                 "Hebrew": "מספר גדול (Mispar Gadol)",
                 "Rule": "Like Absolute, but final forms carry 500–900: ך=500, ם=600, ן=700, ף=800, ץ=900.",
                 "Earliest Source": "The 27-letter sequence including finals is described in Sefer Yetzirah 2:2 (dated 3rd–6th c. CE by scholarship; earlier by tradition). Practical use with the higher values in gematria appears in Sefer ha-Bahir (12th c.) and the Zohar (13th c.)."},
                {"Cipher": "Atbash",
                 "Hebrew": "אתב\"ש (At-Bash)",
                 "Rule": "Mirror the alphabet: א↔ת, ב↔ש, ג↔ר … then Absolute values of the swapped letters.",
                 "Earliest Source": "The oldest attested gematria cipher — it appears in the Hebrew Bible itself. 'Sheshach' (שֵׁשַׁךְ) in Jeremiah 25:26 and 51:41 is Babel (בָּבֶל) by Atbash. Recognized explicitly in BT Sanhedrin 22b. Classified as a temurah system in Sefer Yetzirah ch. 2."},
                {"Cipher": "Albam",
                 "Hebrew": "אלב\"ם (Al-Bam)",
                 "Rule": "Split 22 letters into two groups of 11; swap across groups: א↔ל, ב↔מ, ג↔נ … (ROT-11).",
                 "Earliest Source": "Classical temurah described in Sefer Yetzirah ch. 2 (3rd–6th c. CE). Elaborated in Sefer Yetzirah commentaries by Rav Saadia Gaon (882–942 CE) and R. Dunash ibn Tamim (10th c.)."},
                {"Cipher": "Atbah",
                 "Hebrew": "אטב\"ח (At-Bach)",
                 "Rule": "Pairs whose values sum to 10/100/1000: א↔ט, ב↔ח; י↔צ, כ↔פ; ק↔ץ … Finals carry 600–900.",
                 "Earliest Source": "Attributed to Rabbi Eliezer ben Yose ha-Gelili, a 2nd-century Tanna. The full name 'Atbah of R. Eliezer' appears in the Baraita of 32 Hermeneutical Rules (Tannaic era, transmitted in medieval compilations) and in Midrashic literature."},
                {"Cipher": "Avgad",
                 "Hebrew": "אבג\"ד (Av-Gad)",
                 "Rule": "+1 cyclic shift: א→ב, ב→ג … ת→א. Then Absolute values of the shifted letters.",
                 "Earliest Source": "Classical cyclic temurah. The concept of cyclic letter shifting appears within the temurah tradition of Sefer Yetzirah (3rd–6th c. CE). The specific Avgad cipher is named and elaborated in medieval Kabbalistic works."},
                {"Cipher": "Siduri",
                 "Hebrew": "מספר סידורי (Mispar Siduri)",
                 "Rule": "Ordinal position: א=1, ב=2 … ת=22. Sequence, not standard value.",
                 "Earliest Source": "Ordinal letter counting is implicit in Talmudic letter-position discussions (e.g. BT Shabbat 104a on letter forms and sequence). As a formal gematria cipher, widely attested in Midrashic literature and medieval biblical commentary."},
                {"Cipher": "Ribua",
                 "Hebrew": "מספר מרובע (Mispar Meruba Pratti)",
                 "Rule": "Square each letter's Absolute value, then sum all squares (Σ v²).",
                 "Earliest Source": "Medieval Kabbalistic. No Talmudic source. Appears in Sefer ha-Bahir (Provence, 12th c.) and later Zoharic and Lurianic literature."},
                {"Cipher": "Kidmi",
                 "Hebrew": "מספר קדמי / משולש (Mispar Kidmi)",
                 "Rule": "Triangular cumulative: each letter's value = sum of all Absolute values from א up to it. א=1, ב=3, ג=6 … ת=1495.",
                 "Earliest Source": "Medieval Kabbalistic. No Talmudic source. Appears in later Kabbalistic computational texts; the triangular-number principle is implicit in Pythagorean numerology as absorbed into medieval Jewish mysticism."},
                {"Cipher": "Achbi",
                 "Hebrew": "אכב\"י (Ach-Bi)",
                 "Rule": "Split into two 11-letter groups, reverse each internally: א↔כ, ב↔י … ל↔ת, מ↔ש …",
                 "Earliest Source": "Classical temurah variant. Part of the temurah permutation tradition in Sefer Yetzirah ch. 2 (3rd–6th c. CE). A less common scheme; named and discussed in medieval Kabbalistic commentaries."},
            ]), use_container_width=True, hide_index=True)

        with st.expander("Variant tracks"):
            st.markdown("""
**Ksiv (כְּתִיב — "Written")** — The consonantal text exactly as written in the Torah scroll. The default track; every verse is recorded here. The Masoretes went to extraordinary lengths to preserve this text letter-perfect.

**Kri (קְרֵי — "Read")** — The text as traditionally *read aloud*, sometimes differing from the written form. Marginal notes mark every divergence. Different consonants → different gematria totals. Example: Psalms 100:3 written לֹא (alef), read לוֹ (vav), difference = 1 Absolute.

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
                {"Boundary": "FirstHalf",         "Meaning": "From verse start to the Atnach-bearing word (inclusive).",                                     "Why meaningful": "The Atnach (֑) is the verse's primary cantillation pause — its main syntactic division."},
                {"Boundary": "SecondHalf",        "Meaning": "From after the Atnach to verse end.",                                                          "Why meaningful": "The second syntactic unit; internal balance between halves is a recognized gematria pattern."},
                {"Boundary": "Verse (פסוק)",      "Meaning": "One Masoretic verse, ending at Sof Pasuq (׃).",                                               "Why meaningful": "The canonical citation and reading unit."},
                {"Boundary": "Petucha (פ)",       "Meaning": "'Open' paragraph — a full blank line to end of scroll column; a major thematic break.",        "Why meaningful": "A deliberate Masoretic division, larger than a verse. One of two authentic paragraph units."},
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


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        run_selftest()
    else:
        run_app()
