"""Library API used by the CLI and the Hermes tools."""
import json, re, time
from pathlib import Path
from .util import log, ffprobe_duration
from . import download, transcribe as T, clipselect, captions, render, gallery, publish

DEFAULT_CACHE = "~/.cache/shortrizz"

def _tr_hash(tr):
    import hashlib
    return hashlib.sha1(" ".join(s["text"] for s in tr["segments"]).encode()).hexdigest()[:8]

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "clip"

class Progress:
    """Writes DIR/progress.json so a background job can be polled."""
    def __init__(self, out):
        self.path = Path(out) / "progress.json"; self.t0 = time.time()
    def __call__(self, stage, done=0, total=0, **extra):
        log(f"{stage} {done}/{total}" if total else stage)
        self.path.write_text(json.dumps({"stage": stage, "done": done, "total": total,
                                         "elapsed_sec": round(time.time() - self.t0, 1), **extra}))

def render_clips(src, words, clips, out, style_name, reframe_mode="auto", preset="medium", keep_ass=False,
                 progress=None, watermark=None, edit_opts=None, cache=None, tr_hash="", prev_edls=None):
    style = captions.load_style(style_name)
    results = []
    for i, c in enumerate(clips, 1):
        if progress:
            progress("render", i - 1, len(clips))
        name = f"{i:02d}-{slug(c['title'])}"
        log(f"[{i}/{len(clips)}] {c['start']:.1f}-{c['end']:.1f}s  score {c['virality_score']}  {c['title']}")
        ass = out / f"{name}.ass"
        final_mp4 = out / f"{name}.mp4"
        mp4 = out / f"{name}.partial.mp4"   # renamed only once the encode finished (never a half file at the final path)
        edit_report = None
        if edit_opts and edit_opts.get("enabled", True):
            from . import edits
            edl = edits.make_edl(src, words, c, edit_opts, cache, tr_hash, (prev_edls or {}).get(i))
            (out / f"{name}.edl.json").write_text(json.dumps(edl, indent=1, ensure_ascii=False))
            items, cw, ovl, fx, edit_report, total = edits.assemble(src, words, c, edl, edit_opts, style, out, name)
            ctx = render.prepare(src, c["start"], c["end"], reframe_mode)
            render.pick_holds(ctx, items)
            track = render.face_track(ctx, items, fx)
            base_y = min(int(1920 * style["pos_y"]), int(captions.SAFE_BOTTOM_Y - style["size"] * 0.6))
            hook_until = max((o["t1"] for o in ovl if o.get("pos") == "top"), default=0.0)
            y_at = edits.caption_y_fn(track, base_y, style["size"], hook_until)
            if fx.get("emoji"):
                fx["emoji_places"] = edits.emoji_places(track, fx["emoji"], y_at)
                edit_report["emoji"] = [e for (t0, e) in fx["emoji"] if fx["emoji_places"].get(t0)]
            moved = sum(1 for t, _ in track if y_at(t, t) != base_y)
            edit_report["caption_moves"] = moved
            ass.write_text(captions.build_ass(cw, 0.0, total, style, overlays=ovl, y_at=y_at))
            info = render.render_edit(src, c["start"], c["end"], items, ass, mp4, fx, reframe_mode, preset=preset,
                                      watermark=watermark, loudnorm=edit_opts["loudnorm"], ctx=ctx)
            log(f"  edits: -{edit_report['removed_sec']}s, {edit_report['cuts']} cuts, final {edit_report['final_sec']}s")
            for f in out.glob(f"{name}.*.wav"):
                f.unlink()
        else:
            ass.write_text(captions.build_ass(words, c["start"], c["end"], style))
            info = render.render_clip(src, c["start"], c["end"], ass, mp4, reframe_mode, preset=preset,
                                      watermark=watermark)
        mp4.replace(final_mp4); mp4 = final_mp4
        render.thumbnail(mp4, out / f"{name}.jpg", min(1.5, (c["end"] - c["start"]) / 2))
        if not keep_ass:
            ass.unlink()
        a_, b_ = c["word_range"]
        c = {**c, "hashtags": clipselect.clean_tags(c.get("hashtags"))}
        results.append({**c, "file": mp4.name, "thumb": f"{name}.jpg", "duration": edit_report["final_sec"] if edit_report else round(c["end"] - c["start"], 2),
                        "transcript": " ".join(w["w"] for w in words[a_:b_ + 1]), "style": style_name,
                        "reframe": info["reframe"], "watermark": info["watermark"],
                        **({"edits": edit_report, "edl": f"{name}.edl.json"} if edit_report else {})})
    return results

