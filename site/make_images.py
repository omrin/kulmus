"""Draws the site's share image (img/og.png, 1200x630) and apple-touch-icon (180x180) with Qt (Hebrew bidi).

    python site/make_images.py      (needs PySide6; run from the app's venv)
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath  # noqa: E402


def font(families, size, weight=QFont.Normal):
    for fam in families:
        if fam in QFontDatabase.families():
            f = QFont(fam)
            break
    else:
        f = QFont()
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def share_image():
    import make_icon
    w, h = 1200, 630
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#2A2566"))
    g.setColorAt(1, QColor("#16133A"))
    p.fillRect(0, 0, w, h, g)
    # the app window on the left, cut by the edge
    shot = QImage(str(HERE / "img" / "main.webp"))
    target = QRectF(-40, 150, 640, 640 * shot.height() / shot.width())
    path = QPainterPath()
    path.addRoundedRect(target, 18, 18)
    p.save()
    p.setClipPath(path)
    p.drawImage(target, shot)
    p.restore()
    # logo + text on the right (RTL)
    p.drawImage(QRectF(w - 64 - 112, 70, 112, 112), make_icon.draw(224))
    p.setLayoutDirection(Qt.RightToLeft)
    x0, x1 = 610, w - 64
    lines = [
        ("קולמוס", ["Suez One", "Gisha", "Segoe UI"], 84, QFont.Bold, "#FFFFFF", 196, 110),
        ("תמלול עברית בחינם", ["IBM Plex Sans Hebrew", "Segoe UI", "Arial"], 50, QFont.DemiBold, "#A5B4FC", 320, 70),
        ("על המחשב שלכם, בלי אינטרנט", ["IBM Plex Sans Hebrew", "Segoe UI", "Arial"], 34, QFont.Normal, "#E0E7FF", 400, 52),
        ("הפרדת דוברים · הקלטת שיחות זום", ["IBM Plex Sans Hebrew", "Segoe UI", "Arial"], 28, QFont.Normal, "#5EEAD4", 470, 44),
        ("Windows · Mac · Linux", ["Segoe UI", "Arial"], 26, QFont.Normal, "#5EEAD4", 520, 40),
    ]
    for text, fams, size, weight, colour, y, height in lines:
        p.setFont(font(fams, size, weight))
        p.setPen(QColor(colour))
        p.drawText(QRectF(x0, y, x1 - x0, height), Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter, text)
    p.end()
    img.save(str(HERE / "img" / "og.png"), "PNG", 8)


def touch_icon():
    import make_icon
    img = QImage(180, 180, QImage.Format_RGB32)
    img.fill(QColor("#16133A"))                    # iOS shows transparency as black: use the icon's own ground
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawImage(QRectF(-8, -8, 196, 196), make_icon.draw(392))
    p.end()
    img.save(str(HERE / "img" / "apple-touch-icon.png"))


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    share_image()
    touch_icon()
    print("img/og.png and img/apple-touch-icon.png written")
