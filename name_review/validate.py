"""Validate agent-supplied nikud before it reaches the name index.

⚠️ KNOWN LIMITS — this is a screen, not a proof. Tested on 15 hand-picked
cases, 13 correct. The two it gets wrong are both about matres:
  * חַיה passes when it should fail (the medial yod is exempted as a mater
    when it is really a consonant needing its own vowel — חַיָּה).
  * זֶעלְדָּא fails when it should pass (its final alef IS a mater, but the
    ayin before it carries no vowel, which is correct for a Yiddish name).
Treat a clean run as "nothing obviously broken", not "all correct". Human
review still decides.


Catches the failure modes that actually matter here:
  * partial pointing — the Harkavy problem, where a word carries SOME nikud so
    has_unpointed_word() passes it, but most letters have no vowel. Checked
    per letter, not per word.
  * consonants silently changed — a "vocalization" that is a different word.
  * marks the engine ignores (shin/sin dot alone, meteg) mistaken for vowels.
  * forms that score 0 under the vowel-mark ciphers, i.e. no usable value.

Run after any agent or human pass. Exit code is nonzero if anything fails.
"""
import csv
import importlib.util
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(BASE), 'app.py')
spec = importlib.util.spec_from_file_location('app', APP)
m = importlib.util.module_from_spec(spec)
sys.modules['app'] = m
spec.loader.exec_module(m)

NIKUD = lambda c: 'ְ' <= c <= 'ּ'
LETTER = lambda c: 'א' <= c <= 'ת'
# Matres lectionis and word-final letters legitimately carry no vowel of their
# own: the vav in דָוִד, the yod in מֵאִיר, the final ד/ר themselves. Counting
# them as "missing a vowel" marks correct forms as partial — an earlier version
# of this file did exactly that and failed דָוִד and מֵאִיר while PASSING the
# Harkavy-style חַיה it was written to catch.
MATRES = set('אהוי')


def coverage(word):
    """Fraction of letters that SHOULD carry a vowel and do.

    A letter is exempt when it is a mater lectionis, or word-final (Hebrew
    words do not point their last consonant except with a final kamatz/patach,
    which is rare enough to ignore here).
    """
    letters = [(i, c) for i, c in enumerate(word) if LETTER(c)]
    need = have = 0
    for pos, (i, ch) in enumerate(letters):
        nxt = word[i + 1] if i + 1 < len(word) else ''
        vowelled = NIKUD(nxt)
        is_last = pos == len(letters) - 1
        if vowelled:
            have += 1
            need += 1
            continue
        # A mater is exempt only in the middle of a word, or as the final
        # letter. A MEDIAL yod/vav with no vowel before a final he — חַיה — is
        # the Harkavy failure this check exists to catch: the yod there is a
        # consonant needing its own vowel (חַיָּה), not a mater.
        prev_vowelled = False
        if pos > 0:
            pi = letters[pos - 1][0]
            pn = word[pi + 1] if pi + 1 < len(word) else ''
            prev_vowelled = NIKUD(pn)
        if is_last:
            continue                      # final letters are not pointed
        if ch in MATRES and prev_vowelled:
            continue                      # genuine mater after a vowel
        need += 1
    return (have / need) if need else 1.0


def check(bare, pointed):
    """Return a list of problems with `pointed` as a vocalization of `bare`."""
    problems = []
    if not pointed.strip():
        return ['empty']
    if m.strip_to_consonants(pointed) != m.strip_to_consonants(bare):
        problems.append(
            f'consonants changed: {m.strip_to_consonants(pointed)} != {bare}')
    if not any(NIKUD(c) for c in pointed):
        problems.append('no nikud at all')
    cov = coverage(pointed)
    if cov < 0.9:
        problems.append(f'partial pointing ({cov:.0%} of letters)')
    if m.g_hanekudot(pointed) == 0:
        problems.append('HaNekudot scores 0 — no usable vowel value')
    return problems


def run(path, name_col, pointed_col):
    if not os.path.exists(path):
        print(f'  {os.path.basename(path)}: not present, skipped')
        return 0
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    bad = 0
    checked = 0
    for r in rows:
        p = (r.get(pointed_col) or '').strip()
        if not p:
            continue
        checked += 1
        probs = check(r[name_col], p)
        if probs:
            bad += 1
            print(f'  !! {r[name_col]:14s} {p:18s} — {"; ".join(probs)}')
    print(f'  {os.path.basename(path)}: {checked} vocalized, {bad} with problems')
    return bad


if __name__ == '__main__':
    total = 0
    print('validating agent output...')
    total += run(os.path.join(BASE, '2_needs_nikud_TOP200_agent.csv'),
                 'name', 'VOCALIZED')
    total += run(os.path.join(BASE, '3_ambiguous_check_agent.csv'),
                 'name', 'OVERRIDE')
    print()
    print('FAIL' if total else 'all supplied vocalizations pass')
    sys.exit(1 if total else 0)
