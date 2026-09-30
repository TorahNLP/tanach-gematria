"""Check a tagging model against a hand-picked verse sample BEFORE a full run.

Joshua, 2026-09-30: "I don't want this to fully run only to find out 10% is
mislabeled." Run this after any change to the prompt or the model, and before
re-enabling the hourly "Gematria Verse Tagging" task.

    python verse_tags_check.py gemini-3.5-flash     # Gemini, one request per chapter
    python verse_tags_check.py gemma4:e4b            # a local Ollama model

The expected answers include JOSHUA'S RULINGS, which override modern instinct:
burning an asherah is positive (Devarim 7:5, 12:3) and so is Esther 9:5, the
climax of the megillah. Add to SAMPLE as more rulings come in.

Each model's tags go through app.occ_context_verdict with the BIRTH profile,
i.e. exactly what the page would do, and are scored against my expected
set-aside / keep call. The expectations are a rough guide for choosing a model,
not ground truth — Joshua reviews the disagreements.

Usage: python tag_bakeoff.py gemma4:e4b qwen3.5:9b gemma4:12b
"""
import csv, json, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
DEV = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEV)
os.chdir(DEV)
import io, contextlib
with contextlib.redirect_stderr(io.StringIO()):
    import build_verse_tags as bvt
app = bvt.app

# (book, chapter, verse, expected for a bris: "aside" | "keep", why)
SAMPLE = [
    ("Leviticus", 14, 55, "aside", "tzaraas"),
    ("Leviticus", 13, 45, "aside", "the metzora cries tamei"),
    ("Leviticus", 15, 2, "aside", "zav"),
    ("Lamentations", 5, 21, "keep", "השיבנו — hope, in Eicha"),
    ("Lamentations", 5, 22, "aside", "utterly rejected us"),
    ("Lamentations", 1, 1, "aside", "the city sits alone"),
    ("Deuteronomy", 7, 5, "keep", "DESTROY their altars and idols"),
    ("Deuteronomy", 12, 3, "keep", "burn their asherim"),
    ("Exodus", 32, 4, "aside", "the golden calf"),
    ("Deuteronomy", 28, 16, "aside", "cursed in the city"),
    ("Deuteronomy", 28, 3, "keep", "blessed in the city"),
    ("Genesis", 1, 28, "keep", "be fruitful and multiply"),
    ("Genesis", 48, 16, "keep", "המלאך הגואל"),
    ("Numbers", 6, 24, "keep", "birkas kohanim"),
    ("Psalms", 128, 3, "keep", "children like olive shoots"),
    ("Jeremiah", 33, 11, "keep", "voice of the chosson and kallah"),
    ("Psalms", 23, 4, "keep", "valley of death — but You are with me"),
    ("Isaiah", 25, 8, "keep", "He will swallow death forever"),
    ("Exodus", 15, 26, "keep", "I am Hashem your healer"),
    ("Jeremiah", 17, 14, "keep", "heal me and I shall be healed"),
    ("Proverbs", 31, 10, "keep", "eishes chayil"),
    ("Genesis", 36, 29, "keep", "chiefs of the Chori (genealogy)"),
    ("Numbers", 26, 11, "keep", "the sons of Korach did not die"),
    ("Leviticus", 7, 24, "keep", "fat of a carcass (technical law)"),
    ("Exodus", 30, 34, "keep", "the incense spices"),
    ("Judges", 19, 29, "aside", "the concubine cut in pieces"),
    ("I Samuel", 31, 4, "aside", "Shaul falls on his sword"),
    ("Ecclesiastes", 3, 2, "keep", "a time to be born, a time to die"),
    ("Ezekiel", 37, 5, "keep", "dry bones shall live"),
    ("Genesis", 22, 17, "keep", "I will multiply your seed"),
    ("Psalms", 127, 3, "keep", "children are a heritage"),
    ("II Kings", 2, 24, "aside", "the bears and the children"),
    ("Hosea", 2, 21, "keep", "I will betroth you"),
    ("Isaiah", 54, 1, "keep", "sing, O barren one"),
    ("Deuteronomy", 21, 21, "aside", "the ben sorer is stoned"),
    ("Esther", 9, 5, "keep", "the Jews strike their enemies — climax of the megillah (Joshua: positive)"),
    ("Psalms", 137, 9, "aside", "dashes your infants"),
    ("Micah", 7, 19, "keep", "cast all their sins into the sea"),
    ("Genesis", 17, 12, "keep", "circumcised at eight days"),
    ("Genesis", 21, 8, "keep", "the feast when Yitzchak was weaned"),
]


