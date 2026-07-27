#!/usr/bin/env python3
"""Fetch the JPS 1917 English translation into a sidecar corpus file.

Companion to fetch_corpus.py, which fetches the cantillated Hebrew. This
produces tanach_english.jsonl with one line per verse:

    {"book": str, "chapter": int, "verse": int, "en": str}

Keyed identically to tanach_corpus.jsonl (English book name, chapter, verse) so
the app can join the two without any reconciliation step.

WHY JPS 1917 AND NOT THE DEFAULT JPS:
Sefaria's default English for Tanach is "Tanakh: The Holy Scriptures, published
by JPS" (1985), which is licensed CC-BY-NC. A NonCommercial restriction is not
something to bake into a public deploy. "The Holy Scriptures: A New Translation
(JPS 1917)" is Public Domain and carries no such restriction, so it is what we
bundle. The phrasing is archaic in places; that is the accepted trade for a
text we can redistribute freely.

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
VERSION = "english|The Holy Scriptures: A New Translation (JPS 1917)"

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
_TAG_RE = re.compile(r"<[^>]+>")


def clean(raw: str) -> str:
    """Strip Sefaria's HTML down to plain verse text."""
    import html as _h

    if not raw:
        return ""
    txt = _FOOTNOTE_RE.sub("", raw)
    txt = _TAG_RE.sub("", txt)
    txt = _h.unescape(txt)
    # Collapse the whitespace left behind by removed tags.
    return re.sub(r"\s+", " ", txt).strip()


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


def main() -> int:
    books = books_from_corpus()
    print(f"Fetching JPS 1917 for {len(books)} books -> {OUT_FILE.name}")
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
    print(f"\nWrote {total} verses to {OUT_FILE}")
    if failed:
        print(f"FAILED books ({len(failed)}): {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
