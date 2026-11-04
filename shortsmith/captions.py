"""Word-by-word karaoke captions as ASS subtitles.

Each caption "page" holds 2-4 words; one Dialogue event per word shows the whole page with the
currently spoken word highlighted (colour + optional pop scale), which is what Opus/Submagic do.
"""
import json, re
from pathlib import Path

STYLES = {
    # big bold uppercase, white, yellow active word, thick black outline, lower-middle
    "hormozi": dict(font="Poppins Black", size=132, bold=1, upper=True, primary="#FFFFFF", highlight="#FFE500",
                    outline="#000000", outline_w=9, shadow=4, back="#000000", pos_y=0.70, max_words=3,
                    pop=112, highlight_bg=False, spacing=0),
    # chunky comic font, multi-colour highlight, heavy outline, slightly higher
    "mrbeast": dict(font="Luckiest Guy", size=150, bold=0, upper=True, primary="#FFFFFF", highlight="#2EFF3A",
                    outline="#000000", outline_w=10, shadow=5, back="#000000", pos_y=0.66, max_words=2,
                    pop=120, highlight_bg=False, spacing=1,
                    highlight_cycle=["#2EFF3A", "#FFE500", "#FF3B3B"]),
    # clean minimal: sentence case, thin outline, soft box behind active word
    "abdaal": dict(font="Inter", size=100, bold=1, upper=False, primary="#FFFFFF", highlight="#FFFFFF",
                   outline="#000000", outline_w=2, shadow=0, back="#000000", pos_y=0.76, max_words=4,
                   pop=100, highlight_bg=True, highlight_bg_color="#3A7BFF", spacing=0, dim="#D8D8D8"),
}

def load_style(name_or_path):
    if name_or_path in STYLES:
        return dict(STYLES[name_or_path])
    p = Path(name_or_path)
    if not p.exists():
        raise SystemExit(f"unknown style '{name_or_path}' (built-in: {', '.join(STYLES)}; or a JSON file)")
    d = json.loads(p.read_text())
    base = dict(STYLES.get(d.pop("base", "hormozi"), STYLES["hormozi"]))
    base.update(d)
    return base

def ass_color(hexstr, alpha=0):
    h = hexstr.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()

def ts(t):
    t = max(0, t)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def clean(w, upper):
    w = w.strip()
    if upper:
        w = re.sub(r"[.,!?;:]+$", "", w).upper()  # Hormozi style drops trailing punctuation
    return w.replace("{", "(").replace("}", ")")

def pages(words, max_words):
    """Group words into 2..max_words chunks, breaking on punctuation and pauses."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = words[i + 1]["s"] - w["e"] if i + 1 < len(words) else 9
        if len(cur) >= max_words or (len(cur) >= 2 and (w["w"][-1:] in ".,?!;:" or gap > 0.45)) or gap > 1.0:
            out.append(cur); cur = []
    if cur:
        if out and len(cur) == 1 and len(out[-1]) < max_words + 1:
            out[-1].extend(cur)
        else:
            out.append(cur)
    return out

def build_ass(words, clip_start, clip_end, style, W=1080, H=1920):
    st = style
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{ass_color(st['primary'])},{ass_color(st['highlight'])},{ass_color(st['outline'])},{ass_color(st['back'], 0x60)},{-1 if st['bold'] else 0},0,0,0,100,100,{st['spacing']},0,1,{st['outline_w']},{st['shadow']},5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    x, y = W // 2, int(H * st["pos_y"])
    ev = []
    ws = [w for w in words if w["e"] > clip_start and w["s"] < clip_end]
    cyc = st.get("highlight_cycle") or [st["highlight"]]
    for pi, pg in enumerate(pages(ws, st["max_words"])):
        hl = cyc[pi % len(cyc)]
        nxt_page_start = None
        for k, w in enumerate(pg):
            s = w["s"] - clip_start
            e = (pg[k + 1]["s"] if k + 1 < len(pg) else pg[-1]["e"] + 0.15) - clip_start
            if k + 1 == len(pg):
                # hold the last word until next page starts (max 0.6 s) to avoid flicker
                after = [x2 for x2 in ws if x2["s"] > w["s"]]
                if after:
                    e = min(after[0]["s"] - clip_start, w["e"] - clip_start + 0.6)
            parts = []
            for j, ww in enumerate(pg):
                t = clean(ww["w"], st["upper"])
                if j == k:
                    tag = f"{{\\c{ass_color(hl)}\\fscx{st['pop']}\\fscy{st['pop']}"
                    if st.get("highlight_bg"):
                        tag += f"\\3c{ass_color(st['highlight_bg_color'])}\\bord{max(6, st['outline_w']*4)}"
                    reset = f"{{\\c{ass_color(st['primary'])}\\fscx100\\fscy100\\3c{ass_color(st['outline'])}\\bord{st['outline_w']}}}"
                    parts.append(tag + "}" + t + reset)
                else:
                    dim = st.get("dim")
                    parts.append((f"{{\\c{ass_color(dim)}}}" if dim else "") + t + (f"{{\\c{ass_color(st['primary'])}}}" if dim else ""))
            txt = " ".join(parts)
            # auto-shrink long lines so they fit the frame width (approx glyph width ~0.42*size)
            chars = sum(len(clean(ww["w"], st["upper"])) + 1 for ww in pg)
            fit = min(1.0, (W - 140) / max(1, chars * st.get("char_w", 0.42) * st["size"] * st["pop"] / 100))
            fs = f"\\fs{int(st['size'] * fit)}" if fit < 1 else ""
            intro = "\\fad(40,0)" if k == 0 else ""
            ev.append(f"Dialogue: 0,{ts(s)},{ts(max(e, s + 0.05))},Cap,,0,0,0,,{{\\an5\\pos({x},{y}){fs}{intro}}}{txt}")
    return hdr + "\n".join(ev) + "\n"