def main(models):
    verses = {(v.book, v.chapter, v.verse): v for v in app.load_from_jsonl()}
    english = app.load_english()
    order = list(verses)
    pos = {k: i for i, k in enumerate(order)}
    profile = app.OCCASIONS["Birth · bris"]["context"]
    rows = []
    for m in models:
        print(f"\n=== {m}")
        agree, times = 0, []
        for book, ch, vs, want, why in SAMPLE:
            key = (book, ch, vs)
            p = bvt.prompt_for(verses[key], english, bvt.neighbours_of(key, order, pos))
            t = time.time()
            try:
                res = bvt.ask(m, p)
            except Exception as e:
                print(f"  ! {book} {ch}:{vs} {e}")
                continue
            dt = time.time() - t
            times.append(dt)
            tag = {"tone": res.get("tone") if res.get("tone") in app.VERSE_TONES else "neutral",
                   "themes": [x for x in res.get("themes", []) if x in app.VERSE_THEMES][:3],
                   "jars_at": [k for k in res.get("jars_at") or [] if k in app.VERSE_FIT]}
            _, aside = app.occ_context_verdict(tag, profile)
            got = "aside" if aside else "keep"
            agree += got == want
            mark = "  " if got == want else "✗ "
            print(f"  {mark}{book} {ch}:{vs:<3} want {want:5} got {got:5} "
                  f"{tag['tone']:9} {','.join(tag['themes']):30} jars={','.join(tag['jars_at']):20} {dt:5.1f}s  ({why})")
            row = [m, book, ch, vs, want, got, tag["tone"],
                   ",".join(tag["themes"]), round(dt, 1), why]
            rows.append(row)
            # Saved per verse, so a stopped run keeps what it finished.
            with open(os.path.join(DEV, "verse_tags_check.csv"), "a", newline="",
                      encoding="utf-8") as fh:
                csv.writer(fh).writerow(row)
        if times:
            first, rest = times[0], times[1:] or times
            print(f"  → agreement {agree}/{len(SAMPLE)}; "
                  f"{sum(rest) / len(rest):.1f}s/verse after load (first {first:.0f}s); "
                  f"full Tanach ≈ {sum(rest) / len(rest) * 23206 / 3600:.0f} h")


def gemini_main(model):
    """Same sample and scoring, but one Gemini request per CHAPTER."""
    import collections
    verses = app.load_from_jsonl()
    english = app.load_english()
    by_ch = collections.defaultdict(list)
    for v in verses:
        by_ch[(v.book, v.chapter)].append(v)
    profile = app.OCCASIONS["Birth · bris"]["context"]
    wanted = collections.defaultdict(list)
    for s in SAMPLE:
        wanted[(s[0], s[1])].append(s)
    print(f"=== {model}  ({len(wanted)} chapter requests)")
    agree = n = 0
    times = []
    for (book, ch), items in wanted.items():
        t = time.time()
        try:
            res = bvt.ask_gemini(model, bvt.chapter_prompt(book, ch, by_ch[(book, ch)], english),
                                 os.environ["GEMINI_API_KEY"])
        except bvt.QuotaExhausted:
            print(f"  ! {model}: DAILY quota exhausted — stopping", flush=True)
            break
        except Exception as e:
            print(f"  ! {book} {ch}: {e}")
            continue
        times.append(time.time() - t)
        for book_, ch_, vs, want, why in items:
            r = res.get(vs, {})
            tag = {"tone": r.get("tone") if r.get("tone") in app.VERSE_TONES else "neutral",
                   "themes": [x for x in r.get("themes", []) if x in app.VERSE_THEMES][:3],
                   "jars_at": [k for k in r.get("jars_at") or [] if k in app.VERSE_FIT]}
            _, aside = app.occ_context_verdict(tag, profile)
            got = "aside" if aside else "keep"
            n += 1
            agree += got == want
            mark = "  " if got == want else "✗ "
            print(f"  {mark}{book} {ch}:{vs:<3} want {want:5} got {got:5} {tag['tone']:9} "
                  f"{','.join(tag['themes']):30} jars={','.join(tag['jars_at']):20} "
                  f"(chapter of {len(by_ch[(book, ch)])} in {times[-1]:.1f}s)  ({why})", flush=True)
            with open(os.path.join(DEV, "verse_tags_check.csv"), "a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow([model, book, ch, vs, want, got, tag["tone"],
                                         ",".join(tag["themes"]), round(times[-1], 1), why])
        time.sleep(bvt.PACE["gap"])
    print(f"  → agreement {agree}/{n}; {sum(times) / max(1, len(times)):.1f}s per chapter; "
          f"full Tanach = 929 requests")


if __name__ == "__main__":
    if sys.argv[1:2] and sys.argv[1].startswith("gemini"):
        gemini_main(sys.argv[1])
    else:
        main(sys.argv[1:])
