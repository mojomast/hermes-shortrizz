import argparse, json, sys
from pathlib import Path
from . import pipeline, publish, captions

def _wm_flags(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--watermark", dest="watermark", action="store_const", const=True, default=None,
                   help="burn in the Shortrizz watermark (default off; see SHORTRIZZ_WATERMARK / config)")
    g.add_argument("--no-watermark", dest="watermark", action="store_const", const=False)

def _edit_flags(p):
    g = p.add_argument_group("deeper edits (on by default: tighten, LLM edits, hook text, loudnorm)")
    g.add_argument("--edit", choices=["off", "light", "normal", "aggressive"], default=None,
                   help="dead-air tightening preset (default normal: gaps >0.35 s -> 0.15 s)")
    g.add_argument("--no-edits", dest="enabled", action="store_const", const=False, default=None,
                   help="plain render: one continuous range, no deeper edits at all")
    for flag, key, helptext in [("llm-edits", "llm_edits", "second LLM pass for false starts/repeats/fillers"),
                                ("hook-text", "hook_text", "on-screen hook text in the first 0-3 s"),
                                ("loudnorm", "loudnorm", "two-pass loudnorm to -14 LUFS / -1 dBTP")]:
        g.add_argument(f"--no-{flag}", dest=key, action="store_const", const=False, default=None, help=f"disable {helptext}")
    for flag, key, helptext in [("cold-open", "cold_open", "flash the best line first"),
                                ("tts-intro", "tts_intro", "Kokoro narrator context line before the clip"),
                                ("tts-outro", "tts_outro", "Kokoro narrator CTA at the end"),
                                ("zoom", "zoom", "punch-in zooms on emphasis words + alternating zoom on jump cuts"),
                                ("loop", "loop", "seamless loop ending"),
                                ("emoji", "emoji", "emoji pops on key words"),
                                ("sfx", "sfx", "pop/whoosh SFX (bundled CC0)")]:
        g.add_argument(f"--{flag}", dest=key, action="store_const", const=True, default=None, help=f"opt-in: {helptext}")
    g.add_argument("--all-fx", action="store_true", help="turn every opt-in effect on")

def _edits(a):
    keys = ["edit", "enabled", "llm_edits", "hook_text", "loudnorm", "cold_open", "tts_intro", "tts_outro", "zoom",
            "loop", "emoji", "sfx"]
    d = {k: getattr(a, k) for k in keys}
    d["explicit"] = [k for k in keys if getattr(a, k) is not None]  # flags the user typed (beat auto rules)
    if a.all_fx:
        for k in ["cold_open", "tts_intro", "tts_outro", "zoom", "loop", "emoji", "sfx"]:
            d[k] = True if d[k] is None else d[k]
    return d

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "publish":
        p = argparse.ArgumentParser(prog="shortrizz publish", description="Write a posting plan (dry run).")
        p.add_argument("dir"); p.add_argument("--platforms", default="youtube,tiktok,instagram")
        p.add_argument("--every-hours", type=float, default=24)
        p.add_argument("--live", action="store_true", help="call platform hooks (not implemented; disabled by default)")
        a = p.parse_args(argv[1:])
        for r in publish.plan(a.dir, a.platforms.split(","), every_hours=a.every_hours, live=a.live):
            print(f"{r['scheduled_for']}  {r['platform']:9s}  {r['status']:8s}  {r['file']}")
        print(f"plan written to {Path(a.dir) / 'publish_plan.json'}")
        return
    if argv and argv[0] == "restyle":
        p = argparse.ArgumentParser(prog="shortrizz restyle", description="Re-render a run with another style.")
        p.add_argument("run_dir"); p.add_argument("--style", required=True); p.add_argument("--out")
        p.add_argument("--reframe", choices=["auto", "face", "center", "letterbox"])
        _wm_flags(p); _edit_flags(p)
        a = p.parse_args(argv[1:])
        doc = pipeline.restyle(a.run_dir, a.style, a.out, a.reframe, watermark=a.watermark, edits=_edits(a))
        print(json.dumps(pipeline.summary(doc), indent=2))
        return
    if argv and argv[0] == "styles":
        print(json.dumps(captions.describe_styles(), indent=2)); return
    if argv and argv[0] == "highlights":
        _highlights(argv[1:]); return
    p = argparse.ArgumentParser(prog="shortrizz", description="Turn a long video into captioned vertical shorts.",
                                epilog="Other commands: shortrizz highlights SOURCE | shortrizz restyle RUN_DIR --style S | shortrizz publish DIR | shortrizz styles")
    p.add_argument("source", help="YouTube/any yt-dlp URL, or a local video file")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--min", type=float, default=30, help="min clip length (s)")
    p.add_argument("--max", type=float, default=60, help="max clip length (s)")
    p.add_argument("--exclude", action="append", default=[], metavar="RUN_DIR|clips.json",
                   help="skip moments already used by this earlier run (repeatable)")
    p.add_argument("--avoid-previous", action="store_true",
                   help="skip moments picked by earlier runs on the same video (from the cache)")
    p.add_argument("--length", choices=["default", "tight"], default="default",
                   help="tight = 20-35 s clips (overrides --min/--max)")
    p.add_argument("--style", default="hormozi", help="hormozi | mrbeast | abdaal | path/to/style.json")
    p.add_argument("--out", default="./shortrizz-out")
    p.add_argument("--cache", default=pipeline.DEFAULT_CACHE)
    p.add_argument("--model", default="small", help="faster-whisper model (tiny/base/small/medium/large-v3)")
    p.add_argument("--language", default=None)
    p.add_argument("--reframe", default="auto", choices=["auto", "face", "center", "letterbox"])
    p.add_argument("--no-llm", action="store_true", help="heuristic clip selection only")
    p.add_argument("--fresh", action="store_true", help="ignore cached LLM picks")
    p.add_argument("--preset", default="medium", help="x264 preset")
    p.add_argument("--schedule", default=None, help="also write dry-run posting plan, e.g. youtube,tiktok")
    p.add_argument("--every-hours", type=float, default=24)
    p.add_argument("--keep-ass", action="store_true")
    p.add_argument("--json", action="store_true", help="print a JSON summary on stdout when done")
    _wm_flags(p); _edit_flags(p)
    a = p.parse_args(argv)
    if a.length == "tight":
        a.min, a.max = 20.0, 35.0
    doc = pipeline.make(a.source, a.out, a.count, a.min, a.max, a.style, a.cache, a.model, a.language, a.reframe,
                        not a.no_llm, a.fresh, a.preset, a.schedule.split(",") if a.schedule else None,
                        a.every_hours, a.keep_ass, watermark=a.watermark,
                        edits=_edits(a), exclude=a.exclude, avoid_previous=a.avoid_previous)
    if a.json:
        print(json.dumps(pipeline.summary(doc), indent=2))

