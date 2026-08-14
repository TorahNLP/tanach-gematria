import sys
sys.stdout.reconfigure(encoding='utf-8')

AB = "אבגדהוזחטיכלמנסעפצקרשת"


def gen(k):
    t = (k - 1) % 22
    seen, out, fixed = set(), [], []
    for i, c in enumerate(AB):
        if i in seen: continue
        j = (t - i) % 22
        if j == i:
            fixed.append(i); continue
        seen.add(i); seen.add(j)
        out.append((AB[i], AB[j]))
    if len(fixed) == 2:
        out.append((AB[fixed[0]], AB[fixed[1]]))
    return out


print("STRUCTURAL VALIDATION OF THE RECONSTRUCTED 231 GATES\n")

ok = True
allpairs = set()
for k in range(1, 23):
    g = gen(k)
    letters = [c for p in g for c in p]
    # 1. exactly 11 pairs
    c1 = len(g) == 11
    # 2. all 22 letters, each exactly once
    c2 = sorted(letters) == sorted(AB)
    # 3. involution: pairing is symmetric by construction
    m = {}
    for a, b in g:
        m[a] = b; m[b] = a
    c3 = all(m[m[c]] == c for c in AB)
    if not (c1 and c2 and c3):
        ok = False
        print("  gate %2d FAILED  pairs=%s all22=%s involution=%s" % (k, c1, c2, c3))
    allpairs |= {frozenset(p) for p in g}

print("all 22 gates: 11 pairs, 22 distinct letters, perfect involution ->", "PASS" if ok else "FAIL")
print()
print("distinct pairs across the family : %d" % len(allpairs))
print("C(22,2)                          : %d" % (22 * 21 // 2))
print("covers every possible pair       :", len(allpairs) == 231)
# 22 gates x 11 pairs = 242 slots for 231 distinct pairs. The 11 extra are the
# fixed-point pairs: in each gate the two letters left without a partner take
# each other, and those 11 pairings recur elsewhere in the family. The Remak's
# רל"א counts DISTINCT pairs, which is the 231 above.
_slots = sum(len(gen(k)) for k in range(1, 23))
print("pair slots (242 = 231 + 11 reprised fixed-point pairs) : %d" % _slots)
print()

# Which classical alphabets does the family contain?
def as_set(ps): return {frozenset(p) for p in ps}

ATBASH = [(AB[i], AB[21 - i]) for i in range(11)]
ALBAM = [(AB[i], AB[i + 11]) for i in range(11)]

for name, table in (("Atbash", ATBASH), ("Albam", ALBAM)):
    hit = [k for k in range(1, 23) if as_set(gen(k)) == as_set(table)]
    print("%-8s == gate %s" % (name, hit if hit else "(not in family)"))

print()
print("Gate 1 (sum=0):  ", " ".join(a + b for a, b in gen(1)))
print("Gate 22 (sum=21):", " ".join(a + b for a, b in gen(22)))
