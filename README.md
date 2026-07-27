---
title: Tanach Gematria Engine
emoji: 📜
colorFrom: indigo
colorTo: gray
sdk: docker
pinned: false
---

Multi-method Hebrew gematria search engine, structural pattern database, and
statistical dashboard over the Tanach.

**12 gematria methods:** Absolute, Katan, Gadol, Atbash, Albam, Atbah, Avgad, Siduri,
Ribua, Kidmi, Achbi, HaNikud (vowel-point dot count).

**Full corpus:** 23,206 cantillated Masoretic verses, bundled.

**English translation:** optional per-verse translation in the verse-detail panel
and print-out (off by default). Display only — it takes no part in any gematria
calculation, and is always shown for the whole verse rather than sliced to match
a sub-verse unit.

## Texts & licences

| Text | Edition | Licence |
|---|---|---|
| Hebrew (all calculations) | *Tanach with Ta'amei Hamikra*, from [tanach.us](http://www.tanach.us/Tanach.xml) via [Sefaria](https://www.sefaria.org) | Public Domain |
| English (display only) | *Tanakh: The Holy Scriptures*, © 1985 The Jewish Publication Society, via Sefaria | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |

The translation is CC-BY-NC: attribution is a condition of the licence, and the
app renders it wherever the English appears, including in exported documents.
Re-licensing this project commercially would require swapping the translation
for the public-domain JPS 1917 (one-line change in `fetch_english.py`, then
refetch). Joshua 21:36–37 are present in the Hebrew but absent from the 1985
translation, which follows manuscripts that omit them.

**Features:** Atnach-based half-verse splitting · Ksiv/Kri + Masoretic textual
variant forking (Itture Sopherim, Esther doublets) · Colel (±1) search ·
pattern detection (internal balance, proximity echoes, macro-micro resonances) ·
correlation & fingerprint heatmaps · letter-by-letter breakdown visualization ·
Guide & Sources tab with method sources and variant docs.

See `BUILD.md` for architecture and build reference.

This application is licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/); see
**Texts & licences** above for the licences of the bundled texts.

## Developer documentation

Docs live on the **`docs` branch**, not here — HuggingFace rebuilds and restarts
the Space on any push to `main`, so doc-only edits used to cost a few minutes of
downtime. `HANDOFF.md` (read first), `BUILD.md` and `CLAUDE_CODE_TASKS.md` are
there. Check them out beside the code with:

```bash
git worktree add ../tanakh-docs docs
```
