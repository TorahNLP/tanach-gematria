"""Build and score a HUMAN review of verse tags on a random sample.

Joshua reviews the sample on the app's locked "Review tags" page
(?view=app&page=tagreview), one verse at a time: Correct / Wrong / Unsure.
The 40-verse check (verse_tags_check.py) is hand-picked and tricky; this is the
error rate on ORDINARY verses, which gates the full run.

    python verse_tags_review.py build --chapters 25 --seed 1   # tag + pick sample
    python verse_tags_review.py report                          # score his answers

Tagging goes through build_verse_tags.run_gemini (same prompt, models, quota
handling), into tag_review/tags.jsonl, so the review judges exactly what the
hourly job would produce.
"""
import argparse
import collections
import csv
import io
import contextlib
import json
import os
import random
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
with contextlib.redirect_stderr(io.StringIO()):
    import build_verse_tags as bvt
app = bvt.app

REVIEW_DIR = os.path.join(HERE, "tag_review")
TAGS = os.path.join(REVIEW_DIR, "tags.jsonl")
SAMPLE = os.path.join(REVIEW_DIR, "sample.jsonl")          # read by the app
RESULTS = os.path.join(REVIEW_DIR, "results.csv")          # written by the app


def set_aside(tag: dict, fit: str = "celebration") -> bool:
    """Same verdict the ranking uses (app.occ_context_verdict), for one kind."""
    t = {"tone": tag.get("tone"), "themes": tag.get("themes") or [],
         "jars_at": tag.get("jars_at") or []}
    _, aside = app.occ_context_verdict(t, {"fit": fit, "promote": []})
    return bool(aside)


def build(n_chapters: int, seed: int, per_side: int):
    os.makedirs(REVIEW_DIR, exist_ok=True)
    verses = app.load_from_jsonl()
    chapters = sorted({(v.book, v.chapter) for v in verses})
    rng = random.Random(seed)
    picked = rng.sample(chapters, n_chapters)
    print("chapters:", ", ".join(f"{b} {c}" for b, c in picked))
    args = types.SimpleNamespace(out=TAGS, limit=0,
                                 refs=[f"{b} {c}" for b, c in picked])
    bvt.run_gemini(args, verses, app.load_english())

    latest = {}
    for line in open(TAGS, encoding="utf-8"):
        r = json.loads(line)
        latest[(r["book"], r["chapter"], r["verse"])] = r
    aside = [r for r in latest.values() if set_aside(r)]
    keep = [r for r in latest.values() if not set_aside(r)]
    rng.shuffle(aside)
    rng.shuffle(keep)
    sample = aside[:per_side] + keep[:per_side]
    rng.shuffle(sample)          # don't let the order give the answer away
    with open(SAMPLE, "w", encoding="utf-8") as fh:
        for r in sample:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(latest)} verses tagged; {len(aside)} set aside, {len(keep)} kept; "
          f"sample of {len(sample)} written to {SAMPLE}")


def report():
    if not os.path.exists(RESULTS):
        print("no answers yet")
        return
    sample = {(r["book"], r["chapter"], r["verse"]): r
              for r in map(json.loads, open(SAMPLE, encoding="utf-8"))}
    answers = {}
    for row in csv.DictReader(open(RESULTS, encoding="utf-8")):
        answers[(row["book"], int(row["chapter"]), int(row["verse"]))] = row
    by = collections.defaultdict(collections.Counter)
    for k, a in answers.items():
        side = "set aside" if set_aside(sample[k]) else "kept"
        by[side][a["verdict"]] += 1
    total = collections.Counter()
    for side, c in by.items():
        total.update(c)
        judged = c["correct"] + c["wrong"]
        print(f"{side:10} correct {c['correct']:3}  wrong {c['wrong']:3}  unsure "
              f"{c['unsure']:3}  → {100 * c['wrong'] / judged if judged else 0:.0f}% wrong")
    judged = total["correct"] + total["wrong"]
    print(f"{'overall':10} {len(answers)}/{len(sample)} answered; "
          f"{100 * total['wrong'] / judged if judged else 0:.0f}% wrong of those judged")
    print("\nMarked wrong:")
    for k, a in answers.items():
        if a["verdict"] == "wrong":
            r = sample[k]
            print(f"  {k[0]} {k[1]}:{k[2]}  model: {'SET ASIDE' if set_aside(r) else 'kept'} "
                  f"[{r.get('tone')}; {','.join(r.get('themes') or [])}]  note: {a.get('note', '')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--chapters", type=int, default=25)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--per-side", type=int, default=50)
    sub.add_parser("report")
    a = ap.parse_args()
    build(a.chapters, a.seed, a.per_side) if a.cmd == "build" else report()
