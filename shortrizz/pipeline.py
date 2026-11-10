"""Library API used by the CLI and the Hermes tools."""
import json, re, time
from pathlib import Path
from .util import log, ffprobe_duration
from . import download, transcribe as T, clipselect, captions, render, gallery, publish, highlights as HL, picks

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

def _thumb_at(info, fallback):
    """Frame 0 is the thumbnail when the money shot is on. Otherwise the old mid-clip still."""
    if (info.get("money_shot") or {}).get("enabled"):
        return 0.0
    return fallback


def render_clips(src, words, clips, out, style_name, reframe_mode="auto", preset="medium", keep_ass=False,
                 progress=None, watermark=None, edit_opts=None, cache=None, tr_hash="", prev_edls=None,
                 money_shot=None):
    loaded = {}

    def look(name):
        if name not in loaded:
            loaded[name] = captions.load_style(name)
        return loaded[name]

    look(style_name)  # fail fast on a bad default style
    from . import money
    show_money = money.enabled(money_shot)
    results = []
    for i, c in enumerate(clips, 1):
        style_used = c.get("caption_style") or style_name
        style = look(style_used)
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
            band = None
            for o in ovl:
                if o.get("pos") == "top":
                    o["y"], band = edits.hook_y(track, o["t1"], text=o["text"])
            y_at = edits.caption_y_fn(track, base_y, style["size"], hook_until, band)
            if fx.get("emoji"):
                fx["emoji_places"] = edits.emoji_places(track, fx["emoji"], y_at, hook_band=band, hook_until=hook_until)
                edit_report["emoji"] = [e for (t0, e) in fx["emoji"] if fx["emoji_places"].get(t0)]
            moved = sum(1 for t, _ in track if y_at(t, t) != base_y)
            edit_report["caption_moves"] = moved
            ass.write_text(captions.build_ass(cw, 0.0, total, style, overlays=ovl, y_at=y_at, money_shot=show_money))
            info = render.render_edit(src, c["start"], c["end"], items, ass, mp4, fx, reframe_mode, preset=preset,
                                      watermark=watermark, loudnorm=edit_opts["loudnorm"], ctx=ctx,
                                      money_shot=show_money, clip=c)
            log(f"  edits: -{edit_report['removed_sec']}s, {edit_report['cuts']} cuts, final {edit_report['final_sec']}s")
            for f in out.glob(f"{name}.*.wav"):
                f.unlink()
        else:
            ass.write_text(captions.build_ass(words, c["start"], c["end"], style, money_shot=show_money))
            info = render.render_clip(src, c["start"], c["end"], ass, mp4, reframe_mode, preset=preset,
                                      watermark=watermark, money_shot=show_money, clip=c)
        mp4.replace(final_mp4); mp4 = final_mp4
        render.thumbnail(mp4, out / f"{name}.jpg", _thumb_at(info, min(1.5, (c["end"] - c["start"]) / 2)))
        if not keep_ass:
            ass.unlink()
        a_, b_ = c["word_range"]
        c = {**c, "hashtags": clipselect.clean_tags(c.get("hashtags"))}
        results.append({**c, "file": mp4.name, "thumb": f"{name}.jpg", "duration": edit_report["final_sec"] if edit_report else round(c["end"] - c["start"], 2),
                        "transcript": " ".join(w["w"] for w in words[a_:b_ + 1]), "style": style_used,
                        "reframe": info["reframe"], "watermark": info["watermark"],
                        "money_shot": info.get("money_shot"),
                        **({"edits": edit_report, "edl": f"{name}.edl.json"} if edit_report else {})})
    return results

