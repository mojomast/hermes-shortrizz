import json, re
from pathlib import Path
from .util import run, which, log

def is_url(s):
    return bool(re.match(r"^https?://", s))

def _youtube_id(url):
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None

def fetch(src, cache_dir):
    """Return (video_path, meta dict). Downloads URLs with yt-dlp (cached by video id)."""
    if not is_url(src):
        p = Path(src).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"file not found: {src}")
        return p, {"title": p.stem, "id": p.stem, "source": str(p)}
    ytdlp = which("yt-dlp")
    if not ytdlp:
        import importlib.util, sys
        if importlib.util.find_spec("yt_dlp") is None:
            raise SystemExit("yt-dlp not found (pip install yt-dlp, or put it on PATH)")
        ytdlp = [sys.executable, "-m", "yt_dlp"]
    ytdlp = ytdlp if isinstance(ytdlp, list) else [ytdlp]
    cache_dir = Path(cache_dir); cache_dir.mkdir(parents=True, exist_ok=True)
    vid = _youtube_id(src)
    if not (vid and (cache_dir / f"{vid}.mp4").exists()):
        vid = None
        for attempt in range(3):   # the id lookup sometimes fails transiently (rate limits)
            r = run([*ytdlp, "--print", "id", "--skip-download", "--no-warnings", src],
                    capture_output=True, text=True, check=False)
            lines = r.stdout.strip().splitlines()
            if r.returncode == 0 and lines:
                vid = lines[-1]; break
            log(f"yt-dlp id lookup failed (attempt {attempt + 1}/3), retrying")
            import time; time.sleep(5 * (attempt + 1))
        if not vid:
            raise SystemExit(f"yt-dlp could not read {src}")
    out = cache_dir / f"{vid}.mp4"
    info = cache_dir / f"{vid}.info.json"
    if not out.exists():
        log(f"downloading {src}")
        cmd = [*ytdlp, "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
               "--merge-output-format", "mp4", "--write-info-json", "--no-playlist",
               "-o", str(cache_dir / "%(id)s.%(ext)s"), src]
        if which("deno"):
            cmd[len(ytdlp):len(ytdlp)] = ["--js-runtimes", f"deno:{which('deno')}", "--remote-components", "ejs:github"]
        try:
            run(cmd)
        except Exception:
            log("retrying yt-dlp without JS runtime options")
            run([c for c in cmd if not c.startswith(("--js-runtimes", "deno:", "--remote-components", "ejs:"))])
    else:
        log(f"using cached download {out}")
    meta = {"id": vid, "source": src, "title": vid}
    if info.exists():
        d = json.loads(info.read_text())
        meta.update(title=d.get("title", vid), channel=d.get("channel"), license=d.get("license"),
                    webpage_url=d.get("webpage_url"))
    return out, meta
