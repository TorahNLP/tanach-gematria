import sys, importlib.util
sys.stdout.reconfigure(encoding='utf-8')

spec = importlib.util.spec_from_file_location(
    'app', r'C:\Users\joshu.AKIVA\Desktop\tanakh-gematria\app.py')
m = importlib.util.module_from_spec(spec)
sys.modules['app'] = m
spec.loader.exec_module(m)

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


def as_map(pairs):
    d = {}
    for a, b in pairs:
        d[a] = b; d[b] = a
    return d


gates = {k: as_map(gen(k)) for k in range(1, 23)}

# Existing substitution maps in the app.
candidates = {}
for name in dir(m):
    if name.endswith('_MAP'):
        v = getattr(m, name)
        if isinstance(v, dict) and v:
            candidates[name] = v

print("Existing *_MAP objects in app.py:")
for n in sorted(candidates):
    print("   %-26s %d entries" % (n, len(candidates[n])))
print()

FIN = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}
def base(d):
    """Restrict a map to the 22 base letters, folding finals."""
    out = {}
    for k, v in d.items():
        kk = FIN.get(k, k); vv = FIN.get(v, v)
        if kk in AB and vv in AB:
            out.setdefault(kk, vv)
    return out


print("Which existing ciphers coincide with a gate?")
for n in sorted(candidates):
    b = base(candidates[n])
    if len(b) != 22:
        continue
    for k, g in gates.items():
        if b == g:
            print("   %-26s == gate %d" % (n, k))
print()

# How distinct are the gates from each other, as functions on real text?
print("Sanity: gate maps are all distinct from one another:",
      len({tuple(sorted(g.items())) for g in gates.values()}) == 22)
print()
print("Gate table (k : pairs)")
for k in range(1, 23):
    print("  %2d  %s" % (k, " ".join(a + b for a, b in gen(k))))
