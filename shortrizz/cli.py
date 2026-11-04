import argparse, json, sys
from pathlib import Path
from . import pipeline, publish, captions

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
        a = p.parse_args(argv[1:])
        doc = pipeline.restyle(a.run_dir, a.style, a.out, a.reframe)
        print(json.dumps(pipeline.summary(doc), indent=2))
        return
    if argv and argv[0] == "styles":
        print(json.dumps(captions.describe_styles(), indent=2)); return
    p = argparse.ArgumentParser(prog="shortrizz", description="Turn a long video into captioned vertical shorts.",
                                epilog="Other commands: shortrizz restyle RUN_DIR --style S | shortrizz publish DIR | shortrizz styles")
    p.add_argument("source", help="YouTube/any yt-dlp URL, or a local video file")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--min", type=float, default=30, help="min clip length (s)")
    p.add_argument("--max", type=float, default=60, help="max clip length (s)")
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
    a = p.parse_args(argv)
    doc = pipeline.make(a.source, a.out, a.count, a.min, a.max, a.style, a.cache, a.model, a.language, a.reframe,
                        not a.no_llm, a.fresh, a.preset, a.schedule.split(",") if a.schedule else None,
                        a.every_hours, a.keep_ass)
    if a.json:
        print(json.dumps(pipeline.summary(doc), indent=2))

if __name__ == "__main__":
    main()
