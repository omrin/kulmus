"""Builds the static site into site/_site/ (published by .github/workflows/pages.yml).

Pages: the home page (home.html), a guides hub, one page per guide (guides/<slug>.json), 404.html and sitemap.xml.
Every page gets the shared header/footer (partials/), the stylesheet inlined, and its own title, description,
canonical URL, social tags and JSON-LD. "What's new" on the home page is rendered from CHANGELOG.md.

    python site/build_site.py            # -> site/_site/
"""
import datetime as dt
import html
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "_site"
SITE = "https://omrin.github.io/kulmus/"   # the site's address: change here if it moves to its own domain
BRAND = "קולמוס"
REPO = "https://github.com/omrin/kulmus"
GOOGLE_VERIFICATION = ""   # content="…" of the <meta name="google-site-verification"> tag from Search Console
BING_VERIFICATION = ""     # content="…" of the <meta name="msvalidate.01"> tag from Bing Webmaster Tools
FIRST_RELEASE = "2026-10-14"

HOME_TITLE = "תמלול עברית בחינם, על המחשב ובלי אינטרנט | קולמוס"
HOME_DESCRIPTION = ("תמלול עברית בחינם, על המחשב שלכם: הקלטות, שיחות זום ו-Teams וקבצי שמע ווידאו הופכים לטקסט "
                    "עם הפרדת דוברים. בלי אינטרנט ובלי מנוי. Windows, Mac ו-Linux.")
HOME_OG_TITLE = "קולמוס – תמלול עברית בחינם, על המחשב שלכם"

# cards on the home page ("מה מתמללים בקולמוס"): guide slug -> (heading, one line)
USE_CARDS = [
    ("zoom-transcription", "שיחות זום, Teams ו-Meet", "הקלטה ותמלול של השיחה, עם הפרדה ביניכם לבין המשתתפים."),
    ("interview-transcription", "ראיונות", "ראיונות למחקר ולעיתונות, בלי להעלות את ההקלטות לאף שירות."),
    ("lecture-transcription", "הרצאות", "הרצאות ושיעורים מוקלטים, וחיפוש בכל הסמסטר לפני המבחן."),
    ("audio-to-text", "קבצי שמע ווידאו", "מהקלטה בטלפון, הודעה קולית או סרטון לטקסט: mp3, ‏m4a, ‏mp4 ועוד."),
    ("offline-transcription", "שיחות רגישות", "פגישות פנימיות, ייעוץ וטיפול: תמלול שלא יוצא מהמחשב."),
    ("free-transcription", "השוואת אפשרויות", "מה באמת חינם בתמלול עברית, ומתי עדיף לשלם."),
]

MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
         "&family=IBM+Plex+Sans+Hebrew:wght@400;600&family=Suez+One&display=swap")
ALLOWED_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "em", "a", "code", "table", "thead", "tbody", "tr", "th", "td",
                "figure", "img", "figcaption", "aside", "br"}

esc = html.escape


def he_date(d: dt.date) -> str:
    return f"{d.day} ב{MONTHS[d.month - 1]} {d.year}"


