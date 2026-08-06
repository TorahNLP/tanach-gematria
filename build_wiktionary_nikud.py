"""Extract vocalized headwords from the Hebrew Wiktionary dump.

The lowest-priority nikud source: used only where the Tanach corpus and the
curated name lists have nothing, and as extra variants in the picker.

Why a dump and not an AI: Wiktionary is human-edited lexicography, so there is
no fabrication risk, and — critically — the article TITLE is the bare headword,
so the pointed/bare pairing can be VERIFIED rather than trusted. The bulk-AI
pass had to discard 139 of 986 rows (14%) for silently changing the consonants;
that failure mode cannot occur here because we check.

⚠️ Verify against the TITLE, never against `כתיב מלא`. That field is the *plene*
spelling and is meant to DIFFER from a defectively-spelled pointed headword —
`גֹּלֶם` has `כתיב מלא=גולם`. Comparing the two rejected 4,993 sound entries
(29%) on a difference that is the field working correctly.

Run:  python build_wiktionary_nikud.py
Writes wiktionary_nikud.json beside app.py. Downloads ~14 MB once and caches
the dump in the system temp dir.

Source: he.wiktionary.org, CC-BY-SA 4.0.
"""
import bz2
import collections
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP_URL = ('https://dumps.wikimedia.org/hewiktionary/latest/'
            'hewiktionary-latest-pages-articles.xml.bz2')
CACHE = os.path.join(tempfile.gettempdir(), 'hewiktionary-latest.xml.bz2')

spec = importlib.util.spec_from_file_location('app', os.path.join(HERE, 'app.py'))
app = importlib.util.module_from_spec(spec)
sys.modules['app'] = app
spec.loader.exec_module(app)

NIKUD = lambda c: 'ְ' <= c <= 'ּ'
HEB = lambda s: any('א' <= c <= 'ת' for c in s)

# The vocalized headword is the level-2 heading; the bare form is declared in
# the grammar template. Templates and qualifiers ({{משני|א}}) trail the heading
# and must come off.
RE_TITLE = re.compile(r'<title>([^<]+)</title>')
RE_TEXT = re.compile(r'<text[^>]*>(.*?)</text>', re.S)
RE_HEAD = re.compile(r'^==\s*([^=\n]+?)\s*==\s*$', re.M)
# A heading may offer a second reading: "עֵין־הָרָע (גם: עַיִן רָעָה)". Split
# them apart so both are candidates rather than one malformed blob.
RE_ALSO = re.compile(r'\((?:גם|ובנקבה|ברבים)\s*:\s*([^)]*)\)')


def is_pointed(form):
    """True when EVERY consonant that needs a vowel has one.

    ⚠️ Partly-pointed forms must not pass. `טקסוֹן` carries one holam and is
    otherwise bare; the four vowel-mark methods are undefined for such text —
    that is what nikud_partial suppresses throughout the app — so offering it
    as a vocalization would produce a knowably short total presented as real.

    Three kinds of bare letter are correct Hebrew, not gaps:
      - the last letter of a word takes no vowel      (דָּוִד, חַנְקָן)
      - a mater lectionis — bare א/ו/ה/י right after a pointed letter — is
        itself part of that vowel and takes none (the yod of אֱלֹהִים, which
        is NOT word-final, and the alef of יָאִיר)
      - a letter whose vowel is written on a following mater, which leaves the
        letter reading bare (שָׁלוֹם: the holam sits on the vav, so the lamed
        carries no mark of its own)
    Missing any of the three rejects ordinary words: they cost 79% of the
    corpus on the first attempt.
    """
    words = [w for w in form.split() if any(HEB(c) for c in w)]
    if not words:
        return False
    for word in words:
        chars = list(word)
        idx = [i for i, c in enumerate(chars) if HEB(c)]
        if not idx:
            return False
        for pos, i in enumerate(idx):
            marks = ''.join(chars[i + 1:idx[pos + 1] if pos + 1 < len(idx)
                                   else len(chars)])
            if any(NIKUD(c) for c in marks):
                continue
            if pos == len(idx) - 1:            # word-final: no vowel expected
                continue
            if chars[i] in 'אוהי' and pos > 0:  # this letter IS a mater
                continue
            if chars[idx[pos + 1]] in 'אוהי':   # a mater carries its vowel
                continue
            return False
    return True


