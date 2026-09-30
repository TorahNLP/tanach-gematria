"""Tag every verse with where it would jar, its tone and its themes, for
Occasion search's context check.

Gemini (the main path; runs unattended, hourly, from a scheduled task):

    python build_verse_tags.py --backend gemini

    Tags one CHAPTER per request, in priority order (Chumash first), until
    every model's free quota is spent, then exits. The next run carries on.

Local model (kept as an option; too slow on this PC to tag everything):

    python build_verse_tags.py --backend ollama --model gemma4:e4b --refs "Genesis 1"

Either way: writes verse_tags.jsonl (read by app.py's load_verse_tags), and is
resumable — verses already in the file are skipped.

⚠️ Tags come from a model READING each verse in context, never from keyword
or chapter-range rules (Joshua, 2026-09-30: Eicha ends with השיבנו, and a verse
about idols may be about destroying them). The vocabularies are app.py's
VERSE_FIT / VERSE_TONES / VERSE_THEMES, imported rather than copied, so the
model and the ranking can never disagree about what a label means.

⚠️ The model must judge from the TORAH's point of view, not modern
sensibilities (Joshua, 2026-09-30): burning an asherah is positive. Every model
in the first bake-off called it "harsh" until told so.

⚠️ Everything here is a SUGGESTION to the ranking. The page never hides a
set-aside verse; it lists it separately.
"""
import argparse
import collections
import csv
import datetime
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('app', os.path.join(HERE, 'app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules['app'] = app
spec.loader.exec_module(app)

SYSTEM = (
    "You label verses of Tanach for a tool that suggests verses for Jewish "
    "occasions. Judge what the verse is ABOUT and how it would feel read "
    "aloud, using the surrounding verses to understand it. Answer only with "
    "JSON.\n\n"
    "Judge from the point of view of the Torah and Chazal, NOT modern "
    "sensibilities. Destroying idolatry, zealotry for Hashem, victory over "
    "Hashem's enemies and the punishment of the wicked are POSITIVE in the "
    "Torah's eyes. What would jar at a simcha is what the Torah itself treats "
    "as sad or ominous: tzaraas and ritual impurity, curses and the "
    "tochacha, the Churban and exile, death and mourning, sin and its "
    "consequences. That includes the LAWS of tzaraas, zav, niddah and other "
    "ritual impurity: they jar at a simcha even though they are mitzvos "
    "written as law.\n\n"
    "Do not judge by single words: a verse mentioning death may be about its "
    "defeat; a verse in a sad book may be hopeful.\n\n"
    "jars_at — the kinds of occasion where reading or quoting this verse "
    "would JAR (feel wrong). Most verses jar nowhere: neutral narrative, "
    "genealogies, most mitzvos and the Mishkan's service are fine anywhere "
    "(the impurity laws above are the exception). List a "
    "kind only when the verse would genuinely be out of place there:\n"
    + "\n".join(f"- {k}: {d}" for k, d in app.VERSE_FIT.items()) +
    "\n\ntone — uplifting, neutral, or harsh, as the Torah sees it.\n\n"
    "themes — zero to three that the verse is actually about:\n"
    + "\n".join(f"- {k}: {d}" for k, d in app.VERSE_THEMES.items()))

# Bump whenever SYSTEM changes, so tags made under older instructions can be
# found and redone. 1 = pre-Torah-view; 2 = Torah view (2026-09-30, no field
# on the line); 3 = + the impurity laws jar at a simcha (Flash-Lite kept
# Vayikra 14:55 and 15:2 as "neutral law").
PROMPT_VERSION = 3

VERSE_LABEL = {
    "jars_at": {"type": "array",
                "items": {"type": "string", "enum": list(app.VERSE_FIT)}},
    "tone": {"type": "string", "enum": list(app.VERSE_TONES)},
    "themes": {"type": "array",
               "items": {"type": "string", "enum": list(app.VERSE_THEMES)}},
}


def tag_record(book, chapter, verse, res: dict, model: str) -> dict:
    """One output line. The loader re-validates every field on read."""
    return {"book": book, "chapter": chapter, "verse": verse,
            "jars_at": res.get("jars_at", []), "tone": res.get("tone"),
            "themes": (res.get("themes") or [])[:3], "model": model,
            "prompt": PROMPT_VERSION}


def load_latest_models(path: str) -> dict:
    """{(book, chapter, verse): model of its LATEST line}. Later lines win, as
    they do in app.load_verse_tags, so an upgrade is just an appended line."""
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    out[(r["book"], int(r["chapter"]), int(r["verse"]))] = r.get("model")
                except (ValueError, KeyError):
                    pass
    return out


def load_done(path: str) -> set:
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    done.add((r["book"], int(r["chapter"]), int(r["verse"])))
                except (ValueError, KeyError):
                    pass
    return done


# ── Local backend (Ollama): one request per VERSE ───────────────────────────
OLLAMA = "http://127.0.0.1:11434/api/chat"


def prompt_for(v, english, neighbours) -> str:
    context = "\n".join(f"  {b} {c}:{n} — {english.get((b, c, n), '')}"
                        for b, c, n in neighbours)
    return (
        f"Surrounding verses (context only):\n{context}\n\n"
        f"VERSE TO LABEL: {v.book} {v.chapter}:{v.verse}\n"
        f"Hebrew: {app.strip_taamim(v.text)}\n"
        f"English: {english.get((v.book, v.chapter, v.verse), '(none)')}")


def neighbours_of(key, order, pos):
    """Two verses before and one after, within the same chapter."""
    i = pos[key]
    return [order[j] for j in range(i - 2, i + 2)
            if j != i and 0 <= j < len(order) and order[j][:2] == key[:2]]


def ask(model: str, prompt: str) -> dict:
    body = json.dumps({
        "model": model, "stream": False,
        "format": {"type": "object", "properties": VERSE_LABEL,
                   "required": list(VERSE_LABEL)},
        # Reasoning ("thinking") models would spend minutes per verse on a
        # one-line label; the schema-constrained answer needs none.
        "think": False,
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(json.loads(r.read())["message"]["content"])


def run_ollama(a, verses, english):
    order = [(v.book, v.chapter, v.verse) for v in verses]
    pos = {k: i for i, k in enumerate(order)}
    if a.refs:
        wanted = {tuple(r.rsplit(" ", 1)) for r in a.refs}
        verses = [v for v in verses if (v.book, str(v.chapter)) in wanted]
    done = load_done(a.out)
    todo = [v for v in verses if (v.book, v.chapter, v.verse) not in done]
    print(f"{len(todo)} verses to tag ({len(done)} already done)")
    with open(a.out, "a", encoding="utf-8") as out:
        for v in todo:
            p = prompt_for(v, english,
                           neighbours_of((v.book, v.chapter, v.verse), order, pos))
            try:
                res = ask(a.model, p)
            except Exception as e:   # retried next run
                print(f"  ! {v.book} {v.chapter}:{v.verse}: {e}")
                continue
            out.write(json.dumps(tag_record(v.book, v.chapter, v.verse, res,
                                            a.model), ensure_ascii=False) + "\n")
            out.flush()


# ── Gemini backend: one request per CHAPTER, quota-aware ────────────────────
# Per Joshua's ask-gemini rules: batch, don't drip-feed. One call per verse
# would be 23,206 requests; one per chapter is 929, and the whole chapter is
# better context. Only public Tanach text and translation is sent.
#
# Quotas (Google's docs, fetched 2026-09-30): requests-per-day reset at MIDNIGHT
# PACIFIC; per-minute limits roll every minute; each MODEL has its own quota. A
# 429 names the limit hit (quotaId, e.g. "...PerDay...FreeTier"), so a daily
# limit parks that model until the reset while a per-minute one just waits.
# ⚠️ Joshua asked to CHECK the reset timing, not assume it: every park, probe
# and recovery is logged with Pacific times, and a parked model is probed early
# (one tiny request, at most every PROBE_EVERY_H hours) to catch a reset that
# comes sooner than documented.
GEMINI = ("https://generativelanguage.googleapis.com/v1beta/models/"
          "{model}:generateContent")
# Best first. Flash-Lite is the LAST resort: on the 40-verse sample (with the
# Torah-view prompt) it scored 37/40 against the ranking and ran ~5 s per
# chapter, but it kept tzaraas / zav law as "neutral" where full Flash set it
# aside. So it buys coverage fast, and the UPGRADE pass below re-tags whatever
# it did with a full Flash model once Flash quota is free.
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash",
                 "gemini-flash-lite-latest"]
LITE_MODELS = {"gemini-flash-lite-latest"}
PROBE_EVERY_H = 2

# ── Availability stats + limit experiments (Joshua, 2026-09-30) ─────────────
# Every HTTP attempt is one row in verse_tags_requests.csv: Pacific time,
# model, outcome, seconds. `--stats` summarises it. Two experiments ride along:
#   1. Early probes (PROBE_EVERY_H): does the daily quota return before the
#      documented midnight-Pacific reset?
#   2. Adaptive pacing: start FASTER than the documented per-minute limit and
#      back off only on a per-minute 429, easing forward again after a clean
#      streak. The logged gap shows the real ceiling.
REQUEST_LOG = None        # set by run_gemini
PACE = {"gap": 3.0, "clean": 0}
PACE_MIN, PACE_MAX = 2.0, 30.0


def _record(model: str, outcome: str, secs: float, detail: str = "") -> None:
    if not REQUEST_LOG:
        return
    new = not os.path.exists(REQUEST_LOG)
    with open(REQUEST_LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time_pt", "model", "outcome", "seconds", "gap_s", "detail"])
        w.writerow([datetime.datetime.now(PT).isoformat(timespec="seconds"),
                    model, outcome, round(secs, 1), PACE["gap"], detail])


def _pace_after(ok: bool) -> None:
    """Ease the gap down after 20 clean requests; per-minute 429s push it up."""
    if ok:
        PACE["clean"] += 1
        if PACE["clean"] >= 20:
            PACE["gap"] = max(PACE_MIN, round(PACE["gap"] * 0.8, 1))
            PACE["clean"] = 0
    else:
        PACE["gap"] = min(PACE_MAX, round(PACE["gap"] * 1.5, 1))
        PACE["clean"] = 0
PT = ZoneInfo("America/Los_Angeles")

# Chumash first, then the books most drawn on for occasions, then the rest.
BOOK_PRIORITY = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
    "Psalms", "Proverbs", "Isaiah", "Song of Songs", "Ruth",
    "Joshua", "Judges", "I Samuel", "II Samuel", "I Kings", "II Kings",
    "Jeremiah", "Ezekiel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah",
    "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah",
    "Malachi", "Job", "Ecclesiastes", "Lamentations", "Esther", "Daniel",
    "Ezra", "Nehemiah", "I Chronicles", "II Chronicles",
]


class QuotaExhausted(Exception):
    pass


class Unavailable(Exception):
    pass


def chapter_prompt(book: str, chapter: int, verses, english) -> str:
    lines = [f"{v.verse}. {app.strip_taamim(v.text)}\n"
             f"   {english.get((v.book, v.chapter, v.verse), '(none)')}"
             for v in verses]
    return (f"Label EVERY verse of {book} chapter {chapter} below, one entry "
            f"per verse number. Read the whole chapter first: it is the "
            f"context for each verse.\n\n" + "\n".join(lines))


def _gemini_type(schema: dict) -> dict:
    """Translate a JSON schema into Gemini's OpenAPI dialect (upper-case)."""
    out = {k: v for k, v in schema.items() if k != "type"}
    out["type"] = schema["type"].upper()
    if "items" in schema:
        out["items"] = _gemini_type(schema["items"])
    if "properties" in schema:
        out["properties"] = {k: _gemini_type(v)
                             for k, v in schema["properties"].items()}
    return out


def _gemini_schema() -> dict:
    item = {"type": "object",
            "properties": {"verse": {"type": "integer"}, **VERSE_LABEL},
            "required": ["verse", *VERSE_LABEL]}
    return _gemini_type({"type": "object",
                         "properties": {"verses": {"type": "array", "items": item}},
                         "required": ["verses"]})


def _quota_kind(err: urllib.error.HTTPError) -> tuple:
    """('day' | 'minute', seconds to wait) from a 429's error body."""
    try:
        details = json.loads(err.read())["error"].get("details", [])
    except Exception:
        return "minute", 60.0
    ids = [v.get("quotaId", "") for d in details for v in d.get("violations", [])]
    delay = 60.0
    for d in details:
        if "retryDelay" in d:
            try:
                delay = float(str(d["retryDelay"]).rstrip("s"))
            except ValueError:
                pass
    return ("day" if any("PerDay" in q for q in ids) else "minute"), delay


def ask_gemini(model: str, prompt: str, key: str) -> dict:
    """{verse number: label} for one chapter, or raise QuotaExhausted /
    Unavailable. Per-minute limits are waited out here; the key is never
    printed."""
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0,
                             "responseMimeType": "application/json",
                             "responseSchema": _gemini_schema()},
    }).encode("utf-8")
    busy = 0
    while True:
        req = urllib.request.Request(
            GEMINI.format(model=model), data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            out = {int(e["verse"]): e for e in json.loads(text)["verses"]}
            _record(model, "ok", time.time() - t0, f"{len(out)} verses")
            return out
        except urllib.error.HTTPError as e:
            secs = time.time() - t0
            if e.code == 429:
                kind, delay = _quota_kind(e)
                _record(model, f"quota_{kind}", secs, f"retry {delay:.0f}s")
                if kind == "day":
                    raise QuotaExhausted(model)
                _pace_after(False)
                time.sleep(min(delay + 2, 120))
                continue
            _record(model, f"http_{e.code}", secs)
            if e.code in (500, 502, 503, 504) and busy < 2:
                busy += 1
                time.sleep(30 * busy)
                continue
            if e.code in (500, 502, 503, 504, 404):
                raise Unavailable(f"{model}: HTTP {e.code}")
            raise
        except (TimeoutError, urllib.error.URLError, ValueError, KeyError) as e:
            # ValueError/KeyError: an answer that is not the promised JSON.
            _record(model, type(e).__name__, time.time() - t0, str(e)[:80])
            if busy < 2:
                busy += 1
                time.sleep(30 * busy)
                continue
            raise Unavailable(f"{model}: {e}")


def _pid_alive(pid: int) -> bool:
    """Is process `pid` running?

    ⚠️ NOT os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, so that call
    does not probe anything — it raised, every lock looked stale, and on
    2026-09-30 two runs went at once. Ask Windows directly instead.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    try:
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        return bool(ok) and code.value == 259    # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def next_reset(now: datetime.datetime) -> datetime.datetime:
    """The next midnight Pacific after `now` (Google's documented RPD reset)."""
    local = now.astimezone(PT)
    return datetime.datetime.combine(local.date() + datetime.timedelta(days=1),
                                     datetime.time(0), tzinfo=PT)


def run_gemini(a, verses, english):
    base = os.path.splitext(a.out)[0]
    log_path, state_path, lock_path = base + ".log", base + ".state.json", base + ".lock"
    global REQUEST_LOG
    REQUEST_LOG = base + "_requests.csv"

    def log(msg):
        stamp = datetime.datetime.now(PT).strftime("%Y-%m-%d %H:%M %Z")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {msg}\n")
        print(msg, flush=True)

    # One run at a time: a quota's worth of chapters can outlast the hour.
    if os.path.exists(lock_path):
        try:
            pid = int(open(lock_path).read().split()[0])
        except (OSError, ValueError, IndexError):
            pid = 0
        if pid and _pid_alive(pid):
            print("another run is active; exiting")
            return
    with open(lock_path, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    try:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            log("! GEMINI_API_KEY is empty; nothing done")
            return
        state = {}
        if os.path.exists(state_path):
            try:
                state = json.load(open(state_path, encoding="utf-8"))
            except ValueError:
                state = {}

        def save():
            json.dump(state, open(state_path, "w", encoding="utf-8"), indent=1)

        now = datetime.datetime.now(PT)
        models = []
        for m in GEMINI_MODELS:
            s = state.get(m, {})
            parked_until = (datetime.datetime.fromisoformat(s["reset"])
                            if s.get("reset") else None)
            if parked_until and now < parked_until:
                last_probe = datetime.datetime.fromisoformat(
                    s.get("probed", s["parked"]))
                if (now - last_probe).total_seconds() >= PROBE_EVERY_H * 3600:
                    models.append((m, True))    # probe early, logged below
                continue
            if parked_until:
                log(f"{m}: documented reset passed ({parked_until:%m-%d %H:%M %Z}); trying again")
                state.pop(m, None)
                save()
            models.append((m, False))
        if not models:
            log("all models parked until the Pacific-midnight reset; exiting")
            return

        by_ch = collections.OrderedDict()
        for v in verses:
            by_ch.setdefault((v.book, v.chapter), []).append(v)
        if a.refs:      # e.g. a review sample: only these chapters
            wanted = {tuple(r.rsplit(" ", 1)) for r in a.refs}
            by_ch = collections.OrderedDict(
                (k, vs) for k, vs in by_ch.items() if (k[0], str(k[1])) in wanted)
        rank = {b: i for i, b in enumerate(BOOK_PRIORITY)}
        done = load_done(a.out)
        latest = load_latest_models(a.out)
        order_key = lambda k: (rank.get(k[0], 99), k[1])
        todo = sorted((k for k, vs in by_ch.items()
                       if any((v.book, v.chapter, v.verse) not in done for v in vs)),
                      key=order_key)
        # Upgrade pass: chapters tagged (in any verse) by a Lite model, re-tagged
        # by full Flash only. Runs after the untagged work, never before it.
        upgrade = sorted((k for k, vs in by_ch.items() if k not in set(todo)
                          and any(latest.get((v.book, v.chapter, v.verse)) in LITE_MODELS
                                  for v in vs)), key=order_key)
        log(f"start: {len(todo)} chapters untagged, {len(upgrade)} to upgrade from "
            f"Lite, {len(done)} verses done; models {[m for m, _ in models]}")
        work = [(k, False) for k in todo] + [(k, True) for k in upgrade]
        if a.limit:
            work = work[:a.limit]

        n_ok = 0
        busy_streak = 0     # consecutive "overloaded" failures on one model
        with open(a.out, "a", encoding="utf-8") as out:
            for (book, ch), is_upgrade in work:
                res = used = None
                while True:
                    idx = next((i for i, (m, _) in enumerate(models)
                                if not (is_upgrade and m in LITE_MODELS)), None)
                    if idx is None:
                        break
                    m, probing = models[idx]
                    try:
                        res = ask_gemini(m, chapter_prompt(book, ch, by_ch[(book, ch)],
                                                           english), key)
                    except QuotaExhausted:
                        if probing:
                            log(f"{m}: early probe — still exhausted")
                            state[m]["probed"] = datetime.datetime.now(PT).isoformat()
                        else:
                            t = datetime.datetime.now(PT)
                            state[m] = {"parked": t.isoformat(),
                                        "reset": next_reset(t).isoformat()}
                            log(f"{m}: DAILY quota hit after {n_ok} chapters this run; "
                                f"parked until {next_reset(t):%m-%d %H:%M %Z}")
                        save()
                        models.pop(idx)
                        continue
                    except Unavailable as e:
                        log(f"  ! {book} {ch}: {e} (retried next run)")
                        busy_streak += 1
                        if busy_streak >= 5:
                            # Overloaded, not out of quota: skip it for THIS run
                            # only, so it is not hammered once per chapter.
                            log(f"{m}: 5 failures in a row; skipped for this run")
                            models.pop(idx)
                            busy_streak = 0
                        res = None
                        break
                    if probing:
                        log(f"{m}: early probe SUCCEEDED — quota back before the "
                            f"documented reset (parked {state[m]['parked']})")
                        state.pop(m, None)
                        save()
                        models[idx] = (m, False)
                    used = m
                    break
                if res is None:
                    if idx is None:
                        if not models:
                            log(f"all models exhausted; stopping. {n_ok} chapters this run")
                            return
                        if is_upgrade:
                            log(f"no full-Flash model left for upgrades; stopping. "
                                f"{n_ok} chapters this run")
                            return
                    continue
                busy_streak = 0
                _pace_after(True)
                vs = by_ch[(book, ch)]
                missing = [v.verse for v in vs if v.verse not in res]
                for v in vs:
                    # An upgrade appends a newer line for every verse; later
                    # lines win on load.
                    if v.verse in res and (is_upgrade
                                           or (v.book, v.chapter, v.verse) not in done):
                        out.write(json.dumps(tag_record(v.book, v.chapter, v.verse,
                                                        res[v.verse], used),
                                             ensure_ascii=False) + "\n")
                out.flush()
                n_ok += 1
                if missing:
                    log(f"  {book} {ch}: {len(missing)} verses unanswered (retried next run)")
                time.sleep(PACE["gap"])
        log(f"finished the list: {n_ok} chapters this run")
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def print_stats(path: str) -> None:
    """Summarise verse_tags_requests.csv: per model, per Pacific day, per hour."""
    if not os.path.exists(path):
        print("no request log yet")
        return
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    by_model = collections.defaultdict(collections.Counter)
    ok_per_day = collections.Counter()
    by_hour = collections.defaultdict(collections.Counter)
    secs = collections.defaultdict(list)
    for r in rows:
        by_model[r["model"]][r["outcome"]] += 1
        day, hour = r["time_pt"][:10], r["time_pt"][11:13]
        by_hour[hour]["ok" if r["outcome"] == "ok" else "fail"] += 1
        if r["outcome"] == "ok":
            ok_per_day[(r["model"], day)] += 1
            secs[r["model"]].append(float(r["seconds"]))
    print(f"{len(rows)} requests logged\n\nPer model (all attempts):")
    for m, c in by_model.items():
        n = sum(c.values())
        lat = sorted(secs[m])
        med = f"{lat[len(lat) // 2]:.0f}s" if lat else "-"
        print(f"  {m:24} {n:5} tries  ok {100 * c['ok'] / n:4.0f}%  "
              f"median {med:>5}  " + "  ".join(f"{k}={v}" for k, v in c.items()
                                               if k != "ok"))
    print("\nChapters answered per model per Pacific day "
          "(a day that ends in quota_day = that model's daily allowance):")
    for (m, d), n in sorted(ok_per_day.items()):
        print(f"  {d}  {m:24} {n}")
    print("\nSuccess rate by Pacific hour:")
    for h in sorted(by_hour):
        c = by_hour[h]
        print(f"  {h}:00  {c['ok']:4} ok / {c['ok'] + c['fail']:4}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gemini", "ollama"], default="gemini")
    ap.add_argument("--model", help="Ollama model name (ollama backend)")
    ap.add_argument("--out", default=str(app.VERSE_TAGS_FILE))
    ap.add_argument("--refs", nargs="*",
                    help='limit to chapters, e.g. "Leviticus 14"')
    ap.add_argument("--limit", type=int, default=0,
                    help="gemini: at most this many chapters this run")
    ap.add_argument("--stats", action="store_true",
                    help="summarise the Gemini request log and exit")
    a = ap.parse_args()
    if a.stats:
        print_stats(os.path.splitext(a.out)[0] + "_requests.csv")
        return
    verses = app.load_from_jsonl()
    english = app.load_english()
    if a.backend == "ollama":
        if not a.model:
            ap.error("--model is required for the ollama backend")
        run_ollama(a, verses, english)
    else:
        run_gemini(a, verses, english)


if __name__ == "__main__":
    main()
