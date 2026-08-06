"""Build nikud_names.json — the lookup the nikud tool reads.

Merges four sources, in precedence order:
  1. curated_names.py        hand-approved forms for names Tanach cannot supply
  2. accepted plene rows     modern spellings whose Tanach original was verified
  3. accepted auto-picks     Tanach names with one attested vocalization
  4. reviewed ambiguities    Tanach names with several, overrides applied

Run from the repo root:  python build_name_index.py
Writes nikud_names.json beside app.py. Committed, so the app never rebuilds it
at runtime.
"""
import collections
import csv
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW = os.path.join(HERE, 'name_review')

spec = importlib.util.spec_from_file_location('app', os.path.join(HERE, 'app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules['app'] = app
spec.loader.exec_module(app)

# ⚠️ Explicit character set, NOT a range. [֑-ֽ] would swallow the
# nikud (U+05B0-U+05BC) and silently yield bare consonants.
TAAM = re.compile('[' + ''.join(chr(c) for c in list(range(0x0591, 0x05B0))
                                + [0x05BD, 0x05BF, 0x05C0, 0x05C3, 0x05C4,
                                   0x05C5, 0x05C6]) + ']')


def corpus_forms():
    """{bare consonants: {pointed form: count}} from the JSONL.

    ⚠️ NOT from tanach.db — that stores no pointed text; text_display is bare
    consonants and the nikud lives only in the corpus file.
    """
    forms = collections.defaultdict(collections.Counter)
    path = os.path.join(HERE, 'tanach_corpus.jsonl')
    for line in open(path, encoding='utf-8'):
        d = json.loads(line)
        text = re.sub(r'\[[^\]]*\]', '', d['text']).replace('־', ' ')
        for tok in TAAM.sub('', text).split():
            tok = tok.strip('׃ ')
            cons = app.strip_to_consonants(tok)
            if cons and any('ְ' <= c <= 'ּ' for c in tok):
                forms[cons][tok] += 1
    return forms


def collapse(counter):
    """Group spellings by the VALUES they produce.

    Dagesh-only variants are numerically identical now that the dagesh scores
    0, so דָּוִד and דָוִד are ONE option with two spellings, not two choices.
    Presenting them separately would manufacture a decision that does not
    exist. Returns [{form, count, variants}], commonest first.
    """
    groups = collections.defaultdict(lambda: [0, []])
    for form, n in counter.items():
        key = (app.g_hanekudot(form), app.g_milui_nekudot(form))
        groups[key][0] += n
        groups[key][1].append((form, n))
    out = []
    for (_hn, _ml), (total, spellings) in groups.items():
        spellings.sort(key=lambda x: -x[1])
        out.append({"form": spellings[0][0], "count": total,
                    "variants": [f for f, _ in spellings]})
    out.sort(key=lambda o: -o["count"])
    return out


def load_csv(name):
    path = os.path.join(REVIEW, name)
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding='utf-8-sig')))


def main():
    forms = corpus_forms()
    index = {}
    stats = collections.Counter()

    # --- 4. every corpus word that is also a known name -------------------
    for row in load_csv('3_ambiguous_check_agent.csv'):
        name = row['name']
        if name not in forms:
            continue
        opts = collapse(forms[name])
        override = (row.get('OVERRIDE') or '').strip()
        if override and not override.startswith('UNCERTAIN'):
            # Move the reviewed form to the front; it stays in the list so the
            # picker still shows what else is attested.
            opts.sort(key=lambda o: 0 if o["form"] == override else 1)
            stats['ambiguous_override'] += 1
        else:
            stats['ambiguous_auto'] += 1
        index[name] = {"options": opts, "source": "tanach"}

    clean = json.load(open(os.path.join(REVIEW, '0_clean_tanach_names.json'),
                           encoding='utf-8'))
    for name in clean:
        if name in index or name not in forms:
            continue
        index[name] = {"options": collapse(forms[name]), "source": "tanach"}
        stats['tanach_clean'] += 1

    # --- 2. plene: modern spelling, Tanach original ------------------------
    for row in load_csv('1_plene_candidates_agent.csv'):
        if row['KEEP?'].strip().lower() != 'yes':
            continue
        name, tanach = row['name'], row['tanach_spelling']
        if tanach not in forms:
            continue
        opts = collapse(forms[tanach])
        suggested = (row.get('suggested') or '').strip()
        if suggested:
            opts.sort(key=lambda o: 0 if o["form"] == suggested else 1)
        index[name] = {"options": opts, "source": "tanach-plene",
                       "tanach_spelling": tanach}
        stats['plene'] += 1

    # --- 1. curated wins outright -----------------------------------------
    # Order matters: later batches overwrite earlier ones for the same name.
    # curated_names.py is Joshua-reviewed and must win, so it goes LAST.
    for module, attr, tag in (('curated_names_3.py', 'CURATED_3', 'curated3'),
                              ('curated_names_2.py', 'CURATED_2', 'curated2'),
                              ('curated_names.py', 'CURATED', 'curated')):
        path = os.path.join(REVIEW, module)
        if not os.path.exists(path):
            continue
        s = importlib.util.spec_from_file_location(tag, path)
        mod = importlib.util.module_from_spec(s)
        s.loader.exec_module(mod)
        for name, supplied in getattr(mod, attr).items():
            index[name] = {
                "options": [{"form": f, "count": 0, "variants": [f]}
                            for f in supplied],
                "source": tag}
            stats[tag] += 1

    # --- validate before writing ------------------------------------------
    problems = 0
    for name, entry in index.items():
        # Compare consonants on BOTH sides. A two-word key like "בת שבע" keeps
        # its space, which strip_to_consonants drops from the form — comparing
        # the stripped form against the raw key flags those wrongly.
        want = app.strip_to_consonants(name)
        for opt in entry["options"]:
            if app.strip_to_consonants(opt["form"]) != want and \
                    entry.get("source") != "tanach-plene":
                print(f"  !! {name}: {opt['form']} has different consonants")
                problems += 1
    if problems:
        print(f"ABORTING — {problems} entries have mismatched consonants")
        return 1

    out = os.path.join(HERE, 'nikud_names.json')
    json.dump(index, open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, sort_keys=True)
    print(f"nikud_names.json: {len(index):,} names")
    for k in sorted(stats):
        print(f"   {k:20s} {stats[k]:,}")
    multi = sum(1 for e in index.values() if len(e["options"]) > 1)
    print(f"   with a real choice   {multi:,}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
