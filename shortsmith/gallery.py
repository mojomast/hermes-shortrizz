import html, json
from .util import fmt_dur

def write_gallery(out_dir, meta, clips):
    cards = []
    for c in clips:
        e = html.escape
        cards.append(f"""<div class="card">
<video src="{e(c['file'])}" poster="{e(c.get('thumb',''))}" controls preload="none" playsinline></video>
<div class="meta"><span class="score s{c['virality_score']//25}">{c['virality_score']}</span>
<h3>{e(c['title'])}</h3><div class="dur">{fmt_dur(c['duration'])} · {e(fmt_dur(c['start']))}–{e(fmt_dur(c['end']))} in source · {e(c['method'])}</div>
<p class="why"><b>Why it hooks:</b> {e(c['why_it_hooks'])}</p>
<p class="cap">{e(c['caption'])}<br><span class="tags">{e(' '.join(c['hashtags']))}</span></p></div></div>""")
    title = html.escape(meta.get("title", "clips"))
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>shortsmith · {title}</title>
<style>body{{font-family:system-ui,sans-serif;background:#0e0e12;color:#eee;margin:24px}}
h1{{font-size:20px}} .grid{{display:flex;flex-wrap:wrap;gap:18px}}
.card{{background:#1a1a22;border-radius:14px;width:300px;overflow:hidden}}
video{{width:300px;height:533px;background:#000;display:block}} .meta{{padding:12px;position:relative}}
h3{{margin:0 44px 6px 0;font-size:16px}} .dur{{color:#999;font-size:12px}} .why{{font-size:13px}}
.cap{{font-size:13px;color:#ccc}} .tags{{color:#6cf}}
.score{{position:absolute;right:12px;top:12px;border-radius:50%;width:36px;height:36px;line-height:36px;text-align:center;font-weight:700;background:#555}}
.s3,.s4{{background:#1db954}} .s2{{background:#e0a800;color:#000}}</style></head><body>
<h1>{title}</h1><p style="color:#999">{len(clips)} clips · source: {html.escape(str(meta.get('webpage_url') or meta.get('source','')))} · license: {html.escape(str(meta.get('license') or 'unknown'))}</p>
<div class="grid">{''.join(cards)}</div></body></html>"""
    (out_dir / "index.html").write_text(doc)