def changelog():
    """[(version, date, [bullets])] from CHANGELOG.md, newest first."""
    out = []
    text = (HERE.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    for m in re.finditer(r"^## (\S+) · (\d{1,2})\.(\d{1,2})\.(\d{4})\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        bullets = [ln[2:].strip() for ln in m.group(5).splitlines() if ln.startswith("- ")]
        out.append((m.group(1), dt.date(int(m.group(4)), int(m.group(3)), int(m.group(2))), bullets))
    if not out:
        sys.exit("CHANGELOG.md has no '## <version> · <dd.mm.yyyy>' sections")
    return out


def guides():
    items = []
    for f in sorted((HERE / "guides").glob("*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        g["slug"] = f.stem
        g["published_d"] = dt.date.fromisoformat(g["published"])
        g["updated_d"] = dt.date.fromisoformat(g.get("updated") or g["published"])
        items.append(g)
    order = [slug for slug, *_ in USE_CARDS]
    items.sort(key=lambda g: order.index(g["slug"]) if g["slug"] in order else 99)
    return items


def jsonld(data) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return '<script type="application/ld+json">' + text + "</script>"


def head(title, description, path, root, og_title=None, ld=None, robots=None, og_type="website"):
    url = SITE + path
    css = (HERE / "style.css").read_text(encoding="utf-8")
    tags = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
    ]
    if robots:
        tags.append(f'<meta name="robots" content="{robots}">')
    else:
        tags.append(f'<link rel="canonical" href="{url}">')
    if GOOGLE_VERIFICATION and path == "":
        tags.append(f'<meta name="google-site-verification" content="{esc(GOOGLE_VERIFICATION)}">')
    if BING_VERIFICATION and path == "":
        tags.append(f'<meta name="msvalidate.01" content="{esc(BING_VERIFICATION)}">')
    tags += [
        '<meta name="theme-color" content="#4F46E5">',
        f'<link rel="icon" type="image/png" sizes="64x64" href="{root}img/favicon.png">',
        f'<link rel="apple-touch-icon" href="{root}img/apple-touch-icon.png">',
    ]
    if not robots:
        tags += [
        f'<meta property="og:type" content="{og_type}">',
        '<meta property="og:locale" content="he_IL">',
        f'<meta property="og:site_name" content="{BRAND}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:title" content="{esc(og_title or title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:image" content="{SITE}img/og.png">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:alt" content="חלון קולמוס עם תמלול של ישיבת צוות מחולק לפי דוברים">',
        '<meta name="twitter:card" content="summary_large_image">',
        ]
    tags += [
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        f'<link rel="stylesheet" href="{FONTS.replace("&", "&amp;")}">',
        f"<style>\n{css}</style>",
    ]
    if ld:
        tags.append(jsonld(ld))
    return "\n".join(tags)


def page(title, description, path, root, main, guide_list, **kw):
    header = (HERE / "partials" / "header.html").read_text(encoding="utf-8").replace("{{root}}", root)
    links = "\n".join(f'      <a href="{root}{g["slug"]}/">{esc(g["nav"])}</a>' for g in guide_list)
    footer = ((HERE / "partials" / "footer.html").read_text(encoding="utf-8")
              .replace("{{guide_links}}", links).replace("{{root}}", root))
    script = kw.pop("script", "")
    return ("<!doctype html>\n<html lang=\"he\" dir=\"rtl\">\n<head>\n" + head(title, description, path, root, **kw)
            + "\n</head>\n<body>\n" + header + "\n" + main + "\n" + footer + script + "</body>\n</html>\n")


def news_html(entries) -> str:
    out = []
    for i, (version, d, bullets) in enumerate(entries[:3]):
        new = ' <span class="tag-new">חדש</span>' if i == 0 else ""
        items = "".join(f"<li>{esc(b)}</li>" for b in bullets)
        out.append(f'        <article class="rel"><div class="rel-head"><h3>גרסה {esc(version)}{new}</h3>'
                   f'<time class="rel-date" datetime="{d.isoformat()}">{d:%d.%m.%Y}</time></div><ul>{items}</ul></article>')
    return "\n".join(out)


def use_cards(root, guide_list, cards=USE_CARDS) -> str:
    have = {g["slug"] for g in guide_list}
    return "\n".join(f'        <a class="use" href="{root}{slug}/"><h3>{esc(h)}</h3><p>{esc(p)}</p></a>'
                     for slug, h, p in cards if slug in have)


def check_fragment(slug, body):
    for tag in re.findall(r"</?([a-zA-Z0-9]+)", body):
        if tag.lower() not in ALLOWED_TAGS:
            sys.exit(f"guide {slug}: tag <{tag}> is not allowed")
    if re.search(r"\son\w+=|<script|style=", body, re.I):
        sys.exit(f"guide {slug}: scripts or inline styles are not allowed")
    if re.search(r'<table(?! class="cmp">)', body):
        sys.exit(f'guide {slug}: tables must be <table class="cmp">')
    if re.search(r"<a (?!href=)", body):
        sys.exit(f"guide {slug}: links must start with href=")
    if "{{root}}" in body.replace('href="{{root}}', "").replace('src="{{root}}', ""):
        sys.exit(f"guide {slug}: {{{{root}}}} outside href/src")


def prose(body, root) -> str:
    body = body.replace("{{root}}", root)
    body = body.replace('<table class="cmp">', '<div class="table-wrap"><table class="cmp">').replace("</table>", "</table></div>")
    # external links open no window handle to us and pass no referrer details beyond the origin
    body = re.sub(r'<a href="(https?://[^"]+)"(?![^>]*\brel=)', r'<a href="\1" rel="noopener"', body)
    return body


def build_home(guide_list, log):
    version, date, _ = log[0]
    updated = max([date] + [g["updated_d"] for g in guide_list])
    main = (HERE / "home.html").read_text(encoding="utf-8")
    main = main.replace("{{news}}", news_html(log)).replace("{{use_cards}}", use_cards("./", guide_list))
    app = {
        "@type": "SoftwareApplication", "@id": SITE + "#app", "name": BRAND, "alternateName": "Kulmus",
        "description": "תוכנה חינמית בקוד פתוח לתמלול עברית על המחשב, בלי אינטרנט: הקלטות, שיחות זום וקבצי שמע ווידאו, "
                       "עם הפרדת דוברים וזיהוי קולות לפי שם.",
        "url": SITE, "applicationCategory": "MultimediaApplication", "applicationSubCategory": "תמלול דיבור לטקסט",
        "operatingSystem": "Windows 10, Windows 11, macOS 12+, Linux", "softwareVersion": version,
        "datePublished": FIRST_RELEASE, "dateModified": date.isoformat(), "inLanguage": "he", "isAccessibleForFree": True,
        "license": "https://opensource.org/licenses/MIT", "downloadUrl": REPO + "/releases/latest",
        "releaseNotes": REPO + "/releases", "screenshot": SITE + "img/main.webp", "image": SITE + "img/icon.png",
        "featureList": ["תמלול עברית בלי אינטרנט", "הפרדת דוברים", "זיהוי קולות לפי שם",
                        "הקלטה ותמלול של שיחות זום, Teams ו-Meet (Windows ו-Linux)", "ייבוא קבצי שמע ווידאו",
                        "ייצוא לטקסט ולכתוביות srt", "חיפוש בכל ההקלטות"],
        "memoryRequirements": "8GB RAM לפחות, 16GB מומלץ", "storageRequirements": "כ-3GB",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "ILS"}, "sameAs": [REPO],
    }
    site = {"@type": "WebSite", "@id": SITE + "#website", "url": SITE, "name": BRAND, "alternateName": "Kulmus", "inLanguage": "he"}
    out = page(HOME_TITLE, HOME_DESCRIPTION, "", "./", main, guide_list, og_title=HOME_OG_TITLE,
               ld={"@context": "https://schema.org", "@graph": [site, app]},
               script='<script src="script.js" defer></script>\n')
    (OUT / "index.html").write_text(out, encoding="utf-8")
    return updated


def build_guide(g, guide_list):
    root = "../"
    url = SITE + g["slug"] + "/"
    check_fragment(g["slug"], g["body_html"])
    faq = ""
    if g.get("faq"):
        qs = "".join(f'<details class="q"><summary>{esc(q["q"])}</summary><p>{esc(q["a"])}</p></details>' for q in g["faq"])
        faq = f'<section class="more"><h2>שאלות נפוצות</h2><div class="faq">{qs}</div></section>'
    others = [x for x in guide_list if x["slug"] != g["slug"]]
    rel_slugs = [s for s in g.get("related", []) if any(o["slug"] == s for o in others)][:3] or [o["slug"] for o in others][:3]
    related = "".join(f'<a class="use" href="{root}{o["slug"]}/"><h3>{esc(o["h1"])}</h3><p>{esc(o["description"])}</p></a>'
                      for s in rel_slugs for o in others if o["slug"] == s)
    sources = ""
    if g.get("sources"):
        lis = "".join(f'<li><a href="{esc(s["url"])}" rel="noopener">{esc(s["title"])}</a></li>' for s in g["sources"])
        sources = f'<section class="more sources"><h2>מקורות</h2><ol>{lis}</ol></section>'
    main = f"""<main class="article" id="top">
  <div class="wrap">
    <nav class="crumbs" aria-label="מיקום באתר"><a href="{root}">{BRAND}</a> › <a href="{root}guides/">מדריכים</a> › {esc(g["nav"])}</nav>
    <h1>{esc(g["h1"])}</h1>
    <p class="lede">{esc(g["lede"])}</p>
    <p class="updated">עודכן ב-{he_date(g["updated_d"])}</p>
    <div class="prose">
{prose(g["body_html"], root)}
    </div>
    {faq}
    <section class="cta-box">
      <h2>להוריד את קולמוס</h2>
      <p>תוכנה חינמית בקוד פתוח לתמלול עברית על המחשב, ל-Windows, ‏Mac ו-Linux. בלי אינטרנט, בלי מנוי ובלי הגבלת דקות.</p>
      <div class="cta"><a class="btn btn-primary" href="{root}#download">להורדה חינם</a><a class="btn btn-quiet" href="{root}#requirements">דרישות ומגבלות</a></div>
    </section>
    <section class="more related"><h2>מדריכים נוספים</h2><div class="uses">{related}</div></section>
    {sources}
  </div>
</main>"""
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "Article", "headline": g["h1"][:110], "description": g["description"], "inLanguage": "he",
         "datePublished": g["published"], "dateModified": g["updated_d"].isoformat(), "mainEntityOfPage": url,
         "image": SITE + "img/og.png",
         "author": {"@type": "Organization", "name": BRAND, "url": SITE},
         "publisher": {"@type": "Organization", "name": BRAND, "logo": {"@type": "ImageObject", "url": SITE + "img/icon.png"}}},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": BRAND, "item": SITE},
            {"@type": "ListItem", "position": 2, "name": "מדריכים", "item": SITE + "guides/"},
            {"@type": "ListItem", "position": 3, "name": g["nav"], "item": url}]},
    ]}
    d = OUT / g["slug"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(page(f'{g["title"]} | {BRAND}', g["description"], g["slug"] + "/", root, main, guide_list,
                                       ld=ld, og_type="article"), encoding="utf-8")


def build_hub(guide_list):
    root = "../"
    cards = "".join(f'<a class="use" href="{root}{g["slug"]}/"><h2>{esc(g["h1"])}</h2><p>{esc(g["description"])}</p></a>'
                    for g in guide_list)
    main = f"""<main class="article hub" id="top">
  <div class="wrap">
    <nav class="crumbs" aria-label="מיקום באתר"><a href="{root}">{BRAND}</a> › מדריכים</nav>
    <h1>מדריכים לתמלול בעברית</h1>
    <p class="lede">איך הופכים הקלטה, שיחת זום, ראיון או הרצאה לטקסט: מדריכים מעשיים, עם היתרונות, החסרונות והחלופות, גם כשקולמוס הוא לא הבחירה המתאימה.</p>
    <div class="uses">{cards}</div>
  </div>
</main>"""
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "CollectionPage", "name": "מדריכים לתמלול בעברית", "url": SITE + "guides/", "inLanguage": "he",
         "hasPart": [{"@type": "Article", "headline": g["h1"], "url": SITE + g["slug"] + "/"} for g in guide_list]},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": BRAND, "item": SITE},
            {"@type": "ListItem", "position": 2, "name": "מדריכים", "item": SITE + "guides/"}]}]}
    d = OUT / "guides"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(page(f"מדריכים לתמלול בעברית | {BRAND}",
                                       "מדריכים מעשיים לתמלול בעברית: הקלטות וקבצי שמע, שיחות זום, ראיונות, הרצאות, תמלול בלי "
                                       "אינטרנט והשוואה של האפשרויות החינמיות.", "guides/", root, main, guide_list, ld=ld),
                                  encoding="utf-8")