def write_outputs(out, meta, doc, results, schedule=None, every_hours=24):
    (out / "clips.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    gallery.write_gallery(out, meta, results)
    if schedule:
        publish.plan(out, schedule, every_hours=every_hours)

def make(source, out, count=5, min_len=30, max_len=60, style="hormozi", cache=DEFAULT_CACHE, model="small",
         language=None, reframe="auto", use_llm=True, fresh=False, preset="medium", schedule=None,
         every_hours=24, keep_ass=False, watermark=None, edits=None, exclude=None, avoid_previous=False,
         select_only=False, money_shot=None):
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
    used_file = cache / f"{Path(src).stem}.used.json"
    ex = previous_ranges(exclude or [], used_file if avoid_previous else None)
    if ex:
        log(f"avoiding {len(ex)} range(s) used by earlier runs")
    clips = clipselect.select(tr, count, mn, mx, meta.get("title", ""), use_llm=use_llm,
                              cache_file=None if fresh else cache / f"{Path(src).stem}.picks.{count}-{mn:g}-{mx:g}.{_tr_hash(tr)}.json",
                              exclude=ex)
    _record_used(used_file, clips, out)
    timings["select"] = time.time() - t0 - sum(timings.values())
    log(f"selected {len(clips)} clip(s)")
    eo = {**_edit_opts(edits), "min_len": mn, "max_len": mx}
    meta["video_path"] = str(src)
    _write_run_picks(out, "shorts", src, meta, total, clips, words, {
        "count": count, "min": min_len, "max": max_len, "style": style, "model": model, "language": language,
        "llm": use_llm, "reframe": reframe, "cache": str(cache), "preset": preset, "keep_ass": keep_ass,
        "watermark": wm, "edits": eo,
    })
    if select_only:
        prog("picks_ready", len(clips), len(clips), out_dir=str(out))
        log(f"picks ready -> {out / 'picks.json'}")
        return _picks_ready_doc(out, meta, total, timings)
    results = render_clips(src, words, clips, out, style, reframe, preset, keep_ass, prog, wm, eo, cache, _tr_hash(tr),
                           money_shot=money_shot)
    timings["render"] = time.time() - t0 - sum(timings.values())
    doc = {"tool": "shortrizz", "out_dir": str(out), "source": meta, "source_duration": round(total, 1),
           "settings": {"count": count, "min": min_len, "max": max_len, "style": style, "model": model,
                        "llm": use_llm, "reframe": reframe, "cache": str(cache),
                        "watermark": wm["watermark"], "edits": eo},
           "timings_sec": {k: round(v, 1) for k, v in timings.items()}, "clips": results}
    write_outputs(out, meta, doc, results, schedule, every_hours)
    prog("done", len(results), len(results), out_dir=str(out))
    log(f"done in {time.time() - t0:.0f}s -> {out}/index.html")
    return doc

def previous_ranges(paths, used_file=None):
    """Source ranges from earlier runs: clips.json files or run dirs, plus the cache's used-ranges log."""
    out = []
    for p in paths:
        p = Path(p).expanduser()
        f = p / "clips.json" if p.is_dir() else p
        if f.exists():
            out += [(c["start"], c["end"]) for c in json.loads(f.read_text()).get("clips", [])]
    if used_file and Path(used_file).exists():
        out += [tuple(r[:2]) for r in json.loads(Path(used_file).read_text())]
    return sorted(set((round(a, 2), round(b, 2)) for a, b in out))

def _record_used(used_file, clips, out):
    """Append this run's picks to CACHE/<video>.used.json so --avoid-previous can skip them next time."""
    try:
        prev = json.loads(used_file.read_text()) if used_file.exists() else []
        prev = [r for r in prev if len(r) < 3 or r[2] != out.name]
        prev += [[c["start"], c["end"], out.name] for c in clips]
        used_file.write_text(json.dumps(prev))
    except (OSError, ValueError):
        pass

def _edit_opts(cli):
    from . import edits
    cli = dict(cli or {})
    enabled = cli.pop("enabled", None)
    o = edits.options(cli)
    o["enabled"] = enabled if enabled is not None else True
    return o

def restyle(run_dir, style, out=None, reframe=None, preset="medium", watermark=None, edits=None, money_shot=None):
    """Re-render the clips of an earlier run with another caption style (no download/transcribe/LLM)."""
    t0 = time.time()
    run_dir = Path(run_dir).expanduser().resolve()
    prev = json.loads((run_dir / "clips.json").read_text())
    if prev.get("format") == "highlights":
        raise SystemExit("restyle renders vertical shorts. This folder is a 16:9 highlights video; "
                         "re-run `shortrizz highlights` with another --style.")
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
                           edit_opts=eo, cache=cache, tr_hash=_tr_hash(tr), prev_edls=prev_edls,
                           money_shot=money_shot)
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

