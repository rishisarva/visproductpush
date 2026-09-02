"""
wordmark.py
===========

Finds the supplier's "thayyilsports" mark by reading it.

Earlier attempts compared pixels: brightness, warmth, contrast, then the shape
of the logo. All failed the same way, because a branded wooden hanger and a
plain transparent one look nearly identical by those measures. Every threshold
either missed real branding or wrecked collars.

The mark is text, so this reads it with OCR instead. That gives two things no
pixel measure could: near-certainty the mark is really there, and the exact
position of the letters, so only they get covered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

try:
    import pytesseract
    HAVE_OCR = True
except ImportError:
    HAVE_OCR = False


# OCR rarely reads the whole word cleanly on small or blurred photos, so the
# distinctive fragments count too. "thayyil" is odd enough that garment text
# will not produce it by accident.
NEEDLES = ("thayyilsports", "thayyil", "hayyilsports", "hayyil", "ayyils", "thayy")

# Tried in order, cheapest first; the first that reads the mark wins.
ATTEMPTS = (
    ((0.10, 0.00, 0.90, 0.40), 3, False, 6),
    ((0.10, 0.00, 0.90, 0.40), 3, False, 11),
    ((0.10, 0.00, 0.90, 0.40), 5, False, 6),
    ((0.10, 0.00, 0.90, 0.40), 2, True, 11),
    ((0.10, 0.00, 0.90, 0.40), 4, True, 6),
    ((0.20, 0.00, 0.80, 0.55), 4, True, 11),
    ((0.10, 0.00, 0.90, 0.60), 6, True, 6),
)


@dataclass
class Hit:
    text: str
    box: tuple                      # the letters, in original image pixels
    how: str


def _prep(img, crop, scale, sharpen):
    W, H = img.size
    x0, y0, x1, y1 = crop
    origin = (int(W * x0), int(H * y0), int(W * x1), int(H * y1))
    g = img.crop(origin).convert("L")
    g = g.resize((max(1, int(g.width * scale)), max(1, int(g.height * scale))),
                 Image.LANCZOS)
    g = ImageOps.autocontrast(g)
    if sharpen:
        g = g.filter(ImageFilter.UnsharpMask(3, 200, 3))
    return g, origin


def _matched(text):
    flat = re.sub(r"[^a-z]", "", text.lower())
    return any(n in flat for n in NEEDLES)


def find_wordmark(img):
    """Return a Hit describing where the supplier mark is, or None."""
    if not HAVE_OCR:
        return None

    for crop, scale, sharpen, psm in ATTEMPTS:
        g, origin = _prep(img, crop, scale, sharpen)
        try:
            data = pytesseract.image_to_data(
                g, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
        except Exception:
            continue

        words = data.get("text", [])
        if not words or not _matched(" ".join(words)):
            continue

        xs0, ys0, xs1, ys1 = [], [], [], []
        for i, w in enumerate(words):
            flat = re.sub(r"[^a-z]", "", w.lower())
            if not flat:
                continue
            if any(n in flat for n in NEEDLES) or flat in ("f", "k", "fk", "e"):
                x, y = data["left"][i], data["top"][i]
                cw, ch = data["width"][i], data["height"][i]
                xs0.append(x); ys0.append(y)
                xs1.append(x + cw); ys1.append(y + ch)
        if not xs0:
            continue

        bx0 = origin[0] + int(min(xs0) / scale)
        by0 = origin[1] + int(min(ys0) / scale)
        bx1 = origin[0] + int(max(xs1) / scale)
        by1 = origin[1] + int(max(ys1) / scale)

        W, H = img.size
        bw, bh = bx1 - bx0, by1 - by0
        if bh <= 0 or bw <= 0:
            continue
        if bw > W * 0.75 or bh > H * 0.14 or bw < bh:
            continue

        return Hit(text=" ".join(w for w in words if w.strip())[:80],
                   box=(bx0, by0, bx1, by1),
                   how=f"x{scale} psm{psm}{' sharp' if sharpen else ''}")

    return None
