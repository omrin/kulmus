"""Draws the Kulmus icon -> icon.ico + icon.png.

Sound bars that together form the nib of a quill pen (קולמוס): the sound becomes writing."""

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
_APP = None

# five bars; their lower ends follow the V of a pen nib, the middle one is the writing tip
BARS = [(69, 138, "#5EEAD4"), (94, 172, "#A5B4FC"), (119, 214, "#FFFFFF"), (144, 172, "#A5B4FC"), (169, 138, "#5EEAD4")]
# small sizes (<= 24px) get three fat bars, otherwise the mark turns to mush
BARS_SMALL = [(63, 150, "#A5B4FC"), (111, 216, "#FFFFFF"), (159, 150, "#A5B4FC")]

BG = ('<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
      '<stop offset="0" stop-color="#2A2566"/><stop offset="1" stop-color="#16133A"/></linearGradient></defs>'
      '<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>')


def svg(small=False):
    if small:
        cap, bars, w, top = '<rect x="56" y="34" width="144" height="24" rx="12" fill="#2DD4BF"/>', BARS_SMALL, 34, 70
    else:
        cap, bars, w, top = '<rect x="64" y="42" width="128" height="16" rx="8" fill="#2DD4BF"/>', BARS, 18, 68
    body = "".join(f'<rect x="{x}" y="{top}" width="{w}" height="{b - top}" rx="{w / 2}" fill="{c}"/>' for x, b, c in bars)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">{BG}{cap}{body}</svg>'


SVG = svg()


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg(size <= 24).encode())).render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def main():
    global _APP
    _APP = QGuiApplication(sys.argv)          # QPainter needs an application object
    draw(256).save(str(HERE / "icon.png"))
    (HERE / "icon.svg").write_text(SVG, encoding="utf-8")
    from PIL import Image
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = []
    for n in sizes:
        q = draw(n)
        q.save(str(HERE / f"_icon{n}.png"))
        imgs.append(Image.open(HERE / f"_icon{n}.png").convert("RGBA"))
    imgs[-1].save(HERE / "icon.ico", sizes=[(n, n) for n in sizes], append_images=imgs[:-1])
    for n in sizes:
        (HERE / f"_icon{n}.png").unlink()
    print("saved", HERE / "icon.ico")


if __name__ == "__main__":
    main()
