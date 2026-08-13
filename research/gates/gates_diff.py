import sys
sys.stdout.reconfigure(encoding='utf-8')
SP = r"C:\Users\JOSHU~1.AKI\AppData\Local\Temp\claude\c--\305b2a45-dcc0-462e-a174-ec8b594679d6\scratchpad"
txt = open(SP + r"\pr30_5.txt", encoding="utf-8").read()

AB = "אבגדהוזחטיכלמנסעפצקרשת"
FIN = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
norm = lambda c: FIN.get(c, c)

start = txt.index("אל בת גש"); end = txt.index('ע"כ נשלמו')
rows = [r.strip() for r in txt[start:end].split(":") if r.strip()]
printed = []
for r in rows:
    pr = [p for p in r.split() if len(p) == 2]
    if len(pr) == 11:
        printed.append((r, [tuple(norm(c) for c in p) for p in pr]))


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


S = lambda ps: {frozenset(p) for p in ps}

print("Mismatched rows — printed vs generated:\n")
for n, (raw, row) in enumerate(printed, 1):
    g, p = S(gen(n)), S(row)
    if g == p:
        continue
    missing = g - p          # rule says present, print lacks
    extra = p - g            # print has, rule says no
    print("row %2d  (%d/11)" % (n, len(g & p)))
    print("   printed : %s" % raw)
    print("   in print not in rule : %s" % ", ".join("".join(sorted(x)) for x in sorted(extra, key=lambda s: sorted(s))))
    print("   in rule not in print : %s" % ", ".join("".join(sorted(x)) for x in sorted(missing, key=lambda s: sorted(s))))
    # Does the printed row contain a repeated letter? (a tell for typesetting error)
    letters = [c for pr in row for c in pr]
    dupes = sorted({c for c in letters if letters.count(c) > 1})
    missingletters = [c for c in AB if c not in letters]
    if dupes or missingletters:
        print("   ⚠ repeated letters: %s   absent letters: %s" % ("".join(dupes) or "none", "".join(missingletters) or "none"))
    print()