def build_404(guide_list):
    root = "/kulmus/" if SITE.endswith("/kulmus/") else "/"
    main = f"""<main class="article" id="top">
  <div class="wrap">
    <h1>העמוד לא נמצא</h1>
    <p class="lede">אולי הכתובת השתנתה. אפשר לחזור לעמוד הראשי או לעיין במדריכים.</p>
    <div class="cta"><a class="btn btn-primary" href="{root}">לעמוד הראשי</a><a class="btn btn-quiet" href="{root}guides/">מדריכים</a></div>
  </div>
</main>"""
    (OUT / "404.html").write_text(page(f"העמוד לא נמצא | {BRAND}", "העמוד לא נמצא באתר של קולמוס.", "404.html", root, main,
                                       guide_list, robots="noindex"), encoding="utf-8")


def build_sitemap(home_updated, guide_list):
    urls = [(SITE, home_updated)]
    if guide_list:
        urls.append((SITE + "guides/", max(g["updated_d"] for g in guide_list)))
        urls += [(SITE + g["slug"] + "/", g["updated_d"]) for g in guide_list]
    body = "".join(f"  <url><loc>{u}</loc><lastmod>{d.isoformat()}</lastmod></url>\n" for u, d in urls)
    (OUT / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                     '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + body + "</urlset>\n",
                                     encoding="utf-8")
    return len(urls)


def main():
    OUT.mkdir(exist_ok=True)
    for old in OUT.iterdir():
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    shutil.copytree(HERE / "img", OUT / "img")
    shutil.copy2(HERE / "script.js", OUT / "script.js")
    (OUT / ".nojekyll").write_text("")
    log = changelog()
    guide_list = guides()
    for g in guide_list:
        if len(g["title"]) > 50:
            print(f"warning: {g['slug']} title is {len(g['title'])} characters (limit 50)")
        if not 110 <= len(g["description"]) <= 160:
            print(f"warning: {g['slug']} description is {len(g['description'])} characters (110-160)")
    home_updated = build_home(guide_list, log)
    for g in guide_list:
        build_guide(g, guide_list)
    if guide_list:
        build_hub(guide_list)
    build_404(guide_list)
    n = build_sitemap(home_updated, guide_list)
    for extra in HERE.glob("google*.html"):          # Search Console verification file, if used
        shutil.copy2(extra, OUT / extra.name)
    print(f"built {OUT}: home, {len(guide_list)} guides, sitemap with {n} URLs")


if __name__ == "__main__":
    main()
