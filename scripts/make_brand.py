"""Generate Shortrizz brand assets (logo SVG/PNG, banner) into docs/media.

Sibling of the Comfyrizz mark: same 112 px rounded-square gradient tile with a
gloss, same wordmark layout. Shortrizz swaps the cyan end for hot pink/amber and
the play-with-motion-lines glyph for a 9:16 phone with a highlighted caption.
Wordmark text is converted to outlines with fontTools so the SVG needs no font.
Usage: python scripts/make_brand.py [FONT.ttf] [--thumbs a.jpg b.jpg c.jpg]
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

OUT = Path(__file__).resolve().parents[1] / "docs" / "media"
FONT = "/usr/share/fonts/truetype/sand-box/google/Poppins/Poppins-ExtraBold.ttf"

def text_path(text, size, letter=-0.01):
    f = TTFont(FONT); gs = f.getGlyphSet(); cmap = f.getBestCmap(); upm = f["head"].unitsPerEm
    s = size / upm; pen = SVGPathPen(gs); x = 0
    for ch in text:
        g = cmap[ord(ch)]
        gs[g].draw(TransformPen(pen, (s, 0, 0, -s, x, 0)))
        x += gs[g].width * s + letter * size
    return pen.getCommands(), x

GRAD = """<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#8b5cf6"/><stop offset="0.55" stop-color="#ec4899"/><stop offset="1" stop-color="#f59e0b"/>
    </linearGradient>
    <linearGradient id="s" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.28"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>"""

# Final mark ("sliced play"): a 9:16 phone card with a rounded play button, cut by one diagonal
# slice; the lower piece slides along the cut. It reads as "a clip cut out of a video".
# 120-unit grid; the slice gap is 6 units so it still shows at 32 px.
CARD = "M48 18 H72 A12 12 0 0 1 84 30 V90 A12 12 0 0 1 72 102 H48 A12 12 0 0 1 36 90 V30 A12 12 0 0 1 48 18 Z"
PLAY = "M52 45.5 Q52 41 56 43.3 L73 53.4 Q76.8 55.7 73 58 L56 68.1 Q52 70.4 52 65.9 Z"
# cut line y = 98 - 0.5x clips the foot of the play button; half gap 3
TOP = "M0 0 H120 V35 L0 95 Z"
BOT = "M0 101 L120 41 V120 H0 Z"
SHIFT = "translate(3.2 1.6)"   # lower piece drops off the cut

def _defs(uid):
    return (f'<clipPath id="{uid}t"><path d="{TOP}"/></clipPath>'
            f'<clipPath id="{uid}b"><path d="{BOT}"/></clipPath>')

def _pieces(uid, card_fill, play_fill=None, knockout=False):
    def body():
        if knockout:
            return f'<path fill-rule="evenodd" fill="{card_fill}" d="{CARD} {PLAY}"/>'
        return f'<path fill="{card_fill}" d="{CARD}"/><path fill="{play_fill}" d="{PLAY}"/>'
    return (f'<g clip-path="url(#{uid}t)">{body()}</g>'
            f'<g transform="{SHIFT}"><g clip-path="url(#{uid}b)">{body()}</g></g>')

def tile(x=4, y=4, uid="k"):
    return f"""<g transform="translate({x - 4} {y - 4})">
  <defs>{_defs(uid)}</defs>
  <rect x="4" y="4" width="112" height="112" rx="28" fill="url(#g)"/>
  <rect x="4" y="4" width="112" height="56" rx="28" fill="url(#s)"/>
  {_pieces(uid, "#ffffff", "url(#g)")}
</g>"""

def mono_mark(color, uid="mm"):
    """Single-colour mark (no tile) for watermarks: play knocked out of the sliced card."""
    return f'<defs>{_defs(uid)}</defs>' + _pieces(uid, color, knockout=True)

def mono_logo(color, wordmark=True):
    d, w = text_path("Shortrizz", 86)
    W = int(140 + w + 12) if wordmark else 120
    word = f'<g transform="translate(140 91)" fill="{color}"><path d="{d}"/></g>' if wordmark else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="120" viewBox="0 0 {W} 120" role="img" aria-label="Shortrizz">
  {mono_mark(color)}{word}
</svg>
"""

def icon(size_label="app"):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120" role="img" aria-label="Shortrizz">
  <defs>{GRAD}</defs>{tile()}
</svg>
"""

def logo(fill):
    d, w = text_path("Shortrizz", 86)
    W = int(140 + w + 12)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="120" viewBox="0 0 {W} 120" role="img" aria-label="Shortrizz">
  <title>Shortrizz</title>
  <defs>
    {GRAD}
  </defs>
  {tile()}
  <g transform="translate(140 91)" fill="{fill}"><path d="{d}"/></g>