def _write_run_picks(out, kind, src, meta, total, clips, words, settings):
    doc = picks.build_document(
        run_id=Path(out).name, kind=kind, source=picks.source_block(meta, src, total),
        settings=settings, clips=clips, words=words)
    video = doc["source"].get("video_path")
    if video and Path(video).is_file():
        picks.write_stills(video, doc["picks"], out)
    picks.save_picks(out, doc, refresh_stills=False)
    try:
        picks.write_words(out, words)
    except OSError as e:
        log(f"words sidecar skipped ({e})")
    return doc


def _picks_ready_doc(out, meta, total, timings):
    saved = json.loads((Path(out) / "picks.json").read_text())
    return {"tool": "shortrizz", "stage": "picks_ready", "format": "picks", "kind": saved.get("kind"),
            "run_id": saved.get("run_id"), "out_dir": str(out), "picks_json": str(Path(out) / "picks.json"),
            "source": meta, "source_duration": round(float(total), 1), "settings": saved.get("settings"),
            "timings_sec": {k: round(v, 1) for k, v in timings.items()}, "picks": saved["picks"]}


def _merge_override(stored, override):
    out = dict(stored or {})
    for k, v in (override or {}).items():
        if k == "explicit" or v is None:
            continue
        out[k] = v
    return out


def _resume_meta(source):
    meta = dict(source.get("meta") or {})
    meta.setdefault("title", source.get("title") or "")
    meta.setdefault("source", source.get("url") or "")
    meta["video_path"] = source["video_path"]
    return meta


def _watermark_for(settings, override):
    from .watermark import settings as wm_settings
    if override is not None:
        return wm_settings(override)
    stored = (settings or {}).get("watermark")
    if isinstance(stored, dict) and "watermark" in stored:
        return stored
    return wm_settings(stored if isinstance(stored, bool) else None)


def render_from_picks(picks_path, out=None, preset=None, watermark=None, edits=None, keep_ass=None,
                      reframe=None, schedule=None, every_hours=24, expect_kind=None, money_shot=None):
    """Render enabled picks in order. Trim, title and caption style on each pick are honoured."""
    path = Path(picks_path).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"picks file not found: {path}")
    try:
        doc = picks.load_picks(path)
    except ValueError as e:
        raise SystemExit(str(e))
    if expect_kind and doc["kind"] != expect_kind:
        other = "shortrizz --picks" if doc["kind"] == "shorts" else "shortrizz highlights --picks"
        raise SystemExit(f"this picks file is a {doc['kind']} run. Render it with `{other}`.")
    run = path.parent
    picks.save_picks(run, doc, refresh_stills=True)
    doc = picks.load_picks(run / "picks.json")
    out = Path(out).expanduser().resolve() if out else run
    out.mkdir(parents=True, exist_ok=True)
    if doc["kind"] == "highlights":
        return _render_highlight_picks(doc, out, preset=preset, watermark=watermark, keep_ass=keep_ass,
                                       money_shot=money_shot)
    return _render_short_picks(doc, out, preset=preset, watermark=watermark, edits=edits, keep_ass=keep_ass,
                               reframe=reframe, schedule=schedule, every_hours=every_hours, money_shot=money_shot)


