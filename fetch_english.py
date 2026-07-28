#!/usr/bin/env python3
"""Fetch the JPS 1917 English translation into a sidecar corpus file.

Companion to fetch_corpus.py, which fetches the cantillated Hebrew. This
produces tanach_english.jsonl with one line per verse:

    {"book": str, "chapter": int, "verse": int, "en": str}

Keyed identically to tanach_corpus.jsonl (English book name, chapter, verse) so
the app can join the two without any reconciliation step.

WHICH TRANSLATION, AND THE LICENSING TRADE:
This bundles "Tanakh: The Holy Scriptures, published by JPS" (1985), which is
licensed **CC-BY-NC**. The public-domain alternative, "The Holy Scriptures: A
New Translation (JPS 1917)", was fetched and reviewed first and rejected as too
archaic for readers: 53% of its verses carry thee/thou/unto/hath. Readability
won, deliberately, over the freer licence.

Two consequences of that choice, both already handled but worth keeping in mind
before swapping this back:
  * CC-BY-NC requires **attribution**, which the app renders wherever the
    translation appears (see ENGLISH_ATTRIBUTION in app.py).
  * CC-BY-NC forbids **commercial** redistribution. The app is itself licensed
    CC BY-NC 4.0, so this is consistent with how the project is already shipped
    — but it does mean the translation cannot be relicensed commercially later
    without swapping it back to the 1917 text.

To swap: change VERSION below, rerun, and update the licence strings in app.py
(ENGLISH_VERSION_LABEL / ENGLISH_ATTRIBUTION) and README.md to match.

Run:  python fetch_english.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OUT_FILE = pathlib.Path(__file__).parent / "tanach_english.jsonl"
CORPUS_FILE = pathlib.Path(__file__).parent / "tanach_corpus.jsonl"

BASE = "https://www.sefaria.org/api/v3/texts/"
VERSION = "english|Tanakh: The Holy Scriptures, published by JPS"
# Public-domain fallback, kept here so the swap is a one-line edit:
FALLBACK_VERSION = "english|The Holy Scriptures: A New Translation (JPS 1917)"

# JPS 1985 omits Joshua 21:36-37, which are absent from some Masoretic
# manuscripts, so those two verses came back with Hebrew but no English. The
# public-domain JPS 1917 does translate them (bracketed, to mark the textual
# doubt), so they are filled from there rather than left blank. Nothing else
# in the corpus needs this — it is exactly two verses.
FALLBACK_REFS = [("Joshua", 21, 36), ("Joshua", 21, 37)]

# Sefaria returns translation text with presentational and editorial HTML:
#   <big><strong>W</strong></big>hen God began...      (drop cap)
#   <sup class="footnote-marker">a</sup><i class="footnote">...</i>
# The footnote *body* must be removed wholesale (it is editorial apparatus, not
# the verse), while ordinary tags are merely unwrapped. Order matters: strip the
# footnote elements first, then unwrap whatever markup remains.
_FOOTNOTE_RE = re.compile(
    r"<sup[^>]*class=[\"']?footnote-marker[\"']?[^>]*>.*?</sup>"
    r"|<i[^>]*class=[\"']?footnote[\"']?[^>]*>.*?</i>",
    re.IGNORECASE | re.DOTALL,
)
# Line/verse-structure tags (poetry in Psalms etc. is laid out with <br>).
# Dropping these like any other tag would fuse words across the break —
# "green pastures;He leads me" — so they become a space, not nothing.
_BREAK_RE = re.compile(r"<br\s*/?>|</p>|</div>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# The 1985 JPS closes a footnoted span with a bare "-a" / "-b" anchor that has
# no markup around it, so tag-stripping alone leaves it stranded mid-sentence
# ("water in places of repose;-a"). Removed only when preceded by a letter or
# punctuation, so ordinary hyphenated words are untouched.
_ORPHAN_ANCHOR_RE = re.compile(r"(?<=[\w.,;:!?”’)\]])-[a-z](?=\s|$|[.,;:!?”’)\]])")


def clean(raw: str) -> str:
    """Strip Sefaria's HTML down to plain verse text."""
    import html as _h

    if not raw:
        return ""
    # Order matters: footnote bodies first (they are editorial apparatus, not
    # the verse), then line breaks to spaces, then unwrap whatever markup
    # remains, then the orphaned anchors the footnotes left behind.
    txt = _FOOTNOTE_RE.sub("", raw)
    txt = _BREAK_RE.sub(" ", txt)
    txt = _TAG_RE.sub("", txt)
    txt = _h.unescape(txt)
    txt = _ORPHAN_ANCHOR_RE.sub("", txt)
    # Collapse the whitespace left behind by removed tags, and tidy the space
    # that removing an inline anchor can leave in front of punctuation.
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"\s+([.,;:!?])", r"\1", txt)
    return txt.strip()


