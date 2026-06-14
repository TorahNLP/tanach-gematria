"""
One-time script: fetch the full Tanach from Sefaria and save to tanach_corpus.jsonl.
Run from the project directory with the venv active:
    python fetch_corpus.py
"""
import json
import sys
import time
import urllib.parse
import urllib.request

TANACH_BOOKS = [
    # Torah
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
    # Nevi'im
    "Joshua", "Judges", "I Samuel", "II Samuel", "I Kings", "II Kings",
    "Isaiah", "Jeremiah", "Ezekiel",
    "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah",
    "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    # Ketuvim
    "Psalms", "Proverbs", "Job", "Song of Songs", "Ruth", "Lamentations",
    "Ecclesiastes", "Esther", "Daniel", "Ezra", "Nehemiah",
    "I Chronicles", "II Chronicles",
]

BASE = "https://www.sefaria.org/api/v3/texts/"
QUERY = urllib.parse.urlencode(
    {"version": "hebrew|Tanach with Ta'amei Hamikra"},
    quote_via=urllib.parse.quote,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch_book(book: str, retries: int = 3) -> list[dict]:
    url = f"{BASE}{urllib.parse.quote(book)}?{QUERY}"
    req = urllib.request.Request(url, headers={"User-Agent": "tanach-gematria/1.0"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            book_name = data.get("book", book)
            sections = data.get("sections", [1])
            try:
                start_chap = int(sections[0]) if sections else 1
            except (ValueError, TypeError):
                start_chap = 1
            versions = data.get("versions", [])
            if not versions:
                print(f"  WARNING: no versions returned for {book}")
                return []
            text = versions[0].get("text", [])
            # flat (single chapter) vs nested (multi-chapter)
            if text and isinstance(text[0], list):
                chapters = text
            else:
                chapters = [text]
            rows = []
            for ci, chap_verses in enumerate(chapters):
                chap_num = start_chap + ci
                for vi, vtext in enumerate(chap_verses, start=1):
                    if isinstance(vtext, list):
                        vtext = " ".join(vtext)
                    if not vtext:
                        continue
                    rows.append({"book": book_name, "chapter": chap_num,
                                 "verse": vi, "text": vtext})
            return rows
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Retry {attempt + 1}/{retries} for {book}: {e}")
                time.sleep(2)
            else:
                print(f"  FAILED: {book} — {e}")
                return []


def main():
    out_path = "tanach_corpus.jsonl"
    total = 0
    failed = []
    with open(out_path, "w", encoding="utf-8") as f:
        for i, book in enumerate(TANACH_BOOKS, 1):
            print(f"[{i:2d}/{len(TANACH_BOOKS)}] {book}...", end=" ", flush=True)
            rows = fetch_book(book)
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            total += len(rows)
            print(f"{len(rows)} verses")
            if not rows:
                failed.append(book)
            time.sleep(0.3)  # gentle rate-limit courtesy

    print(f"\nDone. {total} verses written to {out_path}")
    if failed:
        print(f"Failed books: {failed}")
    else:
        print("All books fetched successfully.")


if __name__ == "__main__":
    main()
