"""
wordmark.py
===========

Finds the supplier wordmark by matching its actual shape.

Statistical tests could never work here: a transparent hanger and a branded
wooden one score almost identically on brightness, warmth and contrast, which
is why every threshold tweak either missed real branding or wrecked collars.
The wordmark itself, though, is always the same word in the same font, so we
look for that instead.

The template below is the "thayyilsports" mark lifted from a clear photo,
stored as a small PNG. Matching is done on ink strokes only, with the
background level removed, so it works whatever the clip is lit like.
"""

from __future__ import annotations
import base64, io
import numpy as np
from PIL import Image, ImageFilter

TEMPLATE_B64 = (
    "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"
)
_T = None

def _highpass(img: Image.Image, radius: float) -> np.ndarray:
    """Strip the background level so only ink strokes remain."""
    a = np.asarray(img.convert("L"), dtype=np.float32)
    b = np.asarray(img.convert("L").filter(ImageFilter.BoxBlur(radius)), dtype=np.float32)
    return b - a          # positive where darker than surroundings, i.e. ink

def template() -> np.ndarray:
    global _T
    if _T is None:
        img = Image.open(io.BytesIO(base64.b64decode(TEMPLATE_B64))).convert("L")
        hp = _highpass(img, 6)
        hp = np.maximum(hp, 0)                 # ink only
        _T = (hp - hp.mean()) / (hp.std() + 1e-6)
    return _T

def _ncc_map(region: np.ndarray, tmpl: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of tmpl over region, via FFT."""
    th, tw = tmpl.shape
    rh, rw = region.shape
    if rh < th or rw < tw:
        return np.zeros((1, 1), dtype=np.float32)

    fh, fw = rh + th, rw + tw
    F = np.fft.rfft2(region, (fh, fw))
    T = np.fft.rfft2(tmpl[::-1, ::-1], (fh, fw))
    corr = np.fft.irfft2(F * T, (fh, fw))[th - 1:rh, tw - 1:rw]

    ones = np.ones_like(tmpl)
    O = np.fft.rfft2(ones, (fh, fw))
    s1 = np.fft.irfft2(F * O, (fh, fw))[th - 1:rh, tw - 1:rw]
    F2 = np.fft.rfft2(region * region, (fh, fw))
    s2 = np.fft.irfft2(F2 * O, (fh, fw))[th - 1:rh, tw - 1:rw]

    n = tmpl.size
    var = np.maximum(s2 - s1 * s1 / n, 1e-6)
    return (corr / (np.sqrt(var) * np.sqrt(n))).astype(np.float32)

def find_wordmark(img: Image.Image, scales=None, search_top=0.55):
    """
    Return (score, box) for the best wordmark match, box in pixel coords.
    Score is a correlation between -1 and 1; genuine marks score high.
    """
    tmpl = template()
    th, tw = tmpl.shape
    g = img.convert("L")
    W, H = g.size
    top = int(H * search_top)
    region_img = g.crop((0, 0, W, top))

    best = (-1.0, None)
    if scales is None:
        # wordmark width as a fraction of image width, coarse to fine
        scales = [0.05, 0.065, 0.08, 0.10, 0.12, 0.15, 0.18, 0.22,
                  0.27, 0.33, 0.40, 0.48, 0.58, 0.70]

    for frac in scales:
        target_w = W * frac
        if target_w < tw * 0.6:
            continue
        f = tw / target_w                      # downscale factor for region
        nw, nh = max(tw + 4, int(region_img.width * f)), max(th + 4, int(region_img.height * f))
        if nw < tw + 2 or nh < th + 2:
            continue
        resized = region_img.resize((nw, nh), Image.LANCZOS)
        small = np.maximum(_highpass(resized, 6), 0)
        m = _ncc_map(small, tmpl)
        if m.size == 0:
            continue
        idx = int(np.argmax(m))
        y, x = divmod(idx, m.shape[1])
        score = float(m[y, x])
        if score > best[0]:
            sx = region_img.width / nw
            sy = region_img.height / nh
            best = (score, (int(x * sx), int(y * sy),
                            int((x + tw) * sx), int((y + th) * sy)))
    return best
