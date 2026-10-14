"""Wraps page.html (the page body, also used for the preview) into a standalone index.html for hosting."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
body = (HERE / "page.html").read_text(encoding="utf-8")
head = """<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="קולמוס: תוכנה חינמית בקוד פתוח לתמלול עברית על המחשב, בלי אינטרנט: הקלטות, שיחות זום וקבצים, עם הפרדת דוברים וזיהוי קולות לפי שם. Windows, macOS ו-Linux.">
<meta property="og:title" content="קולמוס — תמלול עברית על המחשב שלך">
<meta property="og:description" content="חינם, קוד פתוח, בלי אינטרנט. הקלטות, שיחות זום וקבצים, עם הפרדת דוברים.">
<meta property="og:image" content="img/main.webp">
<link rel="icon" type="image/png" href="img/favicon.png">
"""
out = head + body.replace("<title>", "<title>", 1) + "\n</html>\n"
# move <title>/<link>/<style> into <head>: everything before </style> belongs there
cut = out.index("</style>") + len("</style>")
out = out[:cut] + "\n</head>\n<body>\n" + out[cut:].replace("\n</html>\n", "\n</body>\n</html>\n")
(HERE / "index.html").write_text(out, encoding="utf-8")
print("index.html written,", len(out) // 1024, "KB")