def _render_short_picks(doc, out, preset=None, watermark=None, edits=None, keep_ass=None,
                        reframe=None, schedule=None, every_hours=24, money_shot=None):
    t0 = time.time()
    settings = doc.get("settings") or {}
    source = doc["source"]
    src = Path(source["video_path"])
    if not src.is_file():
        raise SystemExit(f"source video not found: {src}")
    cache = Path(settings.get("cache") or DEFAULT_CACHE).expanduser()
    meta = _resume_meta(source)
    wm = _watermark_for(settings, watermark)
    eo = _merge_override(settings.get("edits"), edits)
    if "enabled" not in eo:
        eo = _edit_opts(edits)
    eo.setdefault("min_len", settings.get("min"))
    eo.setdefault("max_len", settings.get("max"))
    style = settings.get("style") or "hormozi"
    reframe = reframe or settings.get("reframe") or "auto"
    preset = preset or settings.get("preset") or "medium"
    if keep_ass is None:
        keep_ass = bool(settings.get("keep_ass"))
    prog = Progress(out)
    prog("transcribe")
    tr = T.transcribe(src, cache, settings.get("model") or "small", settings.get("language"))
    words = T.all_words(tr)
    if not words:
        raise SystemExit("no speech found in source")
    try:
        clips = picks.clips_for_render(doc, words)
    except ValueError as e:
        raise SystemExit(str(e))
    results = render_clips(src, words, clips, out, style, reframe, preset, keep_ass, prog, wm, eo, cache, _tr_hash(tr),
                           money_shot=money_shot)
    total = source.get("duration") or ffprobe_duration(src)
    finished = {"tool": "shortrizz", "out_dir": str(out), "source": meta, "source_duration": round(float(total), 1),
                "settings": {"count": settings.get("count"), "min": settings.get("min"), "max": settings.get("max"),
                             "style": style, "model": settings.get("model"), "llm": settings.get("llm"),
                             "reframe": reframe, "cache": str(cache), "watermark": wm["watermark"], "edits": eo},
                "timings_sec": {"render": round(time.time() - t0, 1)}, "clips": results}
    write_outputs(out, meta, finished, results, schedule, every_hours)
    prog("done", len(results), len(results), out_dir=str(out))
    log(f"rendered {len(results)} pick(s) -> {out}")
    return finished


