# אתר ההורדה של קולמוס

## אירוח
האתר מתפרסם אוטומטית ב-GitHub Pages ‏(https://omrin.github.io/kulmus/) בכל שינוי בתיקייה `site/` (`.github/workflows/pages.yml`). ב-GitHub Pages אין PHP, ולכן שם המונה מגיע מ-GitHub בלבד.

## העלאה לשרת משלכם
מעלים לשרת את: `index.html`, התיקייה `img/`, ואם רוצים מונה בשרת שלכם גם את `api/`.
(`page.html` ו-`build_site.py` הם קבצי מקור: אחרי שינוי ב-`page.html` מריצים `python build_site.py`.)

## הגדרה (פעם אחת)
בסוף `index.html` יש בלוק `CONFIG`:
- `repo` — שם המאגר ב-GitHub שבו נמצאות גרסאות ההתקנה, למשל `my-user/kulmus`.
  כפתורי ההורדה מפנים אוטומטית לגרסה האחרונה, והאתר מציג את מספר ההורדות ש-GitHub סופר לכל קובץ.

## מונה הורדות בשרת שלכם (אופציונלי, דורש PHP)
1. ב-`api/download.php` מעדכנים את `$REPO`.
2. ב-`CONFIG` מגדירים: `downloadVia: "api/download.php?file="` ו-`counterEndpoint: "api/stats.php"`.
3. התיקייה `api/` צריכה הרשאת כתיבה (הספירה נשמרת ב-`api/downloads.json`).
כך כל הורדה נספרת גם אצלכם, והמספרים זמינים בכתובת `api/stats.php`.

## איפה רואים כמה הורידו
- באתר עצמו: המספר הכולל מתחת לכפתור ההורדה, ומספר לכל מערכת בכרטיס שלה.
- ב-GitHub: `https://api.github.com/repos/<user>/<repo>/releases` (שדה `download_count` לכל קובץ).