def write_outputs(out, meta, doc, results, schedule=None, every_hours=24):
    (out / "clips.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    gallery.write_gallery(out, meta, results)
    if schedule:
        publish.plan(out, schedule, every_hours=every_hours)

def make(source, out, count=5, min_len=30, max_len=60, style="hormozi", cache=DEFAULT_CACHE, model="small",
         language=None, reframe="auto", use_llm=True, fresh=False, preset="medium", schedule=None,
         every_hours=24, keep_ass=False, watermark=None, edits=None):
    t0 = time.time()
    out = Path(out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
    cache = Path(cache).expanduser(); cache.mkdir(parents=True, exist_ok=True)
    prog = Progress(out)
    captions.load_style(style)  # fail fast on a bad style
    from .watermark import settings as wm_settings
    wm = wm_settings(watermark)
    prog("download")
    src, meta = download.fetch(source, cache)
    timings = {"download": time.time() - t0}
    prog("transcribe")
    tr = T.transcribe(src, cache, model, language)
    timings["transcribe"] = time.time() - t0 - sum(timings.values())
    words = T.all_words(tr)
    if not words:
        raise SystemExit("no speech found in source")
    total = ffprobe_duration(src)
    mx = min(max_len, total); mn = min(min_len, mx)
    prog("select")
    clips = clipselect.select(tr, count, mn, mx, meta.get("title", ""), use_llm=use_llm,
                              cache_file=None if fresh else cache / f"{Path(src).stem}.picks.{count}-{mn:g}-{mx:g}.{_tr_hash(tr)}.json")
    timings["select"] = time.time() - t0 - sum(timings.values())
    log(f"selected {len(clips)} clip(s)")
    eo = {**_edit_opts(edits), "min_len": mn, "max_len": mx}
    results = render_clips(src, words, clips, out, style, reframe, preset, keep_ass, prog, wm, eo, cache, _tr_hash(tr))
    timings["render"] = time.time() - t0 - sum(timings.values())
    meta["video_path"] = str(src)
    doc = {"tool": "shortrizz", "out_dir": str(out), "source": meta, "source_duration": round(total, 1),
           "settings": {"count": count, "min": min_len, "max": max_len, "style": style, "model": model,
                        "llm": use_llm, "reframe": reframe, "cache": str(cache),
                        "watermark": wm["watermark"], "edits": eo},
           "timings_sec": {k: round(v, 1) for k, v in timings.items()}, "clips": results}
    write_outputs(out, meta, doc, results, schedule, every_hours)
    prog("done", len(results), len(results), out_dir=str(out))
    log(f"done in {time.time() - t0:.0f}s -> {out}/index.html")
    return doc

def _edit_opts(cli):
    from . import edits
    cli = dict(cli or {})
    enabled = cli.pop("enabled", None)
    o = edits.options(cli)
    o["enabled"] = enabled if enabled is not None else True
    return o

def restyle(run_dir, style, out=None, reframe=None, preset="medium", watermark=None, edits=None):
    """Re-render the clips of an earlier run with another caption style (no download/transcribe/LLM)."""
    t0 = time.time()
    run_dir = Path(run_dir).expanduser().resolve()
    prev = json.loads((run_dir / "clips.json").read_text())
    out = Path(out).expanduser().resolve() if out else run_dir.parent / f"{run_dir.name}-{Path(style).stem}"
    out.mkdir(parents=True, exist_ok=True)
    src = Path(prev["source"]["video_path"])
    cache = Path(prev["settings"]["cache"])
    tr = T.transcribe(src, cache, prev["settings"]["model"])
    words = T.all_words(tr)
    reframe = reframe or prev["settings"]["reframe"]
    clips = [{k: c[k] for k in ("start", "end", "title", "virality_score", "why_it_hooks", "caption", "hashtags",
                                "method")} for c in prev["clips"]]
    for c, p in zip(clips, prev["clips"]):
        c["word_range"] = _word_range(words, p["start"], p["end"])
    prog = Progress(out)
    from .watermark import settings as wm_settings
    wm = wm_settings(watermark)
    eo = {**_edit_opts(edits), "min_len": prev["settings"].get("min"), "max_len": prev["settings"].get("max")}
    prev_edls = {}
    for i, p in enumerate(prev["clips"], 1):  # reuse the saved EDLs -> no LLM call on restyle
        f = run_dir / p.get("edl", "-")
        if p.get("edl") and f.exists():
            prev_edls[i] = json.loads(f.read_text())
    results = render_clips(src, words, clips, out, style, reframe, preset, progress=prog, watermark=wm,
                           edit_opts=eo, cache=cache, tr_hash=_tr_hash(tr), prev_edls=prev_edls)
    doc = {**prev, "settings": {**prev["settings"], "style": style, "reframe": reframe,
                                                 "watermark": wm["watermark"], "edits": eo},
           "timings_sec": {"render": round(time.time() - t0, 1)}, "clips": results, "restyled_from": str(run_dir),
           "out_dir": str(out)}
    write_outputs(out, prev["source"], doc, results)
    prog("done", len(results), len(results), out_dir=str(out))
    return doc

def _word_range(words, s, e):
    idx = [i for i, w in enumerate(words) if w["s"] >= s - 0.01 and w["e"] <= e + 0.01]
    return [idx[0], idx[-1]] if idx else [0, 0]

def summary(doc, out_dir=None):
    """Compact result for agents."""
    out_dir = Path(out_dir or doc["out_dir"])
    return {"out_dir": str(out_dir), "gallery": str(out_dir / "index.html"), "clips_json": str(out_dir / "clips.json"),
            "source_title": doc["source"].get("title"), "timings_sec": doc.get("timings_sec"),
            "clips": [{"file": str(out_dir / c["file"]), "title": c["title"], "virality_score": c["virality_score"],
                       "duration": c["duration"], "why_it_hooks": c["why_it_hooks"],
                       "caption": f"{c['caption']} {' '.join(c['hashtags'])}".strip()} for c in doc["clips"]]}