def _render_highlight_picks(doc, out, preset=None, watermark=None, keep_ass=None, money_shot=None):
    t0 = time.time()
    settings = doc.get("settings") or {}
    source = doc["source"]
    src = Path(source["video_path"])
    if not src.is_file():
        raise SystemExit(f"source video not found: {src}")
    cache = Path(settings.get("cache") or DEFAULT_CACHE).expanduser()
    meta = _resume_meta(source)
    wm = _watermark_for(settings, watermark)
    captions_on = bool(settings.get("captions", True))
    style_name = settings.get("style") or "hormozi"
    from . import captions as caption_mod
    look = caption_mod.load_style(style_name) if captions_on else None
    preset = preset or settings.get("preset") or "medium"
    crf = int(settings.get("crf") or 20)
    if keep_ass is None:
        keep_ass = bool(settings.get("keep_ass"))
    crossfade = float(settings.get("crossfade") if settings.get("crossfade") is not None else 0.4)
    loudnorm = bool(settings.get("loudnorm", True))
    max_width = int(settings.get("max_width") or 1920)
    order = settings.get("order") if settings.get("order") in ("chrono", "score") else "chrono"
    chapters = bool(settings.get("chapters", True))
    manifest = bool(settings.get("manifest", True))
    prog = Progress(out)
    prog("transcribe")
    tr = T.transcribe(src, cache, settings.get("model") or "small", settings.get("language"))
    words = T.all_words(tr)
    if not words:
        raise SystemExit("no speech found in source")
    try:
        clips = picks.clips_for_render(doc, words)
    except ValueError as e:
        raise SystemExit(str(e))
    total = source.get("duration") or ffprobe_duration(src)
    dest = out / "highlights.mp4"
    info = HL.render_highlights(src, clips, dest, words=words, style=look, crossfade=crossfade,
                                loudnorm=loudnorm, preset=preset, crf=crf, max_width=max_width,
                                watermark=wm, keep_ass=keep_ass, progress=prog, money_shot=money_shot)
    try:
        render.thumbnail(dest, out / "highlights.jpg", _thumb_at(info, min(1.5, info["duration"] / 2)))
    except Exception as e:
        log(f"thumbnail skipped ({e})")
    results = []
    for seg, meta_seg in zip(clips, info["segments"]):
        results.append({**{k: seg.get(k) for k in ("start", "end", "title", "virality_score", "why_it_hooks",
                                                    "caption", "hashtags", "method", "word_range")},
                        "file": dest.name, "duration": meta_seg["duration"],
                        "out_start": meta_seg["out_start"], "out_end": meta_seg["out_end"],
                        "transcript": seg.get("transcript", ""),
                        "style": seg.get("caption_style"),
                        "money_shot": info.get("money_shot")})
    for seg in results:
        seg["hashtags"] = seg.get("hashtags") or []
    ch_name = "chapters.txt" if chapters else None
    man_name = "highlights.json" if manifest else None
    if ch_name:
        (out / ch_name).write_text(HL.chapters_text(results))
    manifest_body = {"file": dest.name, "duration": info["duration"], "width": info["width"],
                     "height": info["height"], "fps": info["fps"], "order": order,
                     "crossfade_sec": info["crossfade"], "audio_lufs": info["audio_lufs"],
                     "captions": captions_on, "style": style_name if captions_on else None,
                     "segments": [{"index": i, "title": s["title"], "virality_score": s["virality_score"],
                                   "why_it_hooks": s.get("why_it_hooks", ""),
                                   "source_start": s["start"], "source_end": s["end"],
                                   "out_start": s["out_start"], "out_end": s["out_end"],
                                   "duration": s["duration"]} for i, s in enumerate(results, 1)]}
    if man_name:
        (out / man_name).write_text(json.dumps(manifest_body, indent=2, ensure_ascii=False))
    finished = {"tool": "shortrizz", "format": "highlights", "out_dir": str(out), "file": dest.name,
                "duration": info["duration"], "width": info["width"], "height": info["height"], "fps": info["fps"],
                "order": order, "crossfade_sec": info["crossfade"], "audio_lufs": info["audio_lufs"],
                "chapters": ch_name, "manifest": man_name, "source": meta, "source_duration": round(float(total), 1),
                "settings": {"target": settings.get("target"), "max_clips": settings.get("max_clips"),
                             "min_score": settings.get("min_score"), "min": settings.get("min"),
                             "max": settings.get("max"), "order": order, "crossfade": crossfade,
                             "captions": captions_on, "style": style_name, "model": settings.get("model"),
                             "llm": settings.get("llm"), "loudnorm": loudnorm, "max_width": max_width,
                             "cache": str(cache), "watermark": wm["watermark"]},
                "timings_sec": {"render": round(time.time() - t0, 1)}, "clips": results}
    (out / "clips.json").write_text(json.dumps(finished, indent=2, ensure_ascii=False))
    gallery.write_highlights(out, meta, results, dest.name, info["duration"])
    prog("done", len(results), len(results), out_dir=str(out))
    log(f"rendered highlights from picks -> {dest}")
    return finished


