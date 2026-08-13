# Recover and validate the generative rule behind the 231 gates
# (Sefer Yetzirah 2:4, tabulated at Pardes Rimonim 30:5).
import re, sys, io

sys.stdout.reconfigure(encoding='utf-8')

SP = r"C:\Users\JOSHU~1.AKI\AppData\Local\Temp\claude\c--\305b2a45-dcc0-462e-a174-ec8b594679d6\scratchpad"
txt = open(SP + r"\pr30_5.txt", encoding="utf-8").read()

ALEPH_BET = "אבגדהוזחטיכלמנסעפצקרשת"          # 22 letters, no finals
FINALS = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
IDX = {c: i for i, c in enumerate(ALEPH_BET)}


def norm(c):
    return FINALS.get(c, c)


# The printed tables sit between the intro and the closing "ע"כ נשלמו".
start = txt.index("אל בת גש")
end = txt.index('ע"כ נשלמו')
block = txt[start:end]

# Each alphabet is a colon-separated run of 11 two-letter pairs.
rows = [r.strip() for r in block.split(":") if r.strip()]
printed = []
for r in rows:
    pairs = [p for p in r.split() if len(p) == 2]
    if len(pairs) == 11:
        printed.append([tuple(norm(c) for c in p) for p in pairs])

print("printed alphabets parsed: %d (expect 22)" % len(printed))
print()


def generated(k):
    """Gate k: pair letters whose indices sum to k-1 (mod 22).

    Fixed points (i where 2i == k-1 mod 22) have no partner under the sum
    rule; there are exactly two of them, and they take each other.
    """
    target = (k - 1) % 22
    seen, pairs, fixed = set(), [], []
    for i, c in enumerate(ALEPH_BET):
        if i in seen:
            continue
        j = (target - i) % 22
        if j == i:
            fixed.append(i)
            continue
        seen.add(i); seen.add(j)
        pairs.append((ALEPH_BET[i], ALEPH_BET[j]))
    if len(fixed) == 2:
        pairs.append((ALEPH_BET[fixed[0]], ALEPH_BET[fixed[1]]))
    return pairs


def as_set(pairs):
    return {frozenset(p) for p in pairs}


exact = 0
pair_hits = pair_total = 0
per_row = []
for n, row in enumerate(printed, start=1):
    best = None
    for k in range(1, 23):
        g = as_set(generated(k))
        p = as_set(row)
        overlap = len(g & p)
        if best is None or overlap > best[1]:
            best = (k, overlap)
    k, overlap = best
    per_row.append((n, k, overlap))
    pair_hits += overlap
    pair_total += len(as_set(row))
    if overlap == 11:
        exact += 1

print("row  best-gate  pairs matched /11")
for n, k, o in per_row:
    flag = "  EXACT" if o == 11 else ""
    print("  %2d      %2d        %2d%s" % (n, k, o, flag))

print()
print("exact reproductions : %d / %d" % (exact, len(printed)))
print("pair-level accuracy : %d / %d = %.1f%%" % (pair_hits, pair_total, 100.0 * pair_hits / pair_total))

# The decisive structural check: the full generated family must be exactly
# C(22,2) = 231 distinct unordered pairs, matching the Remak's own count.
allpairs = set()
for k in range(1, 23):
    allpairs |= as_set(generated(k))
print()
print("distinct pairs across all 22 generated gates: %d" % len(allpairs))
print("C(22,2) = %d" % (22 * 21 // 2))
print("MATCH" if len(allpairs) == 231 else "MISMATCH")