def fetch():
    if os.path.exists(CACHE) and os.path.getsize(CACHE) > 1_000_000:
        print(f'using cached dump ({os.path.getsize(CACHE)/1e6:.1f} MB)')
        return CACHE
    print('downloading dump…')
    req = urllib.request.Request(DUMP_URL, headers={'User-Agent':
                                                    'tanakh-gematria/1.0'})
    with urllib.request.urlopen(req, timeout=600) as r, open(CACHE, 'wb') as fh:
        fh.write(r.read())
    print(f'  {os.path.getsize(CACHE)/1e6:.1f} MB')
    return CACHE


def clean_headword(h):
    """Strip templates, links, qualifiers and punctuation from a heading."""
    h = re.sub(r'\{\{[^}]*\}\}', '', h)
    h = re.sub(r'\[\[|\]\]', '', h)
    h = re.sub(r'\([^)]*\)', '', h)
    h = h.replace('־', ' ')
    keep = [c for c in h if HEB(c) or NIKUD(c) or c in ' ׁׂ']
    return re.sub(r'\s+', ' ', ''.join(keep)).strip()


def main():
    path = fetch()
    found = {}
    stats = collections.Counter()
    page_title, buf, in_page = None, [], False

    with bz2.open(path, 'rt', encoding='utf-8') as fh:
        chunk = []
        for line in fh:
            if '<page>' in line:
                in_page, chunk = True, []
            if in_page:
                chunk.append(line)
            if '</page>' in line and in_page:
                in_page = False
                blob = ''.join(chunk)
                mt = RE_TITLE.search(blob)
                mx = RE_TEXT.search(blob)
                if not mt or not mx:
                    continue
                title = mt.group(1).strip()
                if ':' in title:            # namespaces: קטגוריה:, תבנית: …
                    continue
                stats['pages'] += 1
                body = mx.group(1)

                # The TITLE is the bare headword and becomes the key — it keeps
                # its spaces, so multi-word entries stay reachable. Keying off
                # the stripped pointed form instead collapsed "מפתח אנגלי" to
                # "מפתחאנגלי", which no lookup would ever hit.
                key = re.sub(r'\s+', ' ', title.replace('־', ' ')).strip()
                if not HEB(key) or any(NIKUD(c) for c in key):
                    continue
                key_cons = app.strip_to_consonants(key)
                if not key_cons:
                    continue

                for head in RE_HEAD.findall(body):
                    for variant in [RE_ALSO.sub('', head)] + RE_ALSO.findall(head):
                        pointed = clean_headword(variant)
                        if not pointed or not any(NIKUD(c) for c in pointed):
                            continue
                        stats['pointed_headwords'] += 1

                        # ⚠️ VERIFY against the title. A heading that strips to
                        # different consonants belongs to another lemma (or is
                        # an error) and must not be pinned to this word.
                        if app.strip_to_consonants(pointed) != key_cons:
                            stats['rejected_consonant_drift'] += 1
                            continue
                        if not is_pointed(pointed):
                            stats['rejected_partly_pointed'] += 1
                            continue
                        if app.g_hanekudot(pointed) == 0:
                            stats['rejected_zero_value'] += 1
                            continue
                        found.setdefault(key, [])
                        if pointed not in found[key]:
                            found[key].append(pointed)
                            stats['accepted'] += 1

    print()
    for k in sorted(stats):
        print(f'   {k:26s} {stats[k]:,}')
    print(f'   distinct bare forms        {len(found):,}')

    out = os.path.join(HERE, 'wiktionary_nikud.json')
    json.dump(found, open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, sort_keys=True)
    print(f'\nwrote {out} ({os.path.getsize(out)/1e6:.1f} MB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