def summary(doc, out_dir=None):
    """Compact result for agents."""
    out_dir = Path(out_dir or doc["out_dir"])
    if doc.get("stage") == "picks_ready" or doc.get("format") == "picks":
        saved = doc if doc.get("picks") else json.loads((out_dir / "picks.json").read_text())
        return {"stage": "picks_ready", "out_dir": str(out_dir), "picks_json": str(out_dir / "picks.json"),
                "kind": saved.get("kind") or doc.get("kind"), "run_id": saved.get("run_id") or doc.get("run_id"),
                "source_title": (doc.get("source") or {}).get("title") if isinstance(doc.get("source"), dict) else None,
                "picks": picks.summaries(saved, out_dir)}
    hl = doc.get("format") == "highlights"
    clips = []
    for c in doc.get("clips", []):
        rel = c.get("file") or doc.get("file")
        item = {"file": str(out_dir / rel) if rel else None, "title": c.get("title"),
                "virality_score": c.get("virality_score"), "duration": c.get("duration"),
                "why_it_hooks": c.get("why_it_hooks", ""),
                "caption": f"{c.get('caption', '')} {' '.join(c.get('hashtags') or [])}".strip()}
        if hl:
            item.update(source_start=c.get("start"), source_end=c.get("end"), out_start=c.get("out_start"))
        clips.append(item)
    result = {"out_dir": str(out_dir), "gallery": str(out_dir / "index.html"), "clips_json": str(out_dir / "clips.json"),
              "source_title": doc.get("source", {}).get("title"), "timings_sec": doc.get("timings_sec"), "clips": clips}
    if hl:
        result.update(format="highlights", file=str(out_dir / doc["file"]) if doc.get("file") else None,
                      duration=doc.get("duration"), width=doc.get("width"), height=doc.get("height"),
                      order=doc.get("order"),
                      chapters=str(out_dir / doc["chapters"]) if doc.get("chapters") else None,
                      manifest=str(out_dir / doc["manifest"]) if doc.get("manifest") else None)
    return result

