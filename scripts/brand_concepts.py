"""Three Shortrizz mark concepts (exploration). The chosen one lives in make_brand.py."""
import cairosvg, sys
from pathlib import Path
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/concepts")
OUT.mkdir(parents=True, exist_ok=True)
DEFS = """<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#8b5cf6"/><stop offset="0.55" stop-color="#ec4899"/><stop offset="1" stop-color="#f59e0b"/></linearGradient>
<linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".28"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>"""
TILE = '<rect x="4" y="4" width="112" height="112" rx="28" fill="url(#g)"/><rect x="4" y="4" width="112" height="56" rx="28" fill="url(#s)"/>'
CONCEPTS = {
  # A: viewfinder S - an S stroke whose ends are crop corners of a 9:16 frame, play notch in the middle
  "A-viewfinder-s": TILE + '''<path d="M80 26 H50 a14 14 0 0 0 -14 14 v6 a14 14 0 0 0 14 14 h20 a14 14 0 0 1 14 14 v6 a14 14 0 0 1 -14 14 H40"
     fill="none" stroke="#fff" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>
     <path d="M80 26 v12 M40 94 v-12" stroke="#fff" stroke-width="11" stroke-linecap="round"/>''',
  # B: clip cut - vertical frame with play, diagonal slice offset (the 'clip')
  "B-sliced-play": TILE + '''<g><rect x="36" y="20" width="48" height="80" rx="12" fill="#fff"/>
     <path d="M52 44 L72 60 L52 76 Z" fill="#ec4899"/>
     <path d="M30 78 L92 54" stroke="url(#g)" stroke-width="7"/></g>''',
  # C: stacked feed - three fanned 9:16 cards, front card has a play triangle + yellow caption pill
  "C-stacked-feed": TILE + '''<rect x="22" y="30" width="34" height="60" rx="8" fill="#fff" opacity=".35" transform="rotate(-12 39 60)"/>
     <rect x="64" y="30" width="34" height="60" rx="8" fill="#fff" opacity=".35" transform="rotate(12 81 60)"/>
     <rect x="40" y="22" width="40" height="72" rx="10" fill="#fff"/>
     <path d="M54 44 L70 56 L54 68 Z" fill="#8b5cf6"/><rect x="48" y="78" width="24" height="7" rx="3.5" fill="#ffd400"/>''',
  # D: bolt S - an S built from two offset rounded 9:16 cards (top shifted right) = 'short' + motion
  "D-bolt-s": TILE + '''<path d="M44 22 h34 a8 8 0 0 1 8 8 v22 L62 62 H44 a8 8 0 0 1 -8 -8 V30 a8 8 0 0 1 8 -8 Z" fill="#fff"/>
     <path d="M76 98 H42 a8 8 0 0 1 -8 -8 V68 L58 58 h18 a8 8 0 0 1 8 8 v24 a8 8 0 0 1 -8 8 Z" fill="#fff" opacity=".92"/>
     <path d="M54 34 L68 42 L54 50 Z" fill="#ec4899"/><rect x="44" y="80" width="26" height="7" rx="3.5" fill="#ffd400"/>''',
}
for name, body in CONCEPTS.items():
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">{DEFS}{body}</svg>'
    (OUT / f"{name}.svg").write_text(svg)
    for px in (512, 32):
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(OUT / f"{name}-{px}.png"), output_width=px)
print(OUT)
