import argparse, json, re, sys, time
from pathlib import Path
from .util import log, ffprobe_duration
from . import download, transcribe as T, clipselect, captions, render, gallery, publish

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "clip"

def cmd_make(a):
    t0 = time.time()
    out = Path(a.out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
    cache = Path(a.cache).expanduser(); cache.mkdir(parents=True, exist_ok=True)
    style = captions.load_style(a.style)
    src, meta = download.fetch(a.source, cache)
    timings = {"download": time.time() - t0}
    tr = T.transcribe(src, cache, a.model, a.language)
    timings["transcribe"] = time.time() - t0 - sum(timings.values())
    words = T.all_words(tr)
    if not words:
        raise SystemExit("no speech found in source")
    total = ffprobe_duration(src)
    mx = min(a.max, total)
    mn = min(a.min, mx)
    clips = clipselect.select(tr, a.count, mn, mx, meta.get("title", ""), use_llm=not a.no_llm,
                                cache_file=None if a.fresh else cache / f"{Path(src).stem}.picks.{a.count}-{mn:g}-{mx:g}.json")
    timings["select"] = time.time() - t0 - sum(timings.values())
    log(f"selected {len(clips)} clip(s)")
    results = []
    for i, c in enumerate(clips, 1):
        name = f"{i:02d}-{slug(c['title'])}"
        log(f"[{i}/{len(clips)}] {c['start']:.1f}-{c['end']:.1f}s  score {c['virality_score']}  {c['title']}")
        ass = out / f"{name}.ass"
        ass.write_text(captions.build_ass(words, c["start"], c["end"], style))
        mp4 = out / f"{name}.mp4"
        info = render.render_clip(src, c["start"], c["end"], ass, mp4, a.reframe, preset=a.preset)
        render.thumbnail(mp4, out / f"{name}.jpg", min(1.5, (c["end"] - c["start"]) / 2))
        if not a.keep_ass:
            ass.unlink()
        a_, b_ = c.pop("word_range")
        results.append({**c, "file": mp4.name, "thumb": f"{name}.jpg", "duration": round(c["end"] - c["start"], 2),
                        "transcript": " ".join(w["w"] for w in words[a_:b_ + 1]), "style": a.style,
                        "reframe": info["reframe"]})
    timings["render"] = time.time() - t0 - sum(timings.values())
    timings = {k: round(v, 1) for k, v in timings.items()}
    doc = {"source": meta, "source_duration": round(total, 1), "settings": {"count": a.count, "min": a.min, "max": a.max,
           "style": a.style, "model": a.model, "llm": not a.no_llm, "reframe": a.reframe},
           "timings_sec": timings, "clips": results}
    (out / "clips.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    gallery.write_gallery(out, meta, results)
    if a.schedule:
        publish.plan(out, a.schedule.split(","), every_hours=a.every_hours)
        log("wrote publish_plan.json (dry run)")
    log(f"done in {time.time() - t0:.0f}s -> {out}/index.html  {timings}")

def cmd_publish(a):
    reqs = publish.plan(a.dir, a.platforms.split(","), every_hours=a.every_hours, live=a.live)
    for r in reqs:
        print(f"{r['scheduled_for']}  {r['platform']:9s}  {r['status']:8s}  {r['file']}")
    print(f"plan written to {Path(a.dir) / 'publish_plan.json'}")

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "publish":
        p = argparse.ArgumentParser(prog="shortsmith publish", description="Write a posting plan (dry run).")
        p.add_argument("dir"); p.add_argument("--platforms", default="youtube,tiktok,instagram")
        p.add_argument("--every-hours", type=float, default=24)
        p.add_argument("--live", action="store_true", help="call platform hooks (not implemented; disabled by default)")
        return cmd_publish(p.parse_args(argv[1:]))
    p = argparse.ArgumentParser(prog="shortsmith", description="Turn a long video into captioned vertical shorts.")
    p.add_argument("source", help="YouTube/any yt-dlp URL, or a local video file")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--min", type=float, default=30, help="min clip length (s)")
    p.add_argument("--max", type=float, default=60, help="max clip length (s)")
    p.add_argument("--style", default="hormozi", help="hormozi | mrbeast | abdaal | path/to/style.json")
    p.add_argument("--out", default="./shortsmith-out")
    p.add_argument("--cache", default="~/.cache/shortsmith")
    p.add_argument("--model", default="small", help="faster-whisper model (tiny/base/small/medium/large-v3)")
    p.add_argument("--language", default=None)
    p.add_argument("--reframe", default="auto", choices=["auto", "face", "center", "letterbox"])
    p.add_argument("--no-llm", action="store_true", help="heuristic clip selection only")
    p.add_argument("--preset", default="medium", help="x264 preset")
    p.add_argument("--schedule", default=None, help="also write dry-run posting plan, e.g. youtube,tiktok")
    p.add_argument("--every-hours", type=float, default=24)
    p.add_argument("--fresh", action="store_true", help="ignore cached LLM picks")
    p.add_argument("--keep-ass", action="store_true")
    cmd_make(p.parse_args(argv))

if __name__ == "__main__":
    main()