</svg>
"""

def banner():
    d, w = text_path("Shortrizz", 100)
    t1, _ = text_path("Long video in. Viral-ready shorts out.", 33, 0)
    t2, _ = text_path("Self-hosted AI clipper  ·  CLI + Hermes plugin  ·  CPU only", 21, 0)
    thumbs = ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1280" height="640" viewBox="0 0 1280 640">
  <defs>
    {GRAD}
    <radialGradient id="glow" cx="0.85" cy="0.2" r="0.8">
      <stop offset="0" stop-color="#ec4899" stop-opacity="0.35"/><stop offset="1" stop-color="#0b1020" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="ph"><rect width="216" height="384" rx="22"/></clipPath>
  </defs>
  <rect width="1280" height="640" fill="#0b1020"/>
  <rect width="1280" height="640" fill="url(#glow)"/>
  <g transform="translate(72 150) scale(1.1)">{tile(uid="bn")}</g>
  <g transform="translate(240 252)" fill="#ffffff"><path d="{d}"/></g>
  <g transform="translate(76 360)" fill="#ffffff" opacity="0.92"><path d="{t1}"/></g>
  <g transform="translate(78 410)" fill="#a5b4fc"><path d="{t2}"/></g>
  <rect x="76" y="460" width="560" height="6" rx="3" fill="url(#g)"/>
  {{THUMBS}}
</svg>
"""

def main():
    args = sys.argv[1:]
    thumbs = []
    if "--thumbs" in args:
        i = args.index("--thumbs"); thumbs = args[i + 1:]; args = args[:i]
    global FONT
    if args:
        FONT = args[0]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "logo-dark.svg").write_text(logo("#ffffff"))
    (OUT / "logo-light.svg").write_text(logo("#0b1020"))
    (OUT / "banner.template.svg").write_text(banner())
    (OUT / "logo-mono-white.svg").write_text(mono_logo("#ffffff"))
    (OUT / "logo-mono-dark.svg").write_text(mono_logo("#0b1020"))
    (OUT / "mark-mono-white.svg").write_text(mono_logo("#ffffff", wordmark=False))
    (OUT / "icon.svg").write_text(icon())
    import cairosvg
    # video watermark assets (white, transparent) shipped inside the package
    pkg = OUT.parents[1] / "shortrizz" / "assets"
    pkg.mkdir(parents=True, exist_ok=True)
    wm = mono_logo("#ffffff")
    cairosvg.svg2png(bytestring=wm.encode(), write_to=str(pkg / "watermark.png"), output_height=96)
    d, w = text_path("made with", 30, 0)
    full_w = int(140 + text_path("Shortrizz", 86)[1] + 12)
    made = wm.replace('height="120" viewBox="0 0 %d 120"' % full_w, 'height="160" viewBox="0 -40 %d 160"' % full_w).replace(
        "</svg>", f'<g transform="translate(142 -6)" fill="#ffffff" opacity="0.85"><path d="{d}"/></g></svg>')
    cairosvg.svg2png(bytestring=made.encode(), write_to=str(pkg / "watermark-madewith.png"), output_height=128)
    for px in (32, 512):
        cairosvg.svg2png(url=str(OUT / "icon.svg"), write_to=str(OUT / f"icon-{px}.png"), output_width=px)
    for name in ("logo-mono-white", "logo-mono-dark"):
        cairosvg.svg2png(url=str(OUT / f"{name}.svg"), write_to=str(OUT / f"{name}.png"), output_height=240)
    for name in ("logo-dark", "logo-light"):
        cairosvg.svg2png(url=str(OUT / f"{name}.svg"), write_to=str(OUT / f"{name}.png"), output_height=240)
    if thumbs:
        render_banner(thumbs, OUT / "banner.png")
    print("wrote", OUT)

def render_banner(thumbs, out_png):
    """Fill the banner's right side with up to 3 vertical frames (paths to JPG/PNG) and rasterise."""
    import base64, cairosvg
    svg = (OUT / "banner.template.svg").read_text()
    parts = []
    for i, p in enumerate(thumbs[:3]):
        b64 = base64.b64encode(Path(p).read_bytes()).decode()
        mime = "image/png" if str(p).endswith(".png") else "image/jpeg"
        x, y, rot = 800 + i * 140, 110 + (i % 2) * 50, (-6, 2, 7)[i]
        parts.append(f'<g transform="translate({x} {y}) rotate({rot} 108 192)">'
                     f'<rect x="-6" y="-6" width="228" height="396" rx="28" fill="url(#g)"/>'
                     f'<g clip-path="url(#ph)"><image width="216" height="384" preserveAspectRatio="xMidYMid slice" '
                     f'xlink:href="data:{mime};base64,{b64}"/></g></g>')
    cairosvg.svg2png(bytestring=svg.replace("{THUMBS}", "".join(parts)).encode(), write_to=str(out_png),
                     output_width=1280)

if __name__ == "__main__":
    main()
