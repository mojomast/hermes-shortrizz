import html, json
from pathlib import Path

LOGO = Path(__file__).resolve().parent / "assets" / "logo-dark.svg"
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
    logo = LOGO.read_text().split("?>")[-1].replace('width="', 'data-w="', 1) if LOGO.exists() else "<b>Shortrizz</b>"
    lic = str(meta.get("license") or "unknown")
    credit = html.escape(f"Source: {meta.get('title', '')} by {meta.get('channel') or 'unknown'} ({lic})")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Shortrizz · {title}</title>
<style>body{{font-family:Poppins,Inter,system-ui,sans-serif;background:#0b1020;color:#eee;margin:0;
background-image:radial-gradient(circle at 90% 0,rgba(236,72,153,.25),transparent 45%)}}
header{{display:flex;align-items:center;gap:18px;padding:22px 28px;border-bottom:1px solid #1f2540}}
header svg{{height:44px;width:auto}} main{{padding:20px 28px}} h1{{font-size:20px;margin:0 0 4px}}
.bar{{height:4px;background:linear-gradient(90deg,#8b5cf6,#ec4899,#f59e0b)}}
footer{{color:#778;font-size:12px;padding:20px 28px}} .grid{{display:flex;flex-wrap:wrap;gap:18px}}
.card{{background:#141a30;border:1px solid #232a48;border-radius:14px;width:300px;overflow:hidden}}
video{{width:300px;height:533px;background:#000;display:block}} .meta{{padding:12px;position:relative}}
h3{{margin:0 44px 6px 0;font-size:16px}} .dur{{color:#999;font-size:12px}} .why{{font-size:13px}}
.cap{{font-size:13px;color:#ccc}} .tags{{color:#6cf}}
.score{{position:absolute;right:12px;top:12px;border-radius:50%;width:36px;height:36px;line-height:36px;text-align:center;font-weight:700;background:#555}}
.s3,.s4{{background:linear-gradient(135deg,#8b5cf6,#ec4899)}} .s2{{background:#f59e0b;color:#000}}</style></head><body>
<header>{logo}<span style="color:#a5b4fc">Long video in. Viral-ready shorts out.</span></header><div class="bar"></div><main>
<h1>{title}</h1><p style="color:#999">{len(clips)} clips · source: {html.escape(str(meta.get('webpage_url') or meta.get('source','')))} · license: {html.escape(str(meta.get('license') or 'unknown'))}</p>
<div class="grid">{''.join(cards)}</div></main>
<footer>Made with Shortrizz · {credit} · Only post clips you have the rights to.</footer></body></html>"""
    (out_dir / "index.html").write_text(doc)
