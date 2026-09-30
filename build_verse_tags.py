"""Tag every verse with its tone and themes, for Occasion search's context check.

Run from the repo root, with a local model server running (Ollama):

    python build_verse_tags.py --model <name>            # the whole Tanach
    python build_verse_tags.py --model <name> --refs "Leviticus 14" "Lamentations 5"
    python build_verse_tags.py --dry-run --refs "Genesis 1"   # show prompts only

Writes verse_tags.jsonl (read by app.py's load_verse_tags). Resumable: verses
already in the file are skipped, so stopping and restarting loses nothing.
Delete the file (or pass --out) to re-tag with a different model.

⚠️ Tags come from the model READING each verse in context, never from keyword
or chapter-range rules (Joshua, 2026-09-30: Eicha ends with השיבנו, and a verse
about idols may be about destroying them). The theme vocabulary is app.py's
VERSE_THEMES, imported rather than copied, so the model and the ranking can
never disagree about what a theme means.

⚠️ Everything here is a SUGGESTION to the ranking. The page never hides a
set-aside verse; it lists it separately. Spot-check a sample before trusting a
full run.
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('app', os.path.join(HERE, 'app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules['app'] = app
spec.loader.exec_module(app)

OLLAMA = "http://127.0.0.1:11434/api/chat"

SYSTEM = (
    "You label verses of Tanach for a tool that suggests verses for Jewish "
    "life-cycle occasions (a bris, a wedding, a yahrzeit). Judge what the "
    "verse is ABOUT and how it would feel read aloud at such an occasion, "
    "using the surrounding verses to understand it. Do not judge by single "
    "words: a verse mentioning idols may be about destroying them; a verse in "
    "a sad book may be hopeful. Answer only with JSON.")

SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string", "enum": list(app.VERSE_TONES)},
        "themes": {"type": "array",
                   "items": {"type": "string", "enum": list(app.VERSE_THEMES)}},
    },
    "required": ["tone", "themes"],
}


def prompt_for(v, english, neighbours) -> str:
    themes = "\n".join(f"- {k}: {d}" for k, d in app.VERSE_THEMES.items())
    context = "\n".join(f"  {b} {c}:{n} — {english.get((b, c, n), '')}"
                        for b, c, n in neighbours)
    return (
        f"Surrounding verses (context only):\n{context}\n\n"
        f"VERSE TO LABEL: {v.book} {v.chapter}:{v.verse}\n"
        f"Hebrew: {app.strip_taamim(v.text)}\n"
        f"English: {english.get((v.book, v.chapter, v.verse), '(none)')}\n\n"
        f"tone — uplifting, neutral, or harsh (harsh = unpleasant to read "
        f"aloud at a happy occasion).\n"
        f"themes — zero to three that the verse is actually about:\n{themes}")


def ask(model: str, prompt: str) -> dict:
    body = json.dumps({
        "model": model, "stream": False, "format": SCHEMA,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(json.loads(r.read())["message"]["content"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="Ollama model name")
    ap.add_argument("--out", default=str(app.VERSE_TAGS_FILE))
    ap.add_argument("--refs", nargs="*",
                    help='limit to chapters, e.g. "Leviticus 14"')
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dry_run and not a.model:
        ap.error("--model is required unless --dry-run")

    verses = app.load_from_jsonl()
    english = app.load_english()
    order = [(v.book, v.chapter, v.verse) for v in verses]
    pos = {k: i for i, k in enumerate(order)}
    if a.refs:
        wanted = {tuple(r.rsplit(" ", 1)) for r in a.refs}
        verses = [v for v in verses if (v.book, str(v.chapter)) in wanted]

    done = set()
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    done.add((r["book"], int(r["chapter"]), int(r["verse"])))
                except (ValueError, KeyError):
                    pass
    todo = [v for v in verses if (v.book, v.chapter, v.verse) not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(todo)} verses to tag ({len(done)} already done)")

    t0 = time.time()
    with open(a.out, "a", encoding="utf-8") as out:
        for n, v in enumerate(todo, 1):
            i = pos[(v.book, v.chapter, v.verse)]
            # Two before, one after, within the same chapter.
            neighbours = [order[j] for j in range(i - 2, i + 2)
                          if j != i and 0 <= j < len(order)
                          and order[j][:2] == (v.book, v.chapter)]
            p = prompt_for(v, english, neighbours)
            if a.dry_run:
                print("=" * 60 + "\n" + p)
                continue
            try:
                res = ask(a.model, p)
            except Exception as e:   # keep going; the verse is retried next run
                print(f"  ! {v.book} {v.chapter}:{v.verse}: {e}")
                continue
            out.write(json.dumps({
                "book": v.book, "chapter": v.chapter, "verse": v.verse,
                "tone": res.get("tone"), "themes": res.get("themes", [])[:3],
                "model": a.model}, ensure_ascii=False) + "\n")
            out.flush()
            if n % 25 == 0 or n == len(todo):
                rate = (time.time() - t0) / n
                print(f"  {n}/{len(todo)}  {rate:.1f}s/verse  "
                      f"~{rate * (len(todo) - n) / 3600:.1f}h left")


if __name__ == "__main__":
    main()
