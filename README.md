# קולמוס — Kulmus

**🌐 אתר והורדה: https://omrin.github.io/kulmus/** · [מה חדש](CHANGELOG.md)

קולמוס היא תוכנה חינמית בקוד פתוח לתמלול עברית **על המחשב שלכם, בלי אינטרנט**: הקלטה מהמיקרופון, הקלטת שיחות
(Zoom, Teams, Meet), או ייבוא קבצי שמע ווידאו — עם הפרדת דוברים וזיהוי קולות מוכרים לפי שם.
השמע והטקסט לא יוצאים מהמחשב.

Free, open-source, **offline** Hebrew speech-to-text for Windows, macOS and Linux, with speaker
separation and named-voice recognition. Audio never leaves the computer.

## מה יש בה
- תמלול מדויק עם מודל [ivrit.ai whisper-large-v3-turbo](https://huggingface.co/ivrit-ai/whisper-large-v3-turbo-ct2)
- הפרדת דוברים („דובר 1", „דובר 2"), וזיהוי אוטומטי של אנשים שנתתם להם שם בעבר
- הקלטת שיחות: המיקרופון שלכם ושמע המחשב בערוצים נפרדים (Windows, Linux)
- ספריית הקלטות עם חיפוש בטקסט, נגן, ייצוא ל‑txt/srt, ומחיקת השמע לחיסכון במקום
- בחירת התיקייה שבה נשמרים ההקלטות והתמלולים

## דרישות ומגבלות
- מעבד 64‑ביט מודרני, **8GB RAM** לפחות (16GB מומלץ), כ‑3GB פנויים בדיסק
- בהפעלה הראשונה יורד מודל התמלול (1.6GB, פעם אחת)
- העיבוד רץ על המעבד ותופס אותו כמעט כולו: תמלול + זיהוי דוברים לוקח בערך 40–75% מאורך ההקלטה
- ב‑macOS אין הקלטת שמע מחשב (שיחות) בלי תוכנה נוספת; הקלטה מהמיקרופון וייבוא קבצים עובדים

## הרצה מקוד המקור
```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
python packaging/fetch_models.py
python hebtranscribe.py        # נקודת הכניסה של קולמוס
```
בדיקות: `pytest tests` (התקני שמע מדומים, בלי חומרה ובלי המודל הגדול).

## בנייה
`pyinstaller packaging/kulmus.spec`. דחיפת תג גרסה (`v1.0.0`) בונה ומפרסמת אוטומטית גרסאות ל‑Windows,
macOS ו‑Linux דרך `.github/workflows/release.yml`.

## רישיון
הקוד: MIT (ראו `LICENSE`). רכיבים ומודלים של צד שלישי ורישיונותיהם: `THIRD_PARTY_NOTICES.md`.