def highlights(source, out, target=180, max_clips=8, min_score=50, min_len=15, max_len=45, order="chrono",
               crossfade=0.4, captions=True, style="hormozi", cache=DEFAULT_CACHE, model="small", language=None,
               use_llm=True, fresh=False, preset="medium", watermark=None, loudnorm=True, chapters=True,
               manifest=True, max_width=1920, exclude=None, avoid_previous=False, keep_ass=False, crf=20,
               select_only=False, money_shot=None):
    """One 16:9 highlights reel from the same scored moments the shorts pipeline uses."""
    if order not in ("chrono", "score"):
        raise SystemExit("order must be chrono or score")
    if crossfade < 0 or crossfade > 2:
        raise SystemExit("crossfade must be between 0 and 2 seconds")
    if min_len > max_len:
        raise SystemExit("min segment length must be <= max")
    source = str(source)
    t0 = time.time()
    out = Path(out).expanduser().resolve(); out.mkdir(parents=True, exist_ok=True)
    cache = Path(cache).expanduser(); cache.mkdir(parents=True, exist_ok=True)
    prog = Progress(out)
    # the `captions` argument shadows the captions module imported above
    from . import captions as caption_mod
    look = caption_mod.load_style(style) if captions else None
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
    used_file = cache / f"{Path(src).stem}.used.json"
    ex = previous_ranges(exclude or [], used_file if avoid_previous else None)
    if ex:
        log(f"avoiding {len(ex)} range(s) used by earlier runs")
    ask = max(12, int(max_clips or 8) * 3)
    clips = clipselect.select_highlights(
        tr, mn, mx, target=target, max_clips=max_clips, min_score=min_score, order=order,
        title=meta.get("title", ""), use_llm=use_llm,
        cache_file=None if fresh or not use_llm else cache / f"{Path(src).stem}.highlights.{ask}-{mn:g}-{mx:g}.{_tr_hash(tr)}.json",
        exclude=ex)
    if not clips:
        raise SystemExit("no highlight segments matched the duration, count and score filters. "
                         "Lower --min-score or --min, or raise --duration / --max-clips.")
    if len(clips) < 2:
        log("only one segment cleared the filters; the file will be a single stretch")
    _record_used(used_file, clips, out)
    timings["select"] = time.time() - t0 - sum(timings.values())
    meta["video_path"] = str(src)
    hl_settings = {"target": target, "max_clips": max_clips, "min_score": min_score, "min": min_len,
                   "max": max_len, "order": order, "crossfade": crossfade, "captions": bool(captions),
                   "style": style, "model": model, "language": language, "llm": use_llm, "loudnorm": bool(loudnorm),
                   "max_width": max_width, "cache": str(cache), "watermark": wm, "preset": preset, "crf": crf,
                   "keep_ass": keep_ass, "chapters": bool(chapters), "manifest": bool(manifest)}
    _write_run_picks(out, "highlights", src, meta, total, clips, words, hl_settings)
    if select_only:
        prog("picks_ready", len(clips), len(clips), out_dir=str(out))
        log(f"picks ready -> {out / 'picks.json'}")
        return _picks_ready_doc(out, meta, total, timings)
    dest = out / "highlights.mp4"
    info = HL.render_highlights(src, clips, dest, words=words, style=look, crossfade=crossfade,
                                loudnorm=loudnorm, preset=preset, crf=crf, max_width=max_width,
                                watermark=wm, keep_ass=keep_ass, progress=prog, money_shot=money_shot)
    timings["render"] = time.time() - t0 - sum(timings.values())
    try:
        render.thumbnail(dest, out / "highlights.jpg", _thumb_at(info, min(1.5, info["duration"] / 2)))
    except Exception as e:
        log(f"thumbnail skipped ({e})")
    results = []
    for seg, meta_seg in zip(clips, info["segments"]):
        results.append({**{k: seg.get(k) for k in ("start", "end", "title", "virality_score", "why_it_hooks",
                                                    "caption", "hashtags", "method", "word_range")},
                        "file": dest.name, "duration": meta_seg["duration"],
                        "money_shot": info.get("money_shot"),
                        "out_start": meta_seg["out_start"], "out_end": meta_seg["out_end"],
                        "transcript": seg.get("transcript", "")})
    for seg in results:
        seg["hashtags"] = seg.get("hashtags") or []
    ch_name = "chapters.txt" if chapters else None
    man_name = "highlights.json" if manifest else None
    if ch_name:
        (out / ch_name).write_text(HL.chapters_text(results))
    manifest_body = {"file": dest.name, "duration": info["duration"], "width": info["width"],
                     "height": info["height"], "fps": info["fps"], "order": order,
                     "crossfade_sec": info["crossfade"], "audio_lufs": info["audio_lufs"],
                     "captions": bool(captions), "style": style if captions else None,
                     "segments": [{"index": i, "title": s["title"], "virality_score": s["virality_score"],
                                   "why_it_hooks": s.get("why_it_hooks", ""),
                                   "source_start": s["start"], "source_end": s["end"],
                                   "out_start": s["out_start"], "out_end": s["out_end"],
                                   "duration": s["duration"]} for i, s in enumerate(results, 1)]}
    if man_name:
        (out / man_name).write_text(json.dumps(manifest_body, indent=2, ensure_ascii=False))
    meta["video_path"] = str(src)
    doc = {"tool": "shortrizz", "format": "highlights", "out_dir": str(out), "file": dest.name,
           "duration": info["duration"], "width": info["width"], "height": info["height"], "fps": info["fps"],
           "order": order, "crossfade_sec": info["crossfade"], "audio_lufs": info["audio_lufs"],
           "chapters": ch_name, "manifest": man_name, "source": meta, "source_duration": round(total, 1),
           "settings": {"target": target, "max_clips": max_clips, "min_score": min_score, "min": min_len,
                        "max": max_len, "order": order, "crossfade": crossfade, "captions": bool(captions),
                        "style": style, "model": model, "llm": use_llm, "loudnorm": bool(loudnorm),
                        "max_width": max_width, "cache": str(cache), "watermark": wm["watermark"]},
           "timings_sec": {k: round(v, 1) for k, v in timings.items()}, "clips": results}
    (out / "clips.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    gallery.write_highlights(out, meta, results, dest.name, info["duration"])
    prog("done", len(results), len(results), out_dir=str(out))
    log(f"done in {time.time() - t0:.0f}s -> {dest}")
    return doc
