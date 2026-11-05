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

STYLE_NOTES = {
    "hormozi": "Bold uppercase Poppins Black, white with yellow active word, thick black outline, lower-middle.",
    "mrbeast": "Chunky Luckiest Guy, 2 words per line, green/yellow/red highlight cycle, heavy outline.",
    "abdaal": "Clean minimal Inter, sentence case, dimmed inactive words, blue glow on the active word.",
}

def describe_styles():
    return {"builtin": {k: {"description": STYLE_NOTES.get(k, ""), "font": v["font"], "max_words": v["max_words"]}
                        for k, v in STYLES.items()},
            "custom": "Pass a path to a JSON file with 'base' plus any keys to override: " + ", ".join(sorted(STYLES["hormozi"]))}

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

def for_landscape(style, width=1920, height=1080):
    """Retune a vertical caption style for a 16:9 frame: smaller type, lower third.

    Preset sizes are tuned for a 1920-tall short. Scale them to this frame and anchor
    the line in the lower third (pos_y 0.82) instead of the vertical safe-zone spot.
    """
    st = dict(style)
    scaled = int(round(st["size"] * (height / 1920) * 1.25))
    st["size"] = max(28, min(scaled, max(28, int(width * 0.08))))
    st["outline_w"] = max(2, int(round(st["outline_w"] * (height / 1920) * 1.25)))
    st["shadow"] = max(0, int(round(st.get("shadow", 0) * (height / 1920) * 1.25)))
    st["pos_y"] = 0.82
    return st

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

SAFE_BOTTOM_Y, SAFE_TOP = 1240, 288   # platform UI covers below ~1240 px and above 288 px (1080x1920)

def hook_size(txt, W=1080):
    """(font size, lines) for hook text: fit on at most two lines inside the safe width (768 px),
    Poppins Black ~0.62 em per caps glyph."""
    per_line = max(len(txt) / 2, max(len(x) for x in txt.split())) if len(txt) > 13 else len(txt)
    return int(max(72, min(132, (W - 120 - 192 - 40) / max(1, per_line * 0.58)))), (2 if len(txt) > 13 else 1)

def overlay_events(overlays, W=1080, H=1920):
    """Hook text (first 0-3 s, top safe zone, below the watermark) and an end CTA, as ASS events."""
    ev = []
    for o in overlays or []:
        txt = clean(o["text"], True)
        size, _ = hook_size(txt, W)
        y = int(H * 0.21) if o.get("pos", "top") == "top" else int(H * 0.50)
        y = o.get("y", max(y, SAFE_TOP + 110))  # face-aware hook position from the pipeline
        fade = "\\fad(120,180)"
        ev.append(f"Dialogue: 1,{ts(o['t0'])},{ts(o['t1'])},Hook,,120,192,0,,{{\\an8\\pos({(W + 120 - 192) // 2},{y}){fade}\\fs{size}}}{txt}")
    return ev

def merge_number_tokens(ws):
    """Whisper splits "$3,000" into "$3" + ",000"; glue such pieces back so captions read "$3,000"."""
    out = []
    for w in ws:
        if out and re.match(r"^[,.]\d", w["w"]) and re.search(r"\d$", out[-1]["w"]):
            out[-1] = {**out[-1], "w": out[-1]["w"] + w["w"], "e": w["e"]}
        else:
            out.append(w)
    return out

def build_ass(words, clip_start, clip_end, style, W=1080, H=1920, overlays=None, y_at=None):
    st = style
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Poppins Black,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,14,0,8,120,192,0,1
Style: Cap,{st['font']},{st['size']},{ass_color(st['primary'])},{ass_color(st['highlight'])},{ass_color(st['outline'])},{ass_color(st['back'], 0x60)},{-1 if st['bold'] else 0},0,0,0,100,100,{st['spacing']},0,1,{st['outline_w']},{st['shadow']},5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    x, y = W // 2, min(int(H * st["pos_y"]), int(SAFE_BOTTOM_Y - st["size"] * 0.6))  # stay above platform UI
    ev = []
    ws = merge_number_tokens([w for w in words if w["e"] > clip_start and w["s"] < clip_end])
    cyc = st.get("highlight_cycle") or [st["highlight"]]
    y_default = y
    # the end card / narrator CTA replaces the live captions while it is on screen (never stacked on them)
    cards = [(o["t0"], o["t1"]) for o in overlays or [] if o.get("pos") == "middle"]
    for pi, pg in enumerate(pages(ws, st["max_words"])):
        hl = cyc[pi % len(cyc)]
        if y_at is not None:  # face-aware: one position per caption page
            y = int(y_at(pg[0]["s"] - clip_start, pg[-1]["e"] - clip_start) or y_default)
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
            e = max(e, s + 0.05)
            for c0, c1 in cards:
                if s < c1 and e > c0:
                    e = c0 if s < c0 else s
            if e - s < 0.05:
                continue
            ev.append(f"Dialogue: 0,{ts(s)},{ts(e)},Cap,,0,0,0,,{{\\an5\\pos({x},{y}){fs}{intro}}}{txt}")
    return hdr + "\n".join(ev + overlay_events(overlays, W, H)) + "\n"
