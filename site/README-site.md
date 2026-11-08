# אתר ההורדה של קולמוס

## אירוח
האתר מתפרסם אוטומטית ב-GitHub Pages ‏(https://omrin.github.io/kulmus/) בכל שינוי בתיקייה `site/` או ב-`CHANGELOG.md`.
הפעולה `.github/workflows/pages.yml` מריצה את `site/build_site.py` ומפרסמת את התיקייה `site/_site/` שנוצרת.

## מבנה
| קובץ | מה יש בו |
|---|---|
| `home.html` | התוכן של העמוד הראשי (בלי `<head>`, כותרת עליונה ותחתונה) |
| `guides/<slug>.json` | מדריך: `title`, `description`, `h1`, `lede`, `nav`, `body_html`, `faq`, `sources`, `related`, `published`, `updated` |
| `partials/header.html`, `partials/footer.html` | כותרת עליונה ותחתונה משותפות. `{{root}}` מוחלף בנתיב לשורש האתר |
| `style.css` | העיצוב, משובץ בכל עמוד |
| `script.js` | העמוד הראשי בלבד: מונה הורדות והצעת המערכת של המבקר |
| `img/` | צילומי מסך, סמלים, `og.png` (תמונת שיתוף 1200×630) |
| `build_site.py` | בונה את כל העמודים, `sitemap.xml` ו-`404.html` |
| `make_images.py` | מצייר את `img/og.png` ו-`img/apple-touch-icon.png` (צריך PySide6) |

בנייה מקומית: `python site/build_site.py`, ואז לפתוח את `site/_site/index.html`.
"מה חדש" בעמוד הראשי נבנה מ-`CHANGELOG.md` (שלוש הגרסאות האחרונות).

## הגדרות ב-`build_site.py`
- `SITE`: כתובת האתר. אם עוברים לדומיין משלכם, משנים רק כאן (ובהגדרות GitHub Pages).
- `GOOGLE_VERIFICATION`, `BING_VERIFICATION`: קוד האימות מ-Google Search Console ומ-Bing Webmaster Tools.
- `HOME_TITLE`, `HOME_DESCRIPTION`, `USE_CARDS`: הכותרת, התיאור והכרטיסים של העמוד הראשי.

## מונה הורדות בשרת משלכם (אופציונלי, דורש PHP)
1. ב-`api/download.php` מעדכנים את `$REPO`.
2. ב-`CONFIG` שב-`script.js` מגדירים: `downloadVia: "api/download.php?file="` ו-`counterEndpoint: "api/stats.php"`.
3. התיקייה `api/` צריכה הרשאת כתיבה (הספירה נשמרת ב-`api/downloads.json`).

## איפה רואים כמה הורידו
- באתר עצמו: המספר הכולל מתחת לכפתור ההורדה, ומספר לכל מערכת בכרטיס שלה.
- ב-GitHub: `https://api.github.com/repos/<user>/<repo>/releases` (שדה `download_count` לכל קובץ).
