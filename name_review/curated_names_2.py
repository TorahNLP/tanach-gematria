"""Second curated batch — traditional Ashkenazi/Yiddish names.

Source inventory: Joshua's `Name List.txt` (429 rows, 215 usable entries). That
file supplies SPELLINGS, not nikud — only 1 of its 216 Hebrew entries carried
any vowel points, the same limitation Harkavy had. The vocalizations here are
supplied by Claude and pending Joshua's review.

These are the names the CBS registration list misses almost entirely: Yiddish
and Ashkenazi forms (זוסיא, זלאטא, טשארנא, געצל) that are common in charedi
usage but rare in Israeli civil records.

⚠️ Marked UNREVIEWED. Everything here is mechanically checked (consonants
unchanged, non-zero value, full pointing) but not yet checked by a person.
The Yiddish set in particular has real convention variation — final א vs ה,
how to point ע in געצל — that differs by community.
"""

# name -> [forms]; first is the default, rest appear in the picker
CURATED_2 = {
    # ── Yiddish women ───────────────────────────────────────────────────────
    "זעלדה": ["זֶעלְדָּה"],
    "זעלדא": ["זֶעלְדָּא"],          # the CBS spelling of the same name
    "בלומא": ["בְּלוּמָא"],
    "בריינא": ["בְּרֵיינָא"],
    "גאלדא": ["גָאלְדָּא"],
    "זיסא": ["זִיסָא"],
    "זלאטא": ["זְלָאטָא"],
    "חשא": ["חַשָׁא"],
    "טשארנא": ["טְשַׁארְנָא"],
    "ליבא": ["לִיבָּא"],
    "פרומעט": ["פְרוּמֶעט"],
    "פריידא": ["פְרֵיידָא"],
    "פרימא": ["פְרִימָא"],
    "צירע": ["צִירֶע"],
    "קונא": ["קוּנָא"],
    "ראדא": ["רָאדָא"],
    "רוזא": ["רוֹזָא"],
    "שטערנא": ["שְׁטֶערְנָא"],
    "אלטע": ["אַלְטֶע"],
    "געלע": ["גֶעלֶע"],
    "גננא": ["גְנַנָא"],
    "גרונא": ["גְרוּנָא"],
    "דובא": ["דוֹבָּא"],
    "מינדל": ["מִינְדֶּל"],          # asked for by name; CBS rank 239
    "מאטרונא": ["מַאטְרוֹנָא"],
    "גיסע": ["גִיסֶע"],
    "גנטילע": ["גְנְטִילֶע"],
    "ציטא": ["צִיטָא"],
    "אילא": ["אִילָא"],

    # ── Yiddish / Ashkenazi men ─────────────────────────────────────────────
    "אלטר": ["אַלְטֶר"],
    "געצל": ["גֶעצֶל"],
    "זונדל": ["זוּנְדֶּל"],
    "זוסיא": ["זוּסְיָא"],
    "זעליג": ["זֶעלִיג"],
    "ליבער": ["לִיבֶּער"],
    "מענדל": ["מֶענְדֶּל"],
    "פישל": ["פִישֶׁל"],
    "פייויש": ["פֵּייוִיש"],
    "שכנא": ["שַׁכְנָא"],
    "שרגא": ["שְׂרָגָא"],
    "שניאור": ["שְׁנֵיאוֹר"],
    "בער": ["בֶּער"],
    "לייב": ["לֵייבּ"],
    "מושקט": ["מוּשְׁקַט"],
    "תודרוס": ["תוֹדְרוֹס"],
    "קלונימוס": ["קָלוֹנִימוּס"],
    "שלומיאל": ["שְׁלוּמִיאֵל"],
    "מהרם": ["מַהֲרַ״ם"],

    # ── Hebrew names not in Tanach (or spelled differently there) ───────────
    "אביבה": ["אֲבִיבָה"],
    "אביגדור": ["אֲבִיגְדוֹר"],
    "בנציון": ["בְּנְצִיּוֹן"],
    "דיצה": ["דִּיצָה"],
    "חדוה": ["חֶדְוָה"],
    "חיזקיהו": ["חִיזְקִיָּהוּ"],
    "טליה": ["טַלְיָה"],
    "יוחנה": ["יוֹחָנָה"],
    "נועם": ["נוֹעַם"],
    "עמנואל": ["עִמָּנוּאֵל"],
    "עקיבא": ["עֲקִיבָא"],
    "פנחס": ["פִּנְחָס"],
    "ציפורה": ["צִיפּוֹרָה"],
    "רינה": ["רִינָה"],
    "רפאל": ["רְפָאֵל"],
    "שירה": ["שִׁירָה"],
    "שמריהו": ["שְׁמַרְיָהוּ"],
    "תהילה": ["תְּהִילָּה"],
    "גרסיאה": ["גְרַסְיָאה"],

    # ── two-word names ──────────────────────────────────────────────────────
    "אריה לייב": ["אַרְיֵה לֵייבּ"],
    "דב בער": ["דֹּב בֶּער"],
    "דובער": ["דוֹבֶער"],
    "בת שבע": ["בַּת שֶׁבַע"],
    "יום טוב": ["יוֹם טוֹב"],
    "מזל": ["מַזָּל"],
}