def _highlights(argv):
    p = argparse.ArgumentParser(
        prog="shortrizz highlights",
        description="Build one 16:9 highlights video from the highest-scoring moments of a source.")
    p.add_argument("source", help="YouTube/any yt-dlp URL, or a local video file")
    p.add_argument("--duration", type=float, default=180, help="target total length in seconds (0 = no duration cap)")
    p.add_argument("--max-clips", type=int, default=8, help="max segments (0 = no count cap)")
    p.add_argument("--min-score", type=float, default=50, help="drop segments scored below this (0-100)")
    p.add_argument("--min", type=float, default=15, help="min segment length (s)")
    p.add_argument("--max", type=float, default=45, help="max segment length (s)")
    p.add_argument("--order", choices=["chrono", "score"], default="chrono",
                   help="chrono keeps story order (default); score plays the strongest first")
    p.add_argument("--crossfade", type=float, default=0.4, help="audio and video crossfade in seconds (0 = hard cuts)")
    p.add_argument("--hard-cuts", action="store_true", help="butt-join segments with no crossfade")
    p.add_argument("--no-captions", action="store_true", help="leave captions off (default is on, lower third)")
    p.add_argument("--style", default="hormozi", help="hormozi | mrbeast | abdaal | path/to/style.json")
    p.add_argument("--out", default="./shortrizz-out")
    p.add_argument("--cache", default=pipeline.DEFAULT_CACHE)
    p.add_argument("--model", default="small", help="faster-whisper model (tiny/base/small/medium/large-v3)")
    p.add_argument("--language", default=None)
    p.add_argument("--no-llm", action="store_true", help="heuristic clip selection only")
    p.add_argument("--fresh", action="store_true", help="ignore cached LLM picks")
    p.add_argument("--preset", default="medium", help="x264 preset")
    p.add_argument("--max-width", type=int, default=1920, help="cap the 16:9 frame width; never upscale")
    p.add_argument("--no-loudnorm", action="store_true", help="skip per-segment loudness normalisation")
    p.add_argument("--no-chapters", action="store_true", help="do not write chapters.txt")
    p.add_argument("--no-manifest", action="store_true", help="do not write highlights.json")
    p.add_argument("--exclude", action="append", default=[], metavar="RUN_DIR|clips.json",
                   help="skip moments already used by this earlier run (repeatable)")
    p.add_argument("--avoid-previous", action="store_true",
                   help="skip moments picked by earlier runs on the same video (from the cache)")
    p.add_argument("--keep-ass", action="store_true")
    p.add_argument("--json", action="store_true", help="print a JSON summary on stdout when done")
    _wm_flags(p)
    a = p.parse_args(argv)
    if a.min > a.max:
        p.error("--min must be <= --max")
    if not 0 <= a.crossfade <= 2:
        p.error("--crossfade must be between 0 and 2")
    if not 0 <= a.min_score <= 100:
        p.error("--min-score must be between 0 and 100")
    doc = pipeline.highlights(
        a.source, a.out, target=a.duration, max_clips=a.max_clips, min_score=a.min_score,
        min_len=a.min, max_len=a.max, order=a.order, crossfade=0.0 if a.hard_cuts else a.crossfade,
        captions=not a.no_captions, style=a.style, cache=a.cache, model=a.model, language=a.language,
        use_llm=not a.no_llm, fresh=a.fresh, preset=a.preset, watermark=a.watermark,
        loudnorm=not a.no_loudnorm, chapters=not a.no_chapters, manifest=not a.no_manifest,
        max_width=a.max_width, exclude=a.exclude, avoid_previous=a.avoid_previous, keep_ass=a.keep_ass)
    if a.json:
        print(json.dumps(pipeline.summary(doc), indent=2))

if __name__ == "__main__":
    main()
