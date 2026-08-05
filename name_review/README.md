# Name-index review lists

Generated 2026-08-05. Fill in the marked column, save as CSV (UTF-8), and the
index build will pick them up.

## 0_clean_tanach_names.json  — 430 names, NOTHING TO DO
Unambiguous Tanach names: one vocalization, no choice to make. Included so you
can see what is already covered.

## 1_plene_candidates.csv  — 332 rows, ACCEPT OR REJECT
Modern spellings that miss Tanach only because of an extra vav/yod. Dropping
one mater finds a corpus word — sometimes the right name, sometimes nonsense.

    KEEP?  ->  yes / no   (blank = not yet reviewed)

Rows already marked "LIKELY NO" are ones where the recovered word is a common
function word, e.g.:
    שירה -> שָׂרָה   (Shira is NOT Sarah)
    מאיה -> מֵאָה    ("hundred")
    ליה  -> לָהּ     ("to her")
    ליאור -> לְאוֹר  ("to light")
Check them anyway — the flag is a guess.

Correct ones look like:
    אהרון  -> אַהֲרֹן
    צפורה  -> צִפֹּרָה
    שולמית -> שְׁלֹמִית

## 2_needs_nikud_TOP200.csv  — 200 rows, SUPPLY THE NIKUD
Names with no Tanach form at all. Top 200 by bearer count = 84% of that list.
The remaining 903 are in 2_needs_nikud.csv if you ever want them.

    VOCALIZED -> the pointed form, e.g. זֶעלְדָא

## 3_ambiguous_check.csv  — 299 rows, SPOT-CHECK ONLY
Tanach names with more than one attested vocalization. The auto-pick is the
most frequent, which is usually right but sometimes lands on a CONSTRUCT form:
    עדי -> auto-picks עֲדֵי ("ornaments-of") but the name is עֲדִי
    הדר -> auto-picks הֲדַר ("splendour-of") but the name is הָדָר

    OVERRIDE -> paste the correct form only where the auto-pick is wrong.
Leave blank to accept. Most rows need nothing.