def books_from_corpus() -> list[str]:
    """Book list in corpus order, so the English file mirrors the Hebrew one."""
    seen: dict[str, None] = {}
    with open(CORPUS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                seen.setdefault(json.loads(line)["book"], None)
    return list(seen)


def fetch_book(book: str, timeout: int = 45, retries: int = 3):
    """Yield (chapter, verse, english) for one book.

    Sefaria serves a whole book in one call: text is a list of chapters, each a
    list of verse strings. Verses absent from the translation come back as empty
    strings and are skipped rather than written as blanks.
    """
    query = urllib.parse.urlencode(
        {"version": VERSION}, quote_via=urllib.parse.quote)
    url = f"{BASE}{urllib.parse.quote(book)}?{query}"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "tanach-gematria/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:                      # noqa: BLE001
            last_err = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"{book}: {last_err}")

    versions = data.get("versions", [])
    if not versions:
        raise RuntimeError(f"{book}: no version returned")
    text = versions[0].get("text", [])
    chapters = text if (text and isinstance(text[0], list)) else [text]
    for ci, chap in enumerate(chapters, start=1):
        if not isinstance(chap, list):
            continue
        for vi, vtext in enumerate(chap, start=1):
            if isinstance(vtext, list):
                vtext = " ".join(vtext)
            en = clean(vtext or "")
            if en:
                yield ci, vi, en


def fetch_one(ref: str, version: str, timeout: int = 30) -> str:
    """Fetch a single verse from a named version; "" on any failure."""
    query = urllib.parse.urlencode({"version": version},
                                   quote_via=urllib.parse.quote)
    try:
        req = urllib.request.Request(
            f"{BASE}{urllib.parse.quote(ref)}?{query}",
            headers={"User-Agent": "tanach-gematria/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data.get("versions", [{}])[0].get("text", "")
        if isinstance(text, list):
            text = " ".join(map(str, text))
        # JPS 1917 brackets these verses to flag the textual doubt. The app
        # already uses square brackets for the Kri, so they are stripped here
        # to avoid two unrelated meanings for the same notation.
        return clean(str(text)).strip("[]").strip()
    except Exception:                                 # noqa: BLE001
        return ""


def main() -> int:
    books = books_from_corpus()
    print(f"Fetching English for {len(books)} books -> {OUT_FILE.name}")
    total, failed = 0, []
    with open(OUT_FILE, "w", encoding="utf-8") as out:
        for i, book in enumerate(books, 1):
            try:
                n = 0
                for ch, vs, en in fetch_book(book):
                    out.write(json.dumps(
                        {"book": book, "chapter": ch, "verse": vs, "en": en},
                        ensure_ascii=False) + "\n")
                    n += 1
                total += n
                print(f"  [{i:2}/{len(books)}] {book:<16} {n:>5} verses")
            except Exception as exc:                  # noqa: BLE001
                failed.append(book)
                print(f"  [{i:2}/{len(books)}] {book:<16} FAILED: {exc}")
            out.flush()
            time.sleep(0.4)          # be polite to Sefaria

        # Fill the handful of verses the primary version omits (see
        # FALLBACK_REFS) from the public-domain JPS 1917.
        for book, ch, vs in FALLBACK_REFS:
            txt = fetch_one(f"{book} {ch}.{vs}", FALLBACK_VERSION)
            if txt:
                out.write(json.dumps(
                    {"book": book, "chapter": ch, "verse": vs, "en": txt},
                    ensure_ascii=False) + "\n")
                total += 1
                print(f"  [fallback] {book} {ch}:{vs} from JPS 1917")
            else:
                print(f"  [fallback] {book} {ch}:{vs} FAILED")
    print(f"\nWrote {total} verses to {OUT_FILE}")
    if failed:
        print(f"FAILED books ({len(failed)}): {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
